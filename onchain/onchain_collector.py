"""AG Crypto Analyst v0.7.0 - first working on-chain data collector.

Collects keyless public Bitcoin mempool/fee metrics and chain-level DeFi TVL.
This module stores observations only; it intentionally makes no trade decision.
"""
from __future__ import annotations

import json
import sqlite3
from database.runtime_store import connect as runtime_connect
from datetime import datetime, timezone

import requests


DB_NAME = "crypto_data.db"
MEMPOOL_BASE_URL = "https://mempool.space/api"
DEFILLAMA_CHAINS_URL = "https://api.llama.fi/v2/chains"
USER_AGENT = "AG-Crypto-Analyst/0.7.0"
TRACKED_CHAINS = {
    "Ethereum",
    "Solana",
    "BSC",
    "Arbitrum",
    "Base",
    "Avalanche",
    "Sui",
    "Celestia",
}


def utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


def get_json(url):
    response = requests.get(
        url,
        timeout=20,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    return response.json()


def ensure_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS onchain_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at TEXT NOT NULL,
            source TEXT NOT NULL,
            chain TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            unit TEXT NOT NULL,
            raw_json TEXT
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS ix_onchain_chain_metric_time
        ON onchain_observations(chain, metric, collected_at)
    """)
    con.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_onchain_observation_identity
        ON onchain_observations(source, chain, metric, collected_at)
    """)
    con.commit()


def bitcoin_metrics():
    mempool = get_json(f"{MEMPOOL_BASE_URL}/mempool")
    fees = get_json(f"{MEMPOOL_BASE_URL}/v1/fees/recommended")
    raw = json.dumps({"mempool": mempool, "fees": fees}, ensure_ascii=False)
    return [
        ("mempool.space", "Bitcoin", "mempool_tx_count", mempool["count"], "transactions", raw),
        ("mempool.space", "Bitcoin", "mempool_vsize", mempool["vsize"], "vbytes", raw),
        ("mempool.space", "Bitcoin", "mempool_total_fee", mempool["total_fee"], "sats", raw),
        ("mempool.space", "Bitcoin", "fastest_fee", fees["fastestFee"], "sat/vB", raw),
        ("mempool.space", "Bitcoin", "half_hour_fee", fees["halfHourFee"], "sat/vB", raw),
        ("mempool.space", "Bitcoin", "hour_fee", fees["hourFee"], "sat/vB", raw),
        ("mempool.space", "Bitcoin", "economy_fee", fees["economyFee"], "sat/vB", raw),
    ]


def chain_tvl_metrics():
    rows = get_json(DEFILLAMA_CHAINS_URL)
    metrics = []
    for item in rows:
        chain = item.get("name")
        tvl = item.get("tvl")
        if chain not in TRACKED_CHAINS or tvl is None:
            continue
        metrics.append((
            "DefiLlama",
            chain,
            "defi_tvl_usd",
            float(tvl),
            "USD",
            json.dumps(item, ensure_ascii=False),
        ))
    return metrics


def save_metrics(con, collected_at, metrics):
    con.executemany("""
        INSERT INTO onchain_observations (
            collected_at, source, chain, metric, value, unit, raw_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [
        (collected_at, source, chain, metric, float(value), unit, raw_json)
        for source, chain, metric, value, unit, raw_json in metrics
    ])
    con.commit()


def main():
    collected_at = utcnow_iso()
    metrics = []
    errors = []

    try:
        btc = bitcoin_metrics()
        metrics.extend(btc)
        print(f"mempool.space: {len(btc)} Bitcoin metriği alındı")
    except Exception as exc:
        errors.append(f"mempool.space: {exc}")
        print(f"mempool.space: HATA -> {exc}")

    try:
        tvl = chain_tvl_metrics()
        metrics.extend(tvl)
        print(f"DefiLlama: {len(tvl)} zincir TVL metriği alındı")
    except Exception as exc:
        errors.append(f"DefiLlama: {exc}")
        print(f"DefiLlama: HATA -> {exc}")

    con = runtime_connect(DB_NAME)
    try:
        ensure_table(con)
        if metrics:
            save_metrics(con, collected_at, metrics)
    finally:
        con.close()

    print("\nAG Crypto Analyst - On-chain Collector v0.7.0")
    print(f"Kayıt zamanı: {collected_at}")
    print(f"Kaydedilen metrik: {len(metrics)} | Kaynak hatası: {len(errors)}")
    for source, chain, metric, value, unit, _ in metrics:
        if metric == "defi_tvl_usd":
            shown = f"${value:,.0f}"
        else:
            shown = f"{value:,.0f} {unit}"
        print(f"{chain:<10} | {metric:<22} | {shown}")

    return {"collected": len(metrics), "errors": errors}


if __name__ == "__main__":
    main()
