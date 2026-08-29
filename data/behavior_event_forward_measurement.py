"""Measure, without trading, market behavior around exact-time external events."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from database.market_behavior_store import EXTERNAL_EVIDENCE_TABLE, TABLE


VERSION = "BEHAVIOR_EVENT_FORWARD_MEASUREMENT_V1"
FIXED_HORIZON_MINUTES = (60, 240, 1440)


def _table_exists(con, table):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _base_symbol(instrument):
    return str(instrument).split("-")[0].upper()


def _applies(symbols_json, instrument):
    symbols = [str(value).upper() for value in json.loads(symbols_json or "[]")]
    return not symbols or _base_symbol(instrument) in symbols


def audit(con):
    behavior_exists = _table_exists(con, TABLE)
    evidence_exists = _table_exists(con, EXTERNAL_EVIDENCE_TABLE)
    result = {
        "contract_version": VERSION,
        "behavior_table_exists": behavior_exists,
        "evidence_table_exists": evidence_exists,
        "behavior_rows": 0, "eligible_behavior_rows": 0,
        "evidence_rows": 0, "evidence_by_kind": {},
        "exact_minute_join_rows": 0, "exact_minute_join_events": 0,
        "forward_outcomes_by_horizon": {
            str(value): 0 for value in FIXED_HORIZON_MINUTES
        },
        "measurement_ready": False,
        "blocking_reasons": [],
        "model_or_threshold_search_opened": False,
    }
    if behavior_exists:
        result["behavior_rows"], result["eligible_behavior_rows"] = con.execute(
            f"SELECT COUNT(*),COALESCE(SUM(eligible_for_learning),0) FROM {TABLE}"
        ).fetchone()
    else:
        result["blocking_reasons"].append("BEHAVIOR_TABLE_MISSING")
    if evidence_exists:
        rows = con.execute(
            f"SELECT kind,COUNT(*) FROM {EXTERNAL_EVIDENCE_TABLE} GROUP BY kind"
        ).fetchall()
        result["evidence_by_kind"] = dict(rows)
        result["evidence_rows"] = sum(value for _, value in rows)
    else:
        result["blocking_reasons"].append("EXTERNAL_EVIDENCE_TABLE_MISSING")
    if not behavior_exists or not evidence_exists:
        return result

    matches = con.execute(f"""
        SELECT e.event_id,e.kind,e.symbols_json,b.instrument,
               b.minute_end_timestamp_ms,b.mid_price_last
        FROM {EXTERNAL_EVIDENCE_TABLE} e
        JOIN {TABLE} b USING(minute_end_timestamp_ms)
        WHERE b.eligible_for_learning=1 AND b.mid_price_last>0
    """).fetchall()
    matches = [row for row in matches if _applies(row[2], row[3])]
    result["exact_minute_join_rows"] = len(matches)
    result["exact_minute_join_events"] = len({row[0] for row in matches})
    for horizon in FIXED_HORIZON_MINUTES:
        count = 0
        for _, _, _, instrument, minute_end, _ in matches:
            future = con.execute(f"""
                SELECT mid_price_last FROM {TABLE}
                WHERE instrument=? AND minute_end_timestamp_ms=?
                  AND eligible_for_learning=1 AND mid_price_last>0
            """, (instrument, minute_end + horizon * 60_000)).fetchone()
            count += int(future is not None)
        result["forward_outcomes_by_horizon"][str(horizon)] = count
    if not matches:
        result["blocking_reasons"].append("NO_NATURAL_EXACT_MINUTE_MATCHES")
    elif not any(result["forward_outcomes_by_horizon"].values()):
        result["blocking_reasons"].append("NO_CLOSED_FORWARD_HORIZONS")
    else:
        result["measurement_ready"] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    uri = f"file:{Path(args.database).resolve()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as con:
        result = audit(con)
    result["created_at"] = datetime.now(timezone.utc).isoformat()
    result["database_mutated"] = False
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return result


if __name__ == "__main__":
    main()
