"""Build a conservative cross-chain summary of stored whale evidence."""
from __future__ import annotations

import json
import sqlite3
from database.runtime_store import connect as runtime_connect
from datetime import datetime, timedelta, timezone


DB_NAME = "crypto_data.db"
WINDOW_HOURS = 24


def utcnow():
    return datetime.now(timezone.utc)


def ensure_source_tables(con):
    names = {
        row[0]
        for row in con.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type='table' AND name IN ('whale_events', 'whale_scan_state')
        """).fetchall()
    }
    missing = {"whale_events", "whale_scan_state"} - names
    if missing:
        raise RuntimeError(
            "eksik Whale Tracker tabloları: " + ", ".join(sorted(missing))
        )


def ensure_snapshot_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS whale_evidence_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            window_hours INTEGER NOT NULL,
            chain_count INTEGER NOT NULL,
            event_count INTEGER NOT NULL,
            unique_tx_count INTEGER NOT NULL,
            gross_usd_estimate REAL NOT NULL,
            max_usd_estimate REAL NOT NULL,
            alert_eligible_count INTEGER NOT NULL,
            status TEXT NOT NULL,
            chain_summary_json TEXT NOT NULL,
            detail_json TEXT NOT NULL
        )
    """)
    con.commit()


def canonical_chain_name(scan_key):
    if str(scan_key).startswith("Ethereum"):
        return "Ethereum"
    return str(scan_key)


def load_scanned_chains(con):
    return sorted({
        canonical_chain_name(row[0])
        for row in con.execute(
            "SELECT chain FROM whale_scan_state ORDER BY chain ASC"
        ).fetchall()
    })


def load_events(con, cutoff_iso):
    con.row_factory = sqlite3.Row
    return con.execute("""
        SELECT
            chain, event_type, txid, output_index, block_height, block_time,
            asset, amount_native, usd_estimate, destination, direction,
            exchange_flow, source
        FROM whale_events
        WHERE block_time>=?
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


def build_result(events, scanned_chains, created_at):
    event_details = []
    for event in events:
        detail = dict(event)
        detail["value_band"] = value_band(float(event["usd_estimate"]))
        detail["alert_eligible"] = is_alert_eligible(event)
        detail["interpretation"] = (
            "ONCHAIN_EVIDENCE_ONLY; ownership, exchange flow and intent unknown"
        )
        event_details.append(detail)

    chain_names = sorted(set(scanned_chains) | {
        event["chain"] for event in event_details
    })
    chain_summary = {}
    for chain in chain_names:
        chain_events = [
            event for event in event_details if event["chain"] == chain
        ]
        chain_summary[chain] = {
            "event_count": len(chain_events),
            "unique_tx_count": len({event["txid"] for event in chain_events}),
            "gross_usd_estimate": sum(
                float(event["usd_estimate"]) for event in chain_events
            ),
            "max_usd_estimate": max(
                (float(event["usd_estimate"]) for event in chain_events),
                default=0.0,
            ),
        }

    eligible_count = sum(event["alert_eligible"] for event in event_details)
    return {
        "created_at": created_at,
        "window_hours": WINDOW_HOURS,
        "chain_count": len(chain_names),
        "event_count": len(event_details),
        "unique_tx_count": len({
            (event["chain"], event["txid"]) for event in event_details
        }),
        "gross_usd_estimate": sum(
            float(event["usd_estimate"]) for event in event_details
        ),
        "max_usd_estimate": max(
            (float(event["usd_estimate"]) for event in event_details),
            default=0.0,
        ),
        "alert_eligible_count": eligible_count,
        "status": "ALERT_CANDIDATE" if eligible_count else "MONITOR_ONLY",
        "chain_summary": chain_summary,
        "events": event_details,
        "policy_note": (
            "Cross-chain gross output is not net flow. "
            "Unverified evidence never triggers alerts."
        ),
    }


def save_snapshot(con, result):
    con.execute("""
        INSERT INTO whale_evidence_snapshots (
            created_at, window_hours, chain_count, event_count,
            unique_tx_count, gross_usd_estimate, max_usd_estimate,
            alert_eligible_count, status, chain_summary_json, detail_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        result["created_at"],
        result["window_hours"],
        result["chain_count"],
        result["event_count"],
        result["unique_tx_count"],
        result["gross_usd_estimate"],
        result["max_usd_estimate"],
        result["alert_eligible_count"],
        result["status"],
        json.dumps(result["chain_summary"], ensure_ascii=False),
        json.dumps(result, ensure_ascii=False),
    ))
    con.commit()


def main():
    now = utcnow()
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    con = runtime_connect(DB_NAME)
    try:
        ensure_source_tables(con)
        ensure_snapshot_table(con)
        scanned_chains = load_scanned_chains(con)
        events = load_events(con, cutoff.isoformat())
        result = build_result(events, scanned_chains, now.isoformat())
        save_snapshot(con, result)
    finally:
        con.close()

    print(f"AG Crypto Analyst - Cross-chain Whale Evidence | son {WINDOW_HOURS} saat")
    print(
        f"Kapsanan zincir: {result['chain_count']} | "
        f"büyük kanıt: {result['event_count']} | "
        f"benzersiz işlem: {result['unique_tx_count']}"
    )
    for chain, summary in result["chain_summary"].items():
        print(
            f"{chain:<10} | event={summary['event_count']:>3} | "
            f"tx={summary['unique_tx_count']:>3} | "
            f"gross=~${summary['gross_usd_estimate']:,.0f} | "
            f"max=~${summary['max_usd_estimate']:,.0f}"
        )
    print(
        f"Alarm uygun: {result['alert_eligible_count']} | "
        f"durum: {result['status']}"
    )
    for event in result["events"][:10]:
        print(
            f"- {event['chain']:<8} | {event['value_band']:<11} | "
            f"block={event['block_height']:,} | {event['amount_native']:,.4f} "
            f"{event['asset']} | ~${event['usd_estimate']:,.0f} | "
            f"flow={event['exchange_flow']} | tx={event['txid'][:18]}..."
        )
    if not result["events"]:
        print("Bu pencerede kayıtlı eşik üstü zincir kanıtı yok.")
    print("Not: Zincirler arası brüt toplam net akış değildir; doğrulanmamış kanıt alarm üretmez.")
    return result


if __name__ == "__main__":
    main()
