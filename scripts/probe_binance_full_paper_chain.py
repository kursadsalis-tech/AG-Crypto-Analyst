#!/usr/bin/env python3
"""Isolated actual-VM PAPER chain acceptance on a temporary SQLite clone.

Canonical /var/lib/agca/crypto_data.db is opened read-only and copied using
SQLite backup. All observer persistence, risk snapshots, policy reviews,
selection runs, and any PAPER position experiments happen only in the temp
clone. The temp clone is deleted before exit. No real-order authority exists.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data.binance_ws_market_collector import BinanceWsMinuteCollector
from data.rolling_behavior_window import aggregate_closed_minute
from database.market_behavior_store import persist_closed_minute
from database.runtime_store import connect as runtime_connect
from paper_trading.exchange_metadata import load_cache
from paper_trading import crypto_profit_agent as agent
from paper_trading import position_management
from risk_manager import risk_manager as risk
from scripts.collect_closed_behavior_minute import verified_exchange_info


CANONICAL_DB = "/var/lib/agca/crypto_data.db"


SEED_TABLES = (
    "behavior_event_forward_outcomes",
    "paper_agent_portfolio",
    "paper_agent_positions",
    "paper_behavior_decisions",
)


def _phase(name, **detail):
    payload = {"probe_phase": name, **detail}
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)


def _seed_from_canonical(source_path: str, destination_path: str) -> dict:
    """Copy only decision-relevant bounded tables; never clone the full hot DB."""
    source_uri = Path(source_path).resolve().as_uri() + "?mode=ro"
    counts = {}
    with sqlite3.connect(source_uri, uri=True, timeout=10) as source:
        source.execute("PRAGMA query_only=ON")
        source.execute("PRAGMA busy_timeout=10000")
        active = 0
        if source.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
                AND name='paper_agent_positions'""").fetchone():
            active = source.execute("""
                SELECT COUNT(*) FROM paper_agent_positions
                WHERE status IN ('OPEN','PENDING_ENTRY','PENDING')
            """).fetchone()[0]
        if active:
            return {"blocked_active_positions": int(active), "table_rows": {}}

        with sqlite3.connect(destination_path) as destination:
            destination.execute("PRAGMA journal_mode=DELETE")
            for table in SEED_TABLES:
                row = source.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                if not row or not row[0]:
                    continue
                destination.execute(row[0])
                columns = [
                    item[1] for item in source.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                ]
                if not columns:
                    continue
                placeholders = ",".join("?" for _ in columns)
                select = source.execute(f"SELECT * FROM {table}")
                copied = 0
                while True:
                    batch = select.fetchmany(500)
                    if not batch:
                        break
                    destination.executemany(
                        f"INSERT INTO {table} VALUES({placeholders})", batch
                    )
                    copied += len(batch)
                counts[table] = copied
            destination.commit()
    return {"blocked_active_positions": 0, "table_rows": counts}


def _cleanup(path: str) -> None:
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(path + suffix).unlink()
        except FileNotFoundError:
            pass


def run_probe(*, source_db=CANONICAL_DB, samples=4, cadence_seconds=10):
    if os.getenv("AGCA_EXECUTION_MODE", "PAPER_ONLY") != "PAPER_ONLY":
        raise RuntimeError("PAPER_ONLY_REQUIRED")
    database_url = os.getenv("AGCA_REMOTE_DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("PRIVATE_EGRESS_NOT_CONFIGURED")
    os.environ["AGCA_REMOTE_MIRROR_INLINE"] = "0"

    handle = tempfile.NamedTemporaryFile(
        prefix="agca-pr36-fullchain-", suffix=".db", dir="/tmp", delete=False
    )
    temp_db = handle.name
    handle.close()
    try:
        _phase("SEED_TEMP_DB_START")
        seeded = _seed_from_canonical(source_db, temp_db)
        _phase("SEED_TEMP_DB_DONE", **seeded)
        if seeded["blocked_active_positions"]:
            return {
                "mode": "ISOLATED_TEMP_DB_PAPER_CHAIN",
                "status": "BLOCKED_ACTIVE_POSITIONS",
                "canonical_db_written": False,
                "active_position_count": seeded["blocked_active_positions"],
                "temp_db_deleted_on_exit": True,
                "real_orders": False,
            }
        with sqlite3.connect(temp_db) as con:
            baseline_position_id = con.execute(
                "SELECT COALESCE(MAX(position_id),0) FROM paper_agent_positions"
            ).fetchone()[0] if con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='paper_agent_positions'"
            ).fetchone() else 0

        _phase("OBSERVER_START")
        info, fetched_at = verified_exchange_info()
        usd_m_metadata = load_cache(
            os.getenv(
                "AGCA_EXCHANGE_METADATA_CACHE",
                "/var/lib/agca/exchange-info-usdm.json",
            ),
            "USD_M_PERPETUAL",
            max_age_seconds=float(os.getenv(
                "AGCA_EXCHANGE_METADATA_MAX_AGE_SECONDS", "7200"
            )),
        )
        collector = BinanceWsMinuteCollector(
            info, fetched_at, database_url=database_url
        )
        collected = asyncio.run(
            collector.run(samples=samples, cadence_seconds=cadence_seconds)
        )
        minute_end = int(collected["minute_end_ms"])
        time.sleep(max(
            0, (minute_end - int(time.time() * 1000)) / 1000 + 0.02
        ))
        report = aggregate_closed_minute(collected["bundles"], minute_end)
        _phase("OBSERVER_DONE", row_count=report["row_count"],
               eligible_row_count=report["eligible_row_count"])
        report["source_exchange"] = "BINANCE_USD_M"
        report["causal_external_evidence"] = {
            "contract_version": "CAUSAL_MINUTE_EVIDENCE_V1",
            "minute_end_timestamp_ms": minute_end,
            "assignment_clock": "AVAILABLE_AT",
            "event_count": 0,
            "counts": {
                kind: 0 for kind in ("MACRO", "NEWS", "ONCHAIN", "WHALE")
            },
            "events": [],
        }
        _phase("TEMP_PERSIST_START")
        persisted = persist_closed_minute(temp_db, report, retention_days=30)
        _phase("TEMP_PERSIST_DONE", upserted=persisted.get("upserted"))
        eligible = tuple(sorted(
            row["instrument"] for row in report["rows"]
            if row.get("eligible_for_learning")
        ))

        # Risk manager reads/writes only the cloned DB for this process.
        _phase("RISK_START", eligible_symbols=list(eligible))
        original_risk_db = risk.DB_NAME
        risk.DB_NAME = temp_db
        try:
            risk_result = risk.main()
        finally:
            risk.DB_NAME = original_risk_db

        _phase("RISK_DONE", market_state=risk_result.get("market_state"),
               source_error_count=len(risk_result.get("source_errors") or {}))
        risk_symbols = {
            row["symbol"] for row in risk_result.get("assets", [])
        }
        missing_dynamic_risk = sorted(set(eligible) - risk_symbols)

        _phase("DECISION_GATE_START")
        now = datetime.now(timezone.utc)
        telemetry = {}
        with runtime_connect(temp_db, timeout=30) as con:
            agent.ensure_tables(con)
            position_management.ensure_tables(con)
            selected = agent.select_new_positions(
                con, now,
                exchange_metadata={"USD_M_PERPETUAL": usd_m_metadata},
                telemetry=telemetry,
            )
            latest_scan = con.execute("""
                SELECT core_status,candidate_count,selected_count,
                       reason_counts_json
                FROM paper_behavior_selection_runs
                ORDER BY run_id DESC LIMIT 1
            """).fetchone()
            new_positions = con.execute("""
                SELECT position_id,symbol,market_type,direction,status
                FROM paper_agent_positions
                WHERE position_id>? ORDER BY position_id
            """, (baseline_position_id,)).fetchall()
            current_misses = []
            if con.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
                    AND name='paper_behavior_missed_opportunities'""").fetchone():
                current_misses = con.execute("""
                    SELECT symbol,rejection_reason
                    FROM paper_behavior_missed_opportunities
                    WHERE evidence_minute_end_ms=?
                    ORDER BY symbol,rejection_reason
                """, (minute_end,)).fetchall()

        _phase("DECISION_GATE_DONE", selected=int(selected),
               core_status=latest_scan[0] if latest_scan else None)
        invalid_selected_market = [
            {
                "position_id": int(row[0]), "symbol": row[1],
                "market_type": row[2], "direction": row[3], "status": row[4],
            }
            for row in new_positions if row[2] != "USD_M_PERPETUAL"
        ]
        risk_coverage_misses = [
            {"symbol": row[0], "reason": row[1]}
            for row in current_misses if row[1] == "RISK_COVERAGE_MISSING"
        ]
        transport = collected["transport"]
        integration_ok = (
            bool(eligible)
            and persisted.get("upserted") == len(eligible)
            and not missing_dynamic_risk
            and not risk_result.get("source_errors")
            and not risk_coverage_misses
            and not invalid_selected_market
            and transport["oi_error_count"] == 0
            and not transport["websocket_errors"]
            and all(transport["subscription_confirmed"].values())
        )
        return {
            "mode": "ISOLATED_TEMP_DB_PAPER_CHAIN",
            "status": "COMPLETE" if integration_ok else "PARTIAL_OR_BLOCKED",
            "canonical_db_written": False,
            "temp_db_deleted_on_exit": True,
            "source_exchange": "BINANCE_USD_M",
            "eligible_symbols": list(eligible),
            "persisted_eligible_rows": persisted.get("upserted"),
            "risk_snapshot_id_in_temp_clone": risk_result.get("snapshot_id"),
            "risk_market_state": risk_result.get("market_state"),
            "risk_source_errors": risk_result.get("source_errors"),
            "missing_dynamic_risk": missing_dynamic_risk,
            "decision_core_status": latest_scan[0] if latest_scan else None,
            "decision_candidate_count": latest_scan[1] if latest_scan else None,
            "selected_count_in_temp_clone": int(selected),
            "selection_reasons": (
                json.loads(latest_scan[3] or "{}") if latest_scan else {}
            ),
            "new_temp_positions": [
                {
                    "position_id": int(row[0]), "symbol": row[1],
                    "market_type": row[2], "direction": row[3],
                    "status": row[4],
                } for row in new_positions
            ],
            "risk_coverage_misses": risk_coverage_misses,
            "invalid_selected_market": invalid_selected_market,
            "transport": {
                "oi_error_count": transport["oi_error_count"],
                "websocket_errors": transport["websocket_errors"],
                "subscription_confirmed": transport["subscription_confirmed"],
                "liquidation_state": transport["liquidation_state"],
            },
            "real_orders": False,
        }
    finally:
        _cleanup(temp_db)


if __name__ == "__main__":
    print(json.dumps(run_probe(), sort_keys=True))
