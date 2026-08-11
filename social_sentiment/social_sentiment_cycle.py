"""Run Social Sentiment collection at a safe cadence inside the live product."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from social_sentiment import social_sentiment_agent, social_sentiment_analyzer


DB_NAME = "crypto_data.db"
AGENT_NAME = "social_sentiment"
MIN_INTERVAL = timedelta(minutes=15)


def utcnow():
    return datetime.now(timezone.utc)


def parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
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
            last_success_at=COALESCE(excluded.last_success_at,agent_run_state.last_success_at),
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
            print("Social Sentiment: 15 dakikalık aralık dolmadı; bu cycle atlandı.")
            return {"status": "SKIPPED"}

        try:
            collection = social_sentiment_agent.main()
            if not collection or collection["collected"] <= 0:
                raise RuntimeError("hiçbir sosyal paylaşım alınamadı")
            results = social_sentiment_analyzer.main()
            message = (
                f"collected={collection['collected']} | new={collection['saved']} | "
                f"coin_signals={len(results)}"
            )
            save_state(con, "SUCCESS", message, now)
            print(f"Social Sentiment cycle: SUCCESS | {message}")
            return {"status": "SUCCESS", "message": message}
        except Exception as exc:
            message = str(exc)
            save_state(con, "ERROR", message)
            print(f"Social Sentiment cycle: HATA -> {message}")
            return {"status": "ERROR", "message": message}
    finally:
        con.close()


if __name__ == "__main__":
    main(force=True)
