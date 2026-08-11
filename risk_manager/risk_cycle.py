"""Run the market Risk Manager at a safe live-product cadence."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from risk_manager import risk_manager


DB_NAME = risk_manager.DB_NAME
AGENT_NAME = "risk_manager"
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
        INSERT INTO agent_run_state(agent_name,last_success_at,last_status,last_message)
        VALUES (?,?,?,?)
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
            print("Risk Manager: 15 dakikalık aralık dolmadı; bu cycle atlandı.")
            return {"status": "SKIPPED"}

        try:
            result = risk_manager.main()
            source_errors = result.get("source_errors") or {}
            status = "PARTIAL" if source_errors else "SUCCESS"
            message = (
                f"snapshot={result['snapshot_id']} | "
                f"market_state={result['market_state']} | "
                f"coverage={result['coverage_count']}/{result['coverage_total']} | "
                f"market_stress={result['market_stress_count']} | "
                f"asset_blocks={result['asset_block_count']} | "
                f"source_errors={len(source_errors)}"
            )
            save_state(con, status, message, now)
            print(f"Risk Manager cycle: {status} | {message}")
            return {"status": status, "message": message, "result": result}
        except Exception as exc:
            message = str(exc)
            save_state(con, "ERROR", message)
            print(f"Risk Manager cycle: HATA -> {message}")
            return {"status": "ERROR", "message": message}
    finally:
        con.close()


if __name__ == "__main__":
    main(force=True)
