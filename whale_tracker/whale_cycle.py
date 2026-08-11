"""Run Bitcoin whale evidence collection at a safe live-product cadence."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from whale_tracker import (
    bitcoin_whale_collector,
    ethereum_whale_collector,
    whale_evidence_analyzer,
)


DB_NAME = "crypto_data.db"
AGENT_NAME = "whale_tracker"
MIN_INTERVAL = timedelta(minutes=5)


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
            print("Whale Tracker: 5 dakikalık aralık dolmadı; bu cycle atlandı.")
            return {"status": "SKIPPED"}

        try:
            results = {}
            source_errors = {}
            collectors = (
                ("btc", bitcoin_whale_collector.main),
                ("eth", ethereum_whale_collector.main),
            )
            for source_name, collector in collectors:
                try:
                    result = collector()
                    if not result:
                        raise RuntimeError("collector sonuç döndürmedi")
                    results[source_name] = result
                except Exception as exc:
                    source_errors[source_name] = str(exc)
                    print(f"Whale Tracker {source_name.upper()}: HATA -> {exc}")

            if not results:
                joined = " | ".join(
                    f"{name}={message}"
                    for name, message in source_errors.items()
                )
                raise RuntimeError(f"tüm whale kaynakları başarısız: {joined}")

            btc = results.get("btc", {})
            eth = results.get("eth", {})
            btc_blocks = int(btc.get("blocks_scanned", 0))
            eth_blocks = int(eth.get("blocks_scanned", 0))
            total_blocks = btc_blocks + eth_blocks
            total_new = int(btc.get("inserted", 0)) + int(eth.get("inserted", 0))
            cycle_status = "PARTIAL" if source_errors else "SUCCESS"

            if total_blocks <= 0:
                message = (
                    f"btc_blocks={btc_blocks} | eth_blocks={eth_blocks} | "
                    f"new=0 | source_errors={len(source_errors)} | "
                    "analysis=UNCHANGED"
                )
                save_state(con, cycle_status, message, now)
                print(f"Whale Tracker cycle: {cycle_status} | {message}")
                return {"status": cycle_status, "message": message}

            analysis = whale_evidence_analyzer.main()
            message = (
                f"btc_blocks={btc_blocks} | eth_blocks={eth_blocks} | "
                f"btc_detected={int(btc.get('detected', 0))} | "
                f"eth_detected={int(eth.get('detected', 0))} | "
                f"new={total_new} | source_errors={len(source_errors)} | "
                f"window_events={analysis['event_count']} | "
                f"alert_eligible={analysis['alert_eligible_count']} | "
                f"analysis={analysis['status']}"
            )
            save_state(con, cycle_status, message, now)
            print(f"Whale Tracker cycle: {cycle_status} | {message}")
            return {"status": cycle_status, "message": message}
        except Exception as exc:
            message = str(exc)
            save_state(con, "ERROR", message)
            print(f"Whale Tracker cycle: HATA -> {message}")
            return {"status": "ERROR", "message": message}
    finally:
        con.close()


if __name__ == "__main__":
    main(force=True)
