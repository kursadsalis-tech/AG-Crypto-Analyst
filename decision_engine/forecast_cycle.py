"""Cadenced runtime wrapper for the unified probabilistic Forecast Engine."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from decision_engine import forecast_engine


DB_NAME = forecast_engine.DB_NAME
AGENT_NAME = "unified_forecast_engine"
MIN_INTERVAL = timedelta(hours=1)


def utcnow():
    return datetime.now(timezone.utc)


def parse_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
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
    row = con.execute("SELECT last_success_at FROM agent_run_state WHERE agent_name=?", (AGENT_NAME,)).fetchone()
    last = parse_time(row[0]) if row else None
    return last is None or now - last >= MIN_INTERVAL


def save_state(con, status, message, success_at=None):
    con.execute("""
        INSERT INTO agent_run_state(agent_name,last_success_at,last_status,last_message)
        VALUES(?,?,?,?)
        ON CONFLICT(agent_name) DO UPDATE SET
          last_success_at=COALESCE(excluded.last_success_at,agent_run_state.last_success_at),
          last_status=excluded.last_status,last_message=excluded.last_message
    """, (AGENT_NAME, success_at.isoformat() if success_at else None, status, message))
    con.commit()


def main(force=False):
    now = utcnow()
    con = sqlite3.connect(DB_NAME, timeout=30)
    try:
        ensure_state_table(con)
        if not force and not is_due(con, now):
            print("Unified Forecast Engine: 1 saatlik aralık dolmadı; bu cycle atlandı.")
            return {"status": "SKIPPED"}
    finally:
        con.close()
    try:
        result = forecast_engine.run(now=now)
        forecast_engine.render(result)
        message = (f"forecasts={len(result['forecasts'])} | no_forecast={len(result['no_forecast'])} | "
                   f"settled={result['settled']} | retrained={int(result['model_retrained'])}")
        con = sqlite3.connect(DB_NAME, timeout=30)
        try:
            ensure_state_table(con)
            save_state(con, result["status"], message,
                       now if result["status"] == "SUCCESS" else None)
        finally:
            con.close()
        print(f"Unified Forecast cycle: {result['status']} | {message}")
        return result
    except Exception as exc:
        message = str(exc)
        waiting_for_close = message.startswith("STALE_COMMON_CANDLE")
        status = "WAITING_FRESH_CLOSE" if waiting_for_close else "ERROR"
        con = sqlite3.connect(DB_NAME, timeout=30)
        try:
            ensure_state_table(con)
            save_state(con, status, message)
        finally:
            con.close()
        if waiting_for_close:
            print("Unified Forecast Engine: güncel kapanış penceresi kaçırıldı; "
                  "sonraki taze saat kapanışı bekleniyor.")
            return {"status": "SKIPPED", "reason": status, "message": message}
        print(f"Unified Forecast cycle: HATA -> {message}")
        return {"status": "ERROR", "message": message}


if __name__ == "__main__":
    main(force=True)
