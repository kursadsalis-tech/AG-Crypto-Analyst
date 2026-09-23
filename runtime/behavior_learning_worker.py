"""Background specialist for behavior-policy learning.

This worker owns expensive historical aggregation and missed-opportunity
settlement.  The realtime PAPER decision path reads only its bounded cache, so
market-data ingest, fills and opportunity scans never wait on historical
learning queries.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from database.behavior_event_outcome_store import ensure_outcome_table
from database.runtime_store import connect
from paper_trading import behavior_decision_core as core


DB_PATH = os.getenv("AGCA_DATABASE_PATH", "/var/lib/agca/crypto_data.db")
HEALTH_PATH = Path(os.getenv(
    "AGCA_BEHAVIOR_LEARNING_HEALTH",
    "/var/lib/agca/behavior-learning-health.json",
))
INTERVAL_SECONDS = max(
    60.0, float(os.getenv("AGCA_BEHAVIOR_LEARNING_INTERVAL_SECONDS", "300"))
)
RETRY_SECONDS = max(
    15.0, float(os.getenv("AGCA_BEHAVIOR_LEARNING_RETRY_SECONDS", "30"))
)
MISSED_SETTLEMENT_LIMIT = max(
    1, int(os.getenv("AGCA_BEHAVIOR_MISSED_SETTLEMENT_LIMIT", "2000"))
)
HEALTH_CONTRACT_VERSION = "AGCA_BEHAVIOR_LEARNING_HEALTH_V1_BACKGROUND_CACHE"


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


def run_cycle():
    started = time.monotonic()
    generated_at = utcnow()
    con = connect(DB_PATH, timeout=30)
    try:
        con.execute("PRAGMA busy_timeout=30000")
        # Schema/index work is rare and committed before the historical scan so
        # this low-priority worker never retains a writer lock while learning.
        ensure_outcome_table(con)
        core.ensure_tables(con)
        con.commit()

        result = core.refresh_pattern_cache(
            con,
            now=generated_at,
            missed_limit=MISSED_SETTLEMENT_LIMIT,
        )
        awaiting = con.execute(
            """SELECT COUNT(*) FROM paper_behavior_missed_opportunities
               WHERE review_status='AWAITING_OUTCOME'"""
        ).fetchone()[0]
        outcome_rows = con.execute(
            """SELECT COUNT(*) FROM behavior_event_forward_outcomes
               WHERE horizon_minutes=?""",
            (core.HORIZON_MINUTES,),
        ).fetchone()[0]
    finally:
        con.close()
    return {
        **result,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "awaiting_missed": int(awaiting),
        "horizon_outcome_rows": int(outcome_rows),
    }


def main():
    if os.getenv("AGCA_EXECUTION_MODE", "PAPER_ONLY") != "PAPER_ONLY":
        raise SystemExit("Safety gate: behavior learning accepts PAPER_ONLY exclusively")

    while True:
        try:
            result = run_cycle()
            write_health({
                "health_contract_version": HEALTH_CONTRACT_VERSION,
                "status": "HEALTHY",
                "at": utcnow().isoformat(),
                "execution_mode": "PAPER_ONLY",
                "may_send_real_orders": False,
                "database_path": DB_PATH,
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
                "database_path": DB_PATH,
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            })
            delay = RETRY_SECONDS
        time.sleep(delay)


if __name__ == "__main__":
    main()
