"""Collect large top-level native ETH transfers from safe Ethereum blocks.

This collector does not see internal contract transfers or ERC-20 Transfer
events. It also does not infer wallet ownership, exchange flow, or intent.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import requests


DB_NAME = "crypto_data.db"
ETHEREUM_RPC_URL = "https://ethereum-rpc.publicnode.com"
BINANCE_PRICE_URL = "https://data-api.binance.vision/api/v3/ticker/price"
USER_AGENT = "AG-Crypto-Analyst/0.9.0"
CHAIN = "Ethereum"
ASSET = "ETH"
WEI_PER_ETH = 10**18
USD_THRESHOLD = 10_000_000.0
MAX_BLOCKS_PER_RUN = 40


def utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


def rpc_call(method, params, timeout=45):
    response = requests.post(
        ETHEREUM_RPC_URL,
        json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        error = payload["error"]
        raise RuntimeError(
            f"Ethereum RPC {method} hatası: "
            f"{error.get('code')} {error.get('message')}"
        )
    if payload.get("result") is None:
        raise RuntimeError(f"Ethereum RPC {method} boş sonuç döndürdü")
    return payload["result"]


def fetch_safe_height():
    block = rpc_call("eth_getBlockByNumber", ["safe", False])
    return int(block["number"], 16)


def fetch_block(height):
    block = rpc_call("eth_getBlockByNumber", [hex(height), True], timeout=60)
    if int(block["number"], 16) != height:
        raise RuntimeError(f"beklenmeyen Ethereum blok yüksekliği: {block['number']}")
    return block


def fetch_eth_usd_price():
    response = requests.get(
        BINANCE_PRICE_URL,
        params={"symbol": "ETHUSDT"},
        timeout=30,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    price = float(response.json()["price"])
    if price <= 0:
        raise ValueError("geçersiz ETHUSDT fiyatı")
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


def heights_to_scan(previous_height, safe_height):
    if previous_height is None:
        return [safe_height]
    if previous_height >= safe_height:
        return []
    end_height = min(safe_height, previous_height + MAX_BLOCKS_PER_RUN)
    return list(range(previous_height + 1, end_height + 1))


def block_time_iso(block):
    timestamp = int(block["timestamp"], 16)
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def extract_large_transfers(block, eth_usd):
    height = int(block["number"], 16)
    time_iso = block_time_iso(block)
    events = []
    for transaction in block.get("transactions") or []:
        wei = int(transaction.get("value") or "0x0", 16)
        if wei <= 0:
            continue
        amount_eth = wei / WEI_PER_ETH
        usd_estimate = amount_eth * eth_usd
        if usd_estimate < USD_THRESHOLD:
            continue
        txid = transaction.get("hash")
        if not txid:
            continue
        events.append({
            "chain": CHAIN,
            "event_type": "LARGE_NATIVE_TRANSFER",
            "txid": txid,
            "output_index": 0,
            "block_height": height,
            "block_time": time_iso,
            "asset": ASSET,
            "amount_native": amount_eth,
            "usd_estimate": usd_estimate,
            "destination": transaction.get("to"),
            "direction": "UNKNOWN",
            "exchange_flow": "UNVERIFIED",
            "source": "Ethereum JSON-RPC (PublicNode) + Binance ETHUSDT",
            "from_address": transaction.get("from"),
            "evidence_note": (
                "Üst seviye native ETH transferidir; internal/ERC-20 hareketi değildir. "
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


def main():
    observed_at = utcnow_iso()
    safe_height = fetch_safe_height()

    con = sqlite3.connect(DB_NAME)
    try:
        ensure_tables(con)
        previous_height = last_scanned_height(con)
        heights = heights_to_scan(previous_height, safe_height)
        scanned = 0
        detected = 0
        inserted = 0
        eth_usd = fetch_eth_usd_price() if heights else None

        for height in heights:
            block = fetch_block(height)
            events = extract_large_transfers(block, eth_usd)
            inserted += save_events(con, events, observed_at)
            detected += len(events)
            scanned += 1
            save_scan_height(con, height, observed_at)
    finally:
        con.close()

    print("AG Crypto Analyst - Ethereum Whale Collector v0.9.0")
    if eth_usd is not None:
        print(f"ETHUSDT: ${eth_usd:,.2f} | eşik: ${USD_THRESHOLD:,.0f}")
    print(
        f"Güvenli zincir ucu: {safe_height:,} | taranan blok: {scanned} | "
        f"tespit: {detected} | yeni kayıt: {inserted}"
    )
    if not heights:
        print("Yeni güvenli Ethereum bloğu yok; önceki tarama yüksekliği korunuyor.")
    else:
        read_con = sqlite3.connect(DB_NAME)
        try:
            recent = read_con.execute("""
                SELECT txid, amount_native, usd_estimate, block_height
                FROM whale_events
                WHERE chain=?
                  AND event_type='LARGE_NATIVE_TRANSFER'
                  AND block_height BETWEEN ? AND ?
                ORDER BY usd_estimate DESC
                LIMIT 10
            """, (CHAIN, heights[0], heights[-1])).fetchall()
        finally:
            read_con.close()
        if recent:
            print("Büyük native ETH transferleri (yön/sahiplik bilinmiyor):")
            for txid, amount_eth, usd_estimate, height in recent:
                print(
                    f"- block={height:,} | {amount_eth:,.4f} ETH | "
                    f"~${usd_estimate:,.0f} | tx={txid[:18]}..."
                )
        else:
            print("Taranan güvenli bloklarda eşik üstü native ETH transferi bulunmadı.")
    print(
        "Not: Bu modül internal transferleri taramaz; ERC-20 hareketleri ayrı "
        "doğrulanmış-kontrat collectoründe izlenir. Doğrulanmamış olay alarm üretmez."
    )
    return {
        "safe_height": safe_height,
        "blocks_scanned": scanned,
        "detected": detected,
        "inserted": inserted,
        "eth_usd": eth_usd,
    }


if __name__ == "__main__":
    main()
