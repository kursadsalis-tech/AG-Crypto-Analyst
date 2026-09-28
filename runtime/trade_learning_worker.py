"""Background specialist for PAPER trade experience and strategy learning.

Realtime execution never waits on historical reviews, challenger discovery,
external research, continuous improvement, or strategy rebalancing. This
worker owns those post-trade/learning responsibilities and has no order
authority.
"""
from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from paper_trading.crypto_profit_agent import DB_NAME, FORWARD_COHORT, ensure_tables
from paper_trading import experience_memory, position_management, strategy_allocator, trade_learning


HEALTH_PATH = Path(os.getenv(
    "AGCA_TRADE_LEARNING_HEALTH",
    "/var/lib/agca/trade-learning-health.json",
))
INTERVAL_SECONDS = max(
    30.0, float(os.getenv("AGCA_TRADE_LEARNING_INTERVAL_SECONDS", "60"))
)
RETRY_SECONDS = max(
    10.0, float(os.getenv("AGCA_TRADE_LEARNING_RETRY_SECONDS", "20"))
)
HEALTH_CONTRACT_VERSION = "AGCA_TRADE_LEARNING_HEALTH_V2_CONTROLLED_CHALLENGER"


def utcnow():
    return datetime.now(timezone.utc)


def write_health(payload):
    HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=HEALTH_PATH.parent,
            prefix=HEALTH_PATH.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            os.chmod(temporary, 0o600)
        os.replace(temporary, HEALTH_PATH)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def local_connection():
    """Use the local SQLite file directly; learning never needs remote triggers."""
    con = sqlite3.connect(DB_NAME, timeout=5)
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _database_locked(exc):
    return (
        isinstance(exc, sqlite3.OperationalError)
        and "database is locked" in str(exc).lower()
    )


def learning_input_key():
    """Read only learning inputs; unchanged history needs no new full review."""
    con = local_connection()
    try:
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        inputs = {}
        # These sources are immutable, append-only observations. Their rowid
        # high-water marks avoid rereading payload history on every timer tick.
        for table in ("external_trader_source_snapshots",
                      "external_trader_trade_observations",
                      "external_trader_entry_contexts"):
            inputs[table] = (con.execute(f"SELECT MAX(rowid) FROM {table}").fetchone()[0]
                             if table in tables else None)
        if "paper_agent_positions" in tables:
            columns = {row[1] for row in con.execute("PRAGMA table_info(paper_agent_positions)")}
            fields = [name for name in (
                "position_id", "exit_time", "net_pnl", "gross_pnl", "notional",
                "entry_fee", "exit_fee", "entry_spread_cost", "exit_spread_cost",
                "entry_slippage_cost", "exit_slippage_cost", "funding_cost",
                "funding_source", "liquidated", "close_reason", "margin_allocated",
                "leverage", "risk_tier", "validation_cohort",
            ) if name in columns]
            if {"status", "evidence_eligible", "position_id"}.issubset(columns):
                inputs["closed_positions"] = con.execute(
                    "SELECT " + ",".join(fields) + " FROM paper_agent_positions "
                    "WHERE evidence_eligible=1 AND status='CLOSED' ORDER BY position_id"
                ).fetchall()
        positions = inputs.pop("closed_positions", [])
        return {
            "external": hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest(),
            "positions": hashlib.sha256(json.dumps(positions, sort_keys=True).encode()).hexdigest(),
        }
    finally:
        con.close()


def run_cycle_once(*, initialize=True, refresh_external=True):
    started = time.monotonic()
    stamp = utcnow()
    con = local_connection()
    try:
        if initialize:
            ensure_tables(con)
            position_management.ensure_tables(con)
            experience_memory.ensure_tables(con)
            con.commit()

        reviews = experience_memory.review_closed_positions(con, stamp)
        learning = trade_learning.run(con, FORWARD_COHORT, stamp,
                                      refresh_external=refresh_external)
        allocations = strategy_allocator.rebalance(con, stamp)
        con.commit()
    finally:
        con.close()

    return {
        "generated_at": stamp.isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "new_experience_reviews": int(reviews),
        "learning_status": str(learning.get("status")),
        "closed_trade_count": int(learning.get("closed") or 0),
        "independent_weeks": int(learning.get("weeks") or 0),
        "allocation_count": len(allocations),
        "challenger_count": int(
            (learning.get("controlled_challenger") or {}).get(
                "challenger_count", 0
            )
        ),
        "reviewable_challenger_count": int(
            (learning.get("controlled_challenger") or {}).get(
                "reviewable_count", 0
            )
        ),
        "challenger_statuses": dict(
            (learning.get("controlled_challenger") or {}).get(
                "statuses", {}
            )
        ),
        "promotion_allowed": bool(learning.get("promotion_allowed")),
        "policy_mutation_allowed": False,
    }


def run_cycle(attempts=4, *, initialize=True, refresh_external=True):
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        try:
            result = run_cycle_once(initialize=initialize, refresh_external=refresh_external)
            result["sqlite_attempts"] = attempt
            return result
        except sqlite3.OperationalError as exc:
            if not _database_locked(exc) or attempt >= attempts:
                raise
        time.sleep(min(0.5 * attempt, 2.0))
    raise RuntimeError("unreachable trade-learning retry state")


def main():
    if os.getenv("AGCA_EXECUTION_MODE", "PAPER_ONLY") != "PAPER_ONLY":
        raise SystemExit("Safety gate: trade learning accepts PAPER_ONLY exclusively")

    initialized = False
    last_input_key = None
    last_result = {}
    while True:
        started_at = utcnow()
        write_health({
            "health_contract_version": HEALTH_CONTRACT_VERSION,
            "status": "RUNNING",
            "phase": "LEARNING_CYCLE",
            "at": started_at.isoformat(),
            "execution_mode": "PAPER_ONLY",
            "may_send_real_orders": False,
        })
        try:
            input_key = learning_input_key()
            if initialized and input_key == last_input_key:
                result = {**last_result, "skipped_unchanged_inputs": True}
            else:
                result = run_cycle(
                    initialize=not initialized,
                    refresh_external=(not initialized or
                        input_key["external"] != last_input_key["external"]),
                )
                initialized = True
                # Capture the pre-cycle key: inputs arriving during a review
                # must trigger another pass, never be marked already reviewed.
                last_input_key = input_key
                last_result = result
                result = {**result, "skipped_unchanged_inputs": False}
            write_health({
                "health_contract_version": HEALTH_CONTRACT_VERSION,
                "status": "HEALTHY",
                "at": utcnow().isoformat(),
                "execution_mode": "PAPER_ONLY",
                "may_send_real_orders": False,
                **result,
            })
            delay = INTERVAL_SECONDS
        except Exception as exc:
            write_health({
                "health_contract_version": HEALTH_CONTRACT_VERSION,
                "status": "DEGRADED",
                "at": utcnow().isoformat(),
                "execution_mode": "PAPER_ONLY",
                "may_send_real_orders": False,
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            })
            delay = RETRY_SECONDS
        time.sleep(delay)


if __name__ == "__main__":
    main()
