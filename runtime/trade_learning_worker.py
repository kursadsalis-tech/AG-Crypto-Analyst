"""Background specialist for PAPER trade experience and strategy learning.

Realtime execution never waits on historical reviews, challenger discovery,
external research, continuous improvement, or strategy rebalancing. This
worker owns those post-trade/learning responsibilities and has no order
authority.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from database.runtime_store import connect
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
HEALTH_CONTRACT_VERSION = "AGCA_TRADE_LEARNING_HEALTH_V1_BACKGROUND_SPECIALIST"


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
    con = connect(DB_NAME, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def _database_locked(exc):
    return (
        isinstance(exc, sqlite3.OperationalError)
        and "database is locked" in str(exc).lower()
    )


def run_cycle_once():
    started = time.monotonic()
    stamp = utcnow()
    con = local_connection()
    try:
        ensure_tables(con)
        position_management.ensure_tables(con)
        experience_memory.ensure_tables(con)
        con.commit()

        reviews = experience_memory.review_closed_positions(con, stamp)
        learning = trade_learning.run(con, FORWARD_COHORT, stamp)
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
        "promotion_allowed": bool(learning.get("promotion_allowed")),
    }


def run_cycle(attempts=4):
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        try:
            result = run_cycle_once()
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

    while True:
        try:
            result = run_cycle()
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
