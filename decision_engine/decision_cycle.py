"""Run Decision Engine and its shadow validator at a safe cadence."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from decision_engine import decision_engine, decision_shadow_validator


DB_NAME = decision_engine.DB_NAME
AGENT_NAME = "decision_engine"
MIN_INTERVAL = timedelta(minutes=15)


def utcnow():
    return datetime.now(timezone.utc)


def parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def ensure_state_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS agent_run_state (
            agent_name TEXT PRIMARY KEY,
            last_success_at TEXT,
            last_status TEXT,
            last_message TEXT
        )
    """)
    con.commit()


def is_due(con, now):
    row = con.execute(
        "SELECT last_success_at FROM agent_run_state WHERE agent_name=?",
        (AGENT_NAME,),
    ).fetchone()
    last_success = parse_time(row[0]) if row else None
    return last_success is None or now - last_success >= MIN_INTERVAL


def save_state(con, status, message, success_at=None):
    con.execute("""
        INSERT INTO agent_run_state(
            agent_name, last_success_at, last_status, last_message
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(agent_name) DO UPDATE SET
            last_success_at=COALESCE(
                excluded.last_success_at,
                agent_run_state.last_success_at
            ),
            last_status=excluded.last_status,
            last_message=excluded.last_message
    """, (
        AGENT_NAME,
        success_at.isoformat() if success_at else None,
        status,
        message,
    ))
    con.commit()


def main(force=False):
    now = utcnow()
    con = sqlite3.connect(DB_NAME)
    try:
        ensure_state_table(con)
        if not force and not is_due(con, now):
            print(
                "Decision Engine: 15 dakikalık aralık dolmadı; "
                "bu cycle atlandı."
            )
            return {"status": "SKIPPED"}
    finally:
        con.close()

    try:
        engine_result = decision_engine.main()
        validation = decision_shadow_validator.process(engine_result)
        errors = (
            len(engine_result["technical_errors"])
            + len(validation["recorded"]["source_errors"])
            + len(validation["settled"]["source_errors"])
        )
        status = "PARTIAL" if errors else "SUCCESS"
        all_scores = validation["performance"]["ALL"]
        message = (
            f"decisions={len(engine_result['decisions'])} | "
            f"shadow_new={validation['recorded']['created']} | "
            f"shadow_settled={validation['settled']['settled']} | "
            f"shadow_open={all_scores['open']} | "
            f"source_errors={errors}"
        )
        con = sqlite3.connect(DB_NAME)
        try:
            ensure_state_table(con)
            save_state(con, status, message, now)
        finally:
            con.close()
        print(f"Decision Engine cycle: {status} | {message}")
        return {
            "status": status,
            "message": message,
            "engine": engine_result,
            "validation": validation,
        }
    except Exception as exc:
        message = str(exc)
        con = sqlite3.connect(DB_NAME)
        try:
            ensure_state_table(con)
            save_state(con, "ERROR", message)
        finally:
            con.close()
        print(f"Decision Engine cycle: HATA -> {message}")
        return {"status": "ERROR", "message": message}


if __name__ == "__main__":
    main(force=True)
