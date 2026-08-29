"""Local-only bounded SQLite storage for compact live behavior features."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from database.runtime_store import connect


TABLE = "market_behavior_minute_features"
EXTERNAL_EVIDENCE_TABLE = "causal_minute_external_evidence"
DEFAULT_RETENTION_DAYS = 30


def ensure_table(connection):
    connection.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            instrument TEXT NOT NULL,
            minute_end_timestamp_ms INTEGER NOT NULL,
            minute_start_timestamp_ms INTEGER NOT NULL,
            sample_count INTEGER NOT NULL,
            max_sample_gap_ms INTEGER NOT NULL,
            book_imbalance_20_mean REAL NOT NULL,
            book_imbalance_20_last REAL NOT NULL,
            taker_imbalance_snapshot_mean REAL NOT NULL,
            taker_imbalance_snapshot_last REAL NOT NULL,
            spread_bps_mean REAL NOT NULL,
            open_interest_usd_first REAL NOT NULL,
            open_interest_usd_last REAL NOT NULL,
            open_interest_usd_change REAL NOT NULL,
            funding_rate_last REAL NOT NULL,
            long_liquidation_notional_usd_estimate REAL NOT NULL,
            short_liquidation_notional_usd_estimate REAL NOT NULL,
            liquidation_event_count INTEGER NOT NULL,
            quality_gates_json TEXT NOT NULL,
            eligible_for_learning INTEGER NOT NULL CHECK(eligible_for_learning IN (0,1)),
            created_at TEXT NOT NULL,
            PRIMARY KEY(instrument, minute_end_timestamp_ms)
        )
    """)
    connection.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_market_behavior_minute_end
        ON {TABLE}(minute_end_timestamp_ms)
    """)
    connection.execute(f"""
        CREATE TABLE IF NOT EXISTS {EXTERNAL_EVIDENCE_TABLE} (
            event_id TEXT NOT NULL,
            minute_end_timestamp_ms INTEGER NOT NULL,
            kind TEXT NOT NULL,
            source TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            symbols_json TEXT NOT NULL,
            direction TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(event_id, minute_end_timestamp_ms)
        )
    """)
    connection.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_causal_external_evidence_minute
        ON {EXTERNAL_EVIDENCE_TABLE}(minute_end_timestamp_ms, kind)
    """)


def persist_closed_minute(database_path, report, retention_days=DEFAULT_RETENTION_DAYS):
    if not report.get("minute_is_closed"):
        raise ValueError("only closed minutes may be persisted")
    minute_end = int(report["minute_end_timestamp_ms"])
    cutoff = minute_end - int(retention_days) * 86_400_000
    created_at = datetime.now(timezone.utc).isoformat()
    with connect(database_path) as connection:
        ensure_table(connection)
        for row in report.get("rows", []):
            connection.execute(f"""
                INSERT INTO {TABLE} (
                    instrument,minute_end_timestamp_ms,minute_start_timestamp_ms,
                    sample_count,max_sample_gap_ms,book_imbalance_20_mean,
                    book_imbalance_20_last,taker_imbalance_snapshot_mean,
                    taker_imbalance_snapshot_last,spread_bps_mean,
                    open_interest_usd_first,open_interest_usd_last,
                    open_interest_usd_change,funding_rate_last,
                    long_liquidation_notional_usd_estimate,
                    short_liquidation_notional_usd_estimate,liquidation_event_count,
                    quality_gates_json,eligible_for_learning,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(instrument,minute_end_timestamp_ms) DO UPDATE SET
                    sample_count=excluded.sample_count,
                    max_sample_gap_ms=excluded.max_sample_gap_ms,
                    book_imbalance_20_mean=excluded.book_imbalance_20_mean,
                    book_imbalance_20_last=excluded.book_imbalance_20_last,
                    taker_imbalance_snapshot_mean=excluded.taker_imbalance_snapshot_mean,
                    taker_imbalance_snapshot_last=excluded.taker_imbalance_snapshot_last,
                    spread_bps_mean=excluded.spread_bps_mean,
                    open_interest_usd_first=excluded.open_interest_usd_first,
                    open_interest_usd_last=excluded.open_interest_usd_last,
                    open_interest_usd_change=excluded.open_interest_usd_change,
                    funding_rate_last=excluded.funding_rate_last,
                    long_liquidation_notional_usd_estimate=excluded.long_liquidation_notional_usd_estimate,
                    short_liquidation_notional_usd_estimate=excluded.short_liquidation_notional_usd_estimate,
                    liquidation_event_count=excluded.liquidation_event_count,
                    quality_gates_json=excluded.quality_gates_json,
                    eligible_for_learning=excluded.eligible_for_learning,
                    created_at=excluded.created_at
            """, (
                row["instrument"], minute_end, row["minute_start_timestamp_ms"],
                row["sample_count"], row["max_sample_gap_ms"],
                row["book_imbalance_20_mean"], row["book_imbalance_20_last"],
                row["taker_imbalance_snapshot_mean"], row["taker_imbalance_snapshot_last"],
                row["spread_bps_mean"], row["open_interest_usd_first"],
                row["open_interest_usd_last"], row["open_interest_usd_change"],
                row["funding_rate_last"], row["long_liquidation_notional_usd_estimate"],
                row["short_liquidation_notional_usd_estimate"], row["liquidation_event_count"],
                json.dumps(row["quality_gates"], sort_keys=True, separators=(",", ":")),
                int(row["eligible_for_learning"]), created_at,
            ))
        external = report.get("causal_external_evidence") or {}
        if external and external.get("assignment_clock") != "AVAILABLE_AT":
            raise ValueError("external evidence must use AVAILABLE_AT")
        for event in external.get("events", []):
            connection.execute(f"""
                INSERT INTO {EXTERNAL_EVIDENCE_TABLE}(
                    event_id,minute_end_timestamp_ms,kind,source,occurred_at,
                    available_at,symbols_json,direction,detail_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_id,minute_end_timestamp_ms) DO NOTHING
            """, (
                event["event_id"], minute_end, event["kind"], event["source"],
                event["occurred_at"], event["available_at"],
                json.dumps(event.get("symbols") or [], sort_keys=True, separators=(",", ":")),
                event.get("direction") or "UNKNOWN",
                json.dumps(event.get("detail") or {}, sort_keys=True, separators=(",", ":")),
                created_at,
            ))
        deleted = connection.execute(
            f"DELETE FROM {TABLE} WHERE minute_end_timestamp_ms < ?", (cutoff,)
        ).rowcount
        external_deleted = connection.execute(
            f"DELETE FROM {EXTERNAL_EVIDENCE_TABLE} WHERE minute_end_timestamp_ms < ?",
            (cutoff,),
        ).rowcount
        count = connection.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
        external_count = connection.execute(
            f"SELECT COUNT(*) FROM {EXTERNAL_EVIDENCE_TABLE}"
        ).fetchone()[0]
    return {"upserted": len(report.get("rows", [])), "pruned": deleted,
            "row_count": count, "retention_days": int(retention_days),
            "external_evidence_upserted": len(external.get("events", [])),
            "external_evidence_pruned": external_deleted,
            "external_evidence_row_count": external_count,
            "remote_mirror_enabled_for_table": False}
