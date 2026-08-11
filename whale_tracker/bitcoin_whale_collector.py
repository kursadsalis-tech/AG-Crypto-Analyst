"""Collect directly observable large Bitcoin outputs from confirmed blocks.

This first Whale Tracker version deliberately does not infer wallet ownership,
exchange flow, or buy/sell intent. A large UTXO can be change or a self-transfer.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import requests


DB_NAME = "crypto_data.db"
BLOCKCHAIN_BASE_URL = "https://blockchain.info"
BINANCE_PRICE_URL = "https://data-api.binance.vision/api/v3/ticker/price"
USER_AGENT = "AG-Crypto-Analyst/0.9.0"
CHAIN = "Bitcoin"
ASSET = "BTC"
SATOSHIS_PER_BTC = 100_000_000
USD_THRESHOLD = 10_000_000.0
MAX_BLOCKS_PER_RUN = 6


def utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


def get_json(url, *, params=None, timeout=45):
    response = requests.get(
        url,
        params=params,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    return response.json()


def fetch_latest_height():
    payload = get_json(f"{BLOCKCHAIN_BASE_URL}/latestblock")
    return int(payload["height"])


def fetch_block(height):
    payload = get_json(
        f"{BLOCKCHAIN_BASE_URL}/block-height/{height}",
        params={"format": "json"},
        timeout=90,
    )
    blocks = payload.get("blocks") or []
    if not blocks:
        raise ValueError(f"{height} yüksekliğinde blok bulunamadı")
    main_chain = [block for block in blocks if block.get("main_chain", True)]
    return main_chain[0] if main_chain else blocks[0]


def fetch_btc_usd_price():
    payload = get_json(BINANCE_PRICE_URL, params={"symbol": "BTCUSDT"})
    price = float(payload["price"])
    if price <= 0:
        raise ValueError("geçersiz BTCUSDT fiyatı")
    return price


def ensure_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS whale_scan_state (
            chain TEXT PRIMARY KEY,
            last_height INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS whale_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chain TEXT NOT NULL,
            event_type TEXT NOT NULL,
            txid TEXT NOT NULL,
            output_index INTEGER NOT NULL,
            block_height INTEGER NOT NULL,
            block_time TEXT NOT NULL,
            asset TEXT NOT NULL,
            amount_native REAL NOT NULL,
            usd_estimate REAL NOT NULL,
            destination TEXT,
            direction TEXT NOT NULL,
            exchange_flow TEXT NOT NULL,
            source TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            UNIQUE(chain, txid, output_index)
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS ix_whale_events_block
        ON whale_events(chain, block_height)
    """)
    con.commit()


def last_scanned_height(con):
    row = con.execute(
        "SELECT last_height FROM whale_scan_state WHERE chain=?",
        (CHAIN,),
    ).fetchone()
    return int(row[0]) if row else None


def save_scan_height(con, height, observed_at):
    con.execute("""
        INSERT INTO whale_scan_state(chain, last_height, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(chain) DO UPDATE SET
            last_height=excluded.last_height,
            updated_at=excluded.updated_at
    """, (CHAIN, height, observed_at))
    con.commit()


def block_time_iso(block):
    timestamp = int(block.get("time") or 0)
    if timestamp <= 0:
        raise ValueError("blok zamanı bulunamadı")
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def extract_large_outputs(block, btc_usd):
    height = int(block["height"])
    time_iso = block_time_iso(block)
    events = []
    for transaction in block.get("tx") or []:
        txid = transaction.get("hash")
        if not txid:
            continue
        for output_index, output in enumerate(transaction.get("out") or []):
            satoshis = int(output.get("value") or 0)
            if satoshis <= 0:
                continue
            amount_btc = satoshis / SATOSHIS_PER_BTC
            usd_estimate = amount_btc * btc_usd
            if usd_estimate < USD_THRESHOLD:
                continue
            destination = output.get("addr") or output.get("hash")
            events.append({
                "chain": CHAIN,
                "event_type": "LARGE_CONFIRMED_OUTPUT",
                "txid": txid,
                "output_index": int(
                    output_index if output.get("n") is None else output["n"]
                ),
                "block_height": height,
                "block_time": time_iso,
                "asset": ASSET,
                "amount_native": amount_btc,
                "usd_estimate": usd_estimate,
                "destination": destination,
                "direction": "UNKNOWN",
                "exchange_flow": "UNVERIFIED",
                "source": "Blockchain.com Explorer + Binance BTCUSDT",
                "evidence_note": (
                    "Tekil blok çıktısıdır; change/self-transfer olabilir. "
                    "Cüzdan sahibi, borsa yönü ve işlem niyeti doğrulanmamıştır."
                ),
            })
    return events


def save_events(con, events, observed_at):
    inserted = 0
    for event in events:
        before = con.total_changes
        con.execute("""
            INSERT OR IGNORE INTO whale_events (
                chain, event_type, txid, output_index, block_height,
                block_time, asset, amount_native, usd_estimate, destination,
                direction, exchange_flow, source, observed_at, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event["chain"],
            event["event_type"],
            event["txid"],
            event["output_index"],
            event["block_height"],
            event["block_time"],
            event["asset"],
            event["amount_native"],
            event["usd_estimate"],
            event["destination"],
            event["direction"],
            event["exchange_flow"],
            event["source"],
            observed_at,
            json.dumps(event, ensure_ascii=False),
        ))
        inserted += con.total_changes - before
    con.commit()
    return inserted


def heights_to_scan(previous_height, latest_height):
    if previous_height is None:
        return [latest_height]
    if previous_height >= latest_height:
        return []
    end_height = min(latest_height, previous_height + MAX_BLOCKS_PER_RUN)
    return list(range(previous_height + 1, end_height + 1))


def main():
    observed_at = utcnow_iso()
    btc_usd = fetch_btc_usd_price()
    latest_height = fetch_latest_height()

    con = sqlite3.connect(DB_NAME)
    try:
        ensure_tables(con)
        previous_height = last_scanned_height(con)
        heights = heights_to_scan(previous_height, latest_height)
        scanned = 0
        detected = 0
        inserted = 0

        for height in heights:
            block = fetch_block(height)
            events = extract_large_outputs(block, btc_usd)
            inserted += save_events(con, events, observed_at)
            detected += len(events)
            scanned += 1
            save_scan_height(con, height, observed_at)
    finally:
        con.close()

    print("AG Crypto Analyst - Bitcoin Whale Collector v0.9.0")
    print(f"BTCUSDT: ${btc_usd:,.2f} | eşik: ${USD_THRESHOLD:,.0f}")
    print(
        f"Zincir ucu: {latest_height:,} | taranan blok: {scanned} | "
        f"tespit: {detected} | yeni kayıt: {inserted}"
    )
    if not heights:
        print("Yeni Bitcoin bloğu yok; önceki tarama yüksekliği korunuyor.")
    else:
        read_con = sqlite3.connect(DB_NAME)
        try:
            recent = read_con.execute("""
                SELECT txid, amount_native, usd_estimate, block_height
                FROM whale_events
                WHERE block_height BETWEEN ? AND ?
                ORDER BY usd_estimate DESC
                LIMIT 10
            """, (heights[0], heights[-1])).fetchall()
        finally:
            read_con.close()
        if recent:
            print("Büyük doğrulanmış çıktılar (yön/sahiplik bilinmiyor):")
            for txid, amount_btc, usd_estimate, height in recent:
                print(
                    f"- block={height:,} | {amount_btc:,.4f} BTC | "
                    f"~${usd_estimate:,.0f} | tx={txid[:16]}..."
                )
        else:
            print("Taranan bloklarda eşik üstü tekil BTC çıktısı bulunmadı.")
    print("Not: Büyük çıktı, balinanın alım/satım yaptığı veya borsaya para taşıdığı anlamına gelmez.")
    return {
        "latest_height": latest_height,
        "blocks_scanned": scanned,
        "detected": detected,
        "inserted": inserted,
        "btc_usd": btc_usd,
    }


if __name__ == "__main__":
    main()
