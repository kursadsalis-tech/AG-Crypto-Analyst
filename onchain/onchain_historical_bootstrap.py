"""One-time recent TVL history bootstrap for On-chain Analyzer validation."""
from __future__ import annotations

import json
import sqlite3
from database.runtime_store import connect as runtime_connect
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from onchain.onchain_collector import (
    DB_NAME,
    TRACKED_CHAINS,
    USER_AGENT,
    ensure_table,
)

import requests


DEFILLAMA_HISTORY_BASE = "https://api.llama.fi/v2/historicalChainTvl"
LOOKBACK_DAYS = 7


def fetch_chain_history(chain):
    url = f"{DEFILLAMA_HISTORY_BASE}/{quote(chain, safe='')}"
    response = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    return response.json()


def recent_rows(chain, history, now):
    cutoff = now - timedelta(days=LOOKBACK_DAYS + 1)
    rows = []
    for item in history:
        timestamp = item.get("date")
        tvl = item.get("tvl")
        if timestamp is None or tvl is None:
            continue
        observed_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        if observed_at < cutoff:
            continue
        rows.append((
            observed_at.isoformat(),
            "DefiLlama",
            chain,
            "defi_tvl_usd",
            float(tvl),
            "USD",
            json.dumps(item, ensure_ascii=False),
        ))
    return rows


def main():
    now = datetime.now(timezone.utc)
    all_rows = []
    errors = []
    for chain in sorted(TRACKED_CHAINS):
        try:
            rows = recent_rows(chain, fetch_chain_history(chain), now)
            all_rows.extend(rows)
            print(f"{chain}: {len(rows)} tarihsel TVL gözlemi hazırlandı")
        except Exception as exc:
            errors.append(f"{chain}: {exc}")
            print(f"{chain}: HATA -> {exc}")

    con = runtime_connect(DB_NAME)
    try:
        ensure_table(con)
        before = con.total_changes
        con.executemany("""
            INSERT OR IGNORE INTO onchain_observations (
                collected_at, source, chain, metric, value, unit, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, all_rows)
        con.commit()
        inserted = con.total_changes - before
    finally:
        con.close()

    print("\nAG Crypto Analyst - On-chain Historical Bootstrap")
    print(f"Hazırlanan: {len(all_rows)} | Yeni kaydedilen: {inserted} | Hata: {len(errors)}")
    return {"prepared": len(all_rows), "inserted": inserted, "errors": errors}


if __name__ == "__main__":
    main()
