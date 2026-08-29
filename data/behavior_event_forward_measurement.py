"""Measure, without trading, market behavior around exact-time external events."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from database.market_behavior_store import EXTERNAL_EVIDENCE_TABLE, TABLE


VERSION = "BEHAVIOR_EVENT_FORWARD_MEASUREMENT_V2"
FIXED_HORIZON_MINUTES = (1, 5, 15, 60, 240, 1440)


def _table_exists(con, table):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _base_symbol(instrument):
    return str(instrument).split("-")[0].upper()


def _applies(symbols_json, instrument):
    symbols = [str(value).upper() for value in json.loads(symbols_json or "[]")]
    return not symbols or _base_symbol(instrument) in symbols


def _target_minute_end(event_minute_end, horizon_minutes):
    return int(event_minute_end) + (int(horizon_minutes) - 1) * 60_000


def reaction_paths(con, matches):
    paths = []
    for row in matches:
        (event_id, kind, _, instrument, minute_end, event_price,
         book_imbalance, taker_imbalance, spread_bps, oi_change, funding_rate,
         long_liquidation, short_liquidation, liquidation_count) = row
        baseline = con.execute(f"""
            SELECT mid_price_last FROM {TABLE}
            WHERE instrument=? AND minute_end_timestamp_ms=?
              AND eligible_for_learning=1 AND mid_price_last>0
        """, (instrument, minute_end - 60_000)).fetchone()
        if baseline is None:
            continue
        baseline_price = float(baseline[0])
        horizons = {}
        for horizon in FIXED_HORIZON_MINUTES:
            future = con.execute(f"""
                SELECT mid_price_last FROM {TABLE}
                WHERE instrument=? AND minute_end_timestamp_ms=?
                  AND eligible_for_learning=1 AND mid_price_last>0
            """, (instrument, _target_minute_end(minute_end, horizon))).fetchone()
            if future is not None:
                price = float(future[0])
                horizons[str(horizon)] = {
                    "mid_price": price,
                    "return_from_pre_event_pct": (price / baseline_price - 1.0) * 100.0,
                }
        paths.append({
            "event_id": event_id, "kind": kind, "instrument": instrument,
            "event_minute_end_timestamp_ms": int(minute_end),
            "pre_event_mid_price": baseline_price,
            "event_minute_mid_price": float(event_price), "horizons": horizons,
            "event_minute_behavior": {
                "book_imbalance_20_mean": float(book_imbalance),
                "taker_imbalance_snapshot_mean": float(taker_imbalance),
                "spread_bps_mean": float(spread_bps),
                "open_interest_usd_change": float(oi_change),
                "funding_rate_last": float(funding_rate),
                "long_liquidation_notional_usd_estimate": float(long_liquidation),
                "short_liquidation_notional_usd_estimate": float(short_liquidation),
                "liquidation_event_count": int(liquidation_count),
            },
        })
    return paths


def summarize_paths(paths):
    grouped = {}
    for path in paths:
        for horizon, values in path["horizons"].items():
            key = (path["kind"], horizon)
            grouped.setdefault(key, []).append(values["return_from_pre_event_pct"])
    return {
        f"{kind}:{horizon}": {
            "row_count": len(values),
            "mean_return_from_pre_event_pct": sum(values) / len(values),
        }
        for (kind, horizon), values in sorted(grouped.items())
    }


def summarize_behavior(paths):
    fields = (
        "book_imbalance_20_mean", "taker_imbalance_snapshot_mean",
        "spread_bps_mean", "open_interest_usd_change", "funding_rate_last",
        "long_liquidation_notional_usd_estimate",
        "short_liquidation_notional_usd_estimate", "liquidation_event_count",
    )
    kinds = sorted({path["kind"] for path in paths})
    return {
        kind: {
            "row_count": len(rows),
            **{
                f"mean_{field}": sum(
                    row["event_minute_behavior"][field] for row in rows
                ) / len(rows)
                for field in fields
            },
        }
        for kind in kinds
        for rows in [[path for path in paths if path["kind"] == kind]]
    }


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
        "reaction_summary": {},
        "event_minute_behavior_summary": {},
        "statistical_warning": (
            "Instrument rows sharing one event/minute are correlated and are not "
            "independent evidence."
        ),
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
               b.minute_end_timestamp_ms,b.mid_price_last,
               b.book_imbalance_20_mean,b.taker_imbalance_snapshot_mean,
               b.spread_bps_mean,b.open_interest_usd_change,b.funding_rate_last,
               b.long_liquidation_notional_usd_estimate,
               b.short_liquidation_notional_usd_estimate,b.liquidation_event_count
        FROM {EXTERNAL_EVIDENCE_TABLE} e
        JOIN {TABLE} b USING(minute_end_timestamp_ms)
        WHERE b.eligible_for_learning=1 AND b.mid_price_last>0
    """).fetchall()
    matches = [row for row in matches if _applies(row[2], row[3])]
    result["exact_minute_join_rows"] = len(matches)
    result["exact_minute_join_events"] = len({row[0] for row in matches})
    paths = reaction_paths(con, matches)
    for horizon in FIXED_HORIZON_MINUTES:
        result["forward_outcomes_by_horizon"][str(horizon)] = sum(
            str(horizon) in path["horizons"] for path in paths
        )
    result["reaction_summary"] = summarize_paths(paths)
    result["event_minute_behavior_summary"] = summarize_behavior(paths)
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
