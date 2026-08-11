"""Summarize confirmed large Bitcoin outputs without inventing direction."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone


DB_NAME = "crypto_data.db"
WINDOW_HOURS = 24


def utcnow():
    return datetime.now(timezone.utc)


def ensure_source_table(con):
    row = con.execute("""
        SELECT 1
        FROM sqlite_master
        WHERE type='table' AND name='whale_events'
    """).fetchone()
    if not row:
        raise RuntimeError("whale_events tablosu yok; önce collector çalıştırılmalı")


def ensure_snapshot_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS whale_analysis_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            window_hours INTEGER NOT NULL,
            event_count INTEGER NOT NULL,
            unique_tx_count INTEGER NOT NULL,
            gross_usd_estimate REAL NOT NULL,
            max_usd_estimate REAL NOT NULL,
            alert_eligible_count INTEGER NOT NULL,
            status TEXT NOT NULL,
            detail_json TEXT NOT NULL
        )
    """)
    con.commit()


def load_events(con, cutoff_iso):
    con.row_factory = sqlite3.Row
    return con.execute("""
        SELECT
            chain, event_type, txid, output_index, block_height, block_time,
            asset, amount_native, usd_estimate, destination, direction,
            exchange_flow, source
        FROM whale_events
        WHERE chain='Bitcoin' AND block_time>=?
        ORDER BY usd_estimate DESC, block_height DESC
    """, (cutoff_iso,)).fetchall()


def value_band(usd_estimate):
    if usd_estimate >= 100_000_000:
        return "EXTREME"
    if usd_estimate >= 50_000_000:
        return "VERY_LARGE"
    if usd_estimate >= 25_000_000:
        return "LARGE"
    return "SIGNIFICANT"


def is_alert_eligible(event):
    return (
        event["direction"] in {"EXCHANGE_INFLOW", "EXCHANGE_OUTFLOW"}
        and event["exchange_flow"] == "VERIFIED"
    )


def build_result(events, created_at):
    event_details = []
    for event in events:
        detail = dict(event)
        detail["value_band"] = value_band(float(event["usd_estimate"]))
        detail["alert_eligible"] = is_alert_eligible(event)
        detail["interpretation"] = (
            "CONFIRMED_OUTPUT_ONLY; ownership, exchange flow and intent unknown"
        )
        event_details.append(detail)

    eligible_count = sum(item["alert_eligible"] for item in event_details)
    return {
        "created_at": created_at,
        "window_hours": WINDOW_HOURS,
        "event_count": len(event_details),
        "unique_tx_count": len({item["txid"] for item in event_details}),
        "gross_usd_estimate": sum(
            float(item["usd_estimate"]) for item in event_details
        ),
        "max_usd_estimate": max(
            (float(item["usd_estimate"]) for item in event_details),
            default=0.0,
        ),
        "alert_eligible_count": eligible_count,
        "status": "ALERT_CANDIDATE" if eligible_count else "MONITOR_ONLY",
        "events": event_details,
        "policy_note": (
            "Gross output total is not net flow. Unverified outputs never trigger alerts."
        ),
    }


def save_snapshot(con, result):
    con.execute("""
        INSERT INTO whale_analysis_snapshots (
            created_at, window_hours, event_count, unique_tx_count,
            gross_usd_estimate, max_usd_estimate, alert_eligible_count,
            status, detail_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        result["created_at"],
        result["window_hours"],
        result["event_count"],
        result["unique_tx_count"],
        result["gross_usd_estimate"],
        result["max_usd_estimate"],
        result["alert_eligible_count"],
        result["status"],
        json.dumps(result, ensure_ascii=False),
    ))
    con.commit()


def main():
    now = utcnow()
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    con = sqlite3.connect(DB_NAME)
    try:
        ensure_source_table(con)
        ensure_snapshot_table(con)
        events = load_events(con, cutoff.isoformat())
        result = build_result(events, now.isoformat())
        save_snapshot(con, result)
    finally:
        con.close()

    print(f"AG Crypto Analyst - Bitcoin Whale Evidence Analyzer | son {WINDOW_HOURS} saat")
    print(
        f"Tekil büyük çıktı: {result['event_count']} | "
        f"benzersiz işlem: {result['unique_tx_count']} | "
        f"brüt çıktı tahmini: ~${result['gross_usd_estimate']:,.0f}"
    )
    print(
        f"En büyük çıktı: ~${result['max_usd_estimate']:,.0f} | "
        f"alarm uygun: {result['alert_eligible_count']} | "
        f"durum: {result['status']}"
    )
    for event in result["events"][:10]:
        print(
            f"- {event['value_band']:<11} | block={event['block_height']:,} | "
            f"{event['amount_native']:,.4f} BTC | ~${event['usd_estimate']:,.0f} | "
            f"flow={event['exchange_flow']} | tx={event['txid'][:16]}..."
        )
    if not result["events"]:
        print("Bu pencerede kayıtlı eşik üstü Bitcoin çıktısı yok.")
    print("Not: Brüt çıktı toplamı net akış değildir; doğrulanmamış olaylar alarm üretmez.")
    return result


if __name__ == "__main__":
    main()
