"""Background specialist for behavior-policy learning.

This worker owns expensive historical aggregation and missed-opportunity
settlement.  The realtime PAPER decision path reads only its bounded cache, so
market-data ingest, fills and opportunity scans never wait on historical
learning queries.
"""
from __future__ import annotations

import json
import os
import sqlite3
from database.sqlite_policy import connect_local, is_sqlite_busy
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from runtime.process_identity import WorkerHealthState
from runtime.worker_progress import WorkProgress

from database.behavior_event_outcome_store import (
    ensure_outcome_table,
    settle_queued_outcomes,
)
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
OUTCOME_QUEUE_LIMIT = max(
    1, int(os.getenv("AGCA_BEHAVIOR_OUTCOME_QUEUE_LIMIT", "20"))
)
HEALTH_CONTRACT_VERSION = "AGCA_BEHAVIOR_LEARNING_HEALTH_V1_BACKGROUND_CACHE"


_HEALTH_STATE = WorkerHealthState()

def utcnow():
    return datetime.now(timezone.utc)


def write_health(payload, *, success=None):
    payload = _HEALTH_STATE.decorate({
        **payload,
        "core_version": core.VERSION,
        "producer_version": core.PATTERN_CACHE_PRODUCER_VERSION,
        "cache_contract_version": core.PATTERN_CACHE_CONTRACT_VERSION,
    }, success=success)
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


def _database_locked(exc):
    return is_sqlite_busy(exc)


def refresh_status():
    last = _HEALTH_STATE.last_success_at
    if last and 0 <= (utcnow() - datetime.fromisoformat(last)).total_seconds() <= 360:
        return "HEALTHY"
    return "RUNNING"


def report_progress(progress):
    write_health({"health_contract_version": HEALTH_CONTRACT_VERSION,
                  "status": refresh_status(), "phase": progress["phase"],
                  "progress": progress, "at": utcnow().isoformat(),
                  "execution_mode": "PAPER_ONLY", "may_send_real_orders": False}, success=False)


_PROGRESS_ENABLED = False
_PROGRESS = WorkProgress(lambda progress: report_progress(progress) if _PROGRESS_ENABLED else None)


def local_connection():
    """Open a local-only connection without remote-mirror trigger setup."""
    con = connect_local(DB_PATH, timeout=0.25)
    con.execute("PRAGMA busy_timeout=250")
    _PROGRESS.attach(con)
    return con


def run_cycle_once():
    started = time.monotonic()
    generated_at = utcnow()
    con = local_connection()
    try:
        # Schema/index work is rare and committed before the historical scan so
        # this low-priority worker never retains a writer lock while learning.
        ensure_outcome_table(con)
        core.ensure_tables(con)
        con.commit()
        _PROGRESS.advance("SCHEMA_READY")

        queued_outcomes = settle_queued_outcomes(
            con, limit=OUTCOME_QUEUE_LIMIT
        )
        _PROGRESS.advance("OUTCOME_QUEUE_SETTLED")
        result = core.refresh_pattern_cache(
            con,
            now=generated_at,
            missed_limit=MISSED_SETTLEMENT_LIMIT,
            progress=_PROGRESS.step,
        )
        _PROGRESS.advance("PATTERN_CACHE_COMMITTED")
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
        "outcome_queue": queued_outcomes,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "awaiting_missed": int(awaiting),
        "horizon_outcome_rows": int(outcome_rows),
    }


def run_cycle(attempts=6):
    """Retry transient SQLite writer contention with fresh local connections."""
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        try:
            result = run_cycle_once()
            result["sqlite_attempts"] = attempt
            return result
        except sqlite3.OperationalError as exc:
            if not _database_locked(exc) or attempt >= attempts:
                raise
        time.sleep(min(0.5 * attempt, 3.0))
    raise RuntimeError("unreachable behavior-learning retry state")


def _main():
    if os.getenv("AGCA_EXECUTION_MODE", "PAPER_ONLY") != "PAPER_ONLY":
        raise SystemExit("Safety gate: behavior learning accepts PAPER_ONLY exclusively")

    while True:
        write_health({
            "health_contract_version": HEALTH_CONTRACT_VERSION,
            "status": refresh_status(), "phase": "LEARNING_CYCLE",
            "at": utcnow().isoformat(), "execution_mode": "PAPER_ONLY",
            "may_send_real_orders": False, "database_path": DB_PATH,
        }, success=False)
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
                "fatal_error": isinstance(exc, sqlite3.DatabaseError) and not is_sqlite_busy(exc),
                "sqlite_busy": is_sqlite_busy(exc),
                "error": str(exc)[:500],
            })
            delay = RETRY_SECONDS
        time.sleep(delay)


def main():
    global _PROGRESS_ENABLED
    _PROGRESS_ENABLED = True
    try:
        return _main()
    finally:
        _PROGRESS_ENABLED = False


if __name__ == "__main__":
    main()
