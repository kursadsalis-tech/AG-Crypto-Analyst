"""Collect large transfers for a verified Ethereum ERC-20 allowlist.

The collector reads finalized-enough (``safe``) Ethereum logs directly from a
JSON-RPC endpoint. Contract identity and decimals are allowlisted, and decimals
are checked on-chain before a scan advances. Wallet ownership, exchange flow,
and buy/sell intent remain unknown.
"""
from __future__ import annotations

import json
import sqlite3
from database.runtime_store import connect as runtime_connect
from datetime import datetime, timezone

import requests


DB_NAME = "crypto_data.db"
ETHEREUM_RPC_URL = "https://ethereum-rpc.publicnode.com"
BINANCE_PRICE_URL = "https://data-api.binance.vision/api/v3/ticker/price"
USER_AGENT = "AG-Crypto-Analyst/0.9.0"
CHAIN = "Ethereum"
SCAN_KEY = "EthereumERC20"
USD_THRESHOLD = 10_000_000.0
MAX_BLOCKS_PER_RUN = 40
LOG_BLOCK_CHUNK = 20
TOKEN_OUTPUT_INDEX_BASE = 1_000_000
TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa"
    "952ba7f163c4a11628f55a4df523b3ef"
)
DECIMALS_SELECTOR = "0x313ce567"


# Only verified Ethereum-mainnet contracts from the product watchlist belong
# here. Expected decimals are also checked with decimals() before each due scan.
TOKEN_CONFIGS = {
    "0x6e2a43be0b1d33b726f0ca3b8de60b3482b8b050": {
        "symbol": "ARKM",
        "decimals": 18,
        "price_symbol": "ARKMUSDT",
        "contract_source": (
            "https://etherscan.io/token/"
            "0x6e2a43be0b1d33b726f0ca3b8de60b3482b8b050"
        ),
    },
    "0xcb1592591996765ec0efc1f92599a19767ee5ffa": {
        "symbol": "BIO",
        "decimals": 18,
        "price_symbol": "BIOUSDT",
        "contract_source": (
            "https://docs.bio.xyz/bio/introduction/bio-token/"
            "basic-token-information"
        ),
    },
    "0x6dea81c8171d0ba574754ef6f8b412f2ed88c54d": {
        "symbol": "LQTY",
        "decimals": 18,
        "price_symbol": "LQTYUSDT",
        "contract_source": (
            "https://etherscan.io/token/"
            "0x6dea81c8171d0ba574754ef6f8b412f2ed88c54d"
        ),
    },
    "0xfe0c30065b384f05761f15d0cc899d4f9f9cc0eb": {
        "symbol": "ETHFI",
        "decimals": 18,
        "price_symbol": "ETHFIUSDT",
        "contract_source": (
            "https://etherscan.io/token/"
            "0xfe0c30065b384f05761f15d0cc899d4f9f9cc0eb"
        ),
    },
    "0x767fe9edc9e0df98e07454847909b5e959d7ca0e": {
        "symbol": "ILV",
        "decimals": 18,
        "price_symbol": "ILVUSDT",
        "contract_source": (
            "https://etherscan.io/token/"
            "0x767fe9edc9e0df98e07454847909b5e959d7ca0e"
        ),
    },
    "0xcc8fa225d80b9c7d42f96e9570156c65d6caaa25": {
        "symbol": "SLP",
        "decimals": 0,
        "price_symbol": "SLPUSDT",
        "contract_source": (
            "https://etherscan.io/token/"
            "0xcc8fa225d80b9c7d42f96e9570156c65d6caaa25"
        ),
    },
    "0x04fa0d235c4abf4bcf4787af4cf447de572ef828": {
        "symbol": "UMA",
        "decimals": 18,
        "price_symbol": "UMAUSDT",
        "contract_source": (
            "https://etherscan.io/token/"
            "0x04fa0d235c4abf4bcf4787af4cf447de572ef828"
        ),
    },
}


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


def fetch_token_prices():
    price_symbols = [config["price_symbol"] for config in TOKEN_CONFIGS.values()]
    response = requests.get(
        BINANCE_PRICE_URL,
        params={"symbols": json.dumps(price_symbols, separators=(",", ":"))},
        timeout=30,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("Binance ERC-20 fiyat yanıtı liste değil")

    by_market = {}
    for item in payload:
        price = float(item["price"])
        if price > 0:
            by_market[item["symbol"]] = price

    missing = sorted(set(price_symbols) - set(by_market))
    if missing:
        raise RuntimeError("eksik ERC-20 fiyatları: " + ", ".join(missing))
    return {
        config["symbol"]: by_market[config["price_symbol"]]
        for config in TOKEN_CONFIGS.values()
    }


def validate_contract_decimals(block_height):
    block_tag = hex(block_height)
    validated = {}
    for address, config in TOKEN_CONFIGS.items():
        raw = rpc_call(
            "eth_call",
            [{"to": address, "data": DECIMALS_SELECTOR}, block_tag],
        )
        try:
            actual = int(raw, 16)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"{config['symbol']} decimals() çözümlenemedi: {raw!r}"
            ) from exc
        expected = int(config["decimals"])
        if actual != expected:
            raise RuntimeError(
                f"{config['symbol']} decimals uyuşmazlığı: "
                f"beklenen={expected}, zincir={actual}"
            )
        validated[config["symbol"]] = actual
    return validated


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
        (SCAN_KEY,),
    ).fetchone()
    return int(row[0]) if row else None


def save_scan_height(con, height, observed_at):
    con.execute("""
        INSERT INTO whale_scan_state(chain, last_height, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(chain) DO UPDATE SET
            last_height=excluded.last_height,
            updated_at=excluded.updated_at
    """, (SCAN_KEY, height, observed_at))
    con.commit()


def heights_to_scan(previous_height, safe_height):
    if previous_height is None:
        return [safe_height]
    if previous_height >= safe_height:
        return []
    end_height = min(safe_height, previous_height + MAX_BLOCKS_PER_RUN)
    return list(range(previous_height + 1, end_height + 1))


def chunk_ranges(heights):
    if not heights:
        return []
    ranges = []
    start = heights[0]
    while start <= heights[-1]:
        end = min(start + LOG_BLOCK_CHUNK - 1, heights[-1])
        ranges.append((start, end))
        start = end + 1
    return ranges


def fetch_transfer_logs(start_height, end_height):
    return rpc_call(
        "eth_getLogs",
        [{
            "fromBlock": hex(start_height),
            "toBlock": hex(end_height),
            "address": list(TOKEN_CONFIGS),
            "topics": [TRANSFER_TOPIC],
        }],
        timeout=60,
    )


def topic_address(topic):
    if not isinstance(topic, str) or len(topic) != 66:
        raise ValueError(f"geçersiz adres topic'i: {topic!r}")
    return "0x" + topic[-40:].lower()


def parse_candidate(log, token_prices):
    address = str(log.get("address") or "").lower()
    config = TOKEN_CONFIGS.get(address)
    topics = log.get("topics") or []
    if (
        config is None
        or len(topics) < 3
        or str(topics[0]).lower() != TRANSFER_TOPIC
    ):
        return None

    try:
        raw_amount = int(log.get("data") or "0x0", 16)
        block_height = int(log["blockNumber"], 16)
        log_index = int(log["logIndex"], 16)
    except (KeyError, TypeError, ValueError):
        return None
    if raw_amount <= 0:
        return None

    symbol = config["symbol"]
    amount_native = raw_amount / (10 ** int(config["decimals"]))
    token_usd = float(token_prices[symbol])
    usd_estimate = amount_native * token_usd
    if usd_estimate < USD_THRESHOLD:
        return None

    txid = log.get("transactionHash")
    if not txid:
        return None
    try:
        from_address = topic_address(topics[1])
        destination = topic_address(topics[2])
    except ValueError:
        return None

    return {
        "chain": CHAIN,
        "event_type": "LARGE_ERC20_TRANSFER",
        "txid": txid,
        # The existing event key shares a namespace with native transfers.
        # Offset the log index so a transaction's log 0 cannot collide with
        # the native collector's output_index=0 record.
        "output_index": TOKEN_OUTPUT_INDEX_BASE + log_index,
        "erc20_log_index": log_index,
        "block_height": block_height,
        "asset": symbol,
        "amount_native": amount_native,
        "usd_estimate": usd_estimate,
        "token_usd": token_usd,
        "destination": destination,
        "direction": "UNKNOWN",
        "exchange_flow": "UNVERIFIED",
        "source": "Ethereum eth_getLogs (PublicNode) + Binance spot price",
        "from_address": from_address,
        "contract_address": address,
        "contract_source": config["contract_source"],
        "decimals": int(config["decimals"]),
        "decimals_verified_onchain": True,
        "evidence_note": (
            "Doğrulanmış kontratta ERC-20 Transfer olayıdır; mint, burn, "
            "self-transfer veya sözleşme içi hareket olabilir. Cüzdan sahibi, "
            "borsa yönü ve işlem niyeti doğrulanmamıştır."
        ),
    }


def fetch_block_time(height, cache):
    if height not in cache:
        block = rpc_call("eth_getBlockByNumber", [hex(height), False])
        if int(block["number"], 16) != height:
            raise RuntimeError(
                f"beklenmeyen Ethereum blok yüksekliği: {block['number']}"
            )
        timestamp = int(block["timestamp"], 16)
        cache[height] = datetime.fromtimestamp(
            timestamp, tz=timezone.utc
        ).isoformat()
    return cache[height]


def extract_large_transfers(logs, token_prices, block_time_cache=None):
    cache = block_time_cache if block_time_cache is not None else {}
    events = []
    for log in logs:
        event = parse_candidate(log, token_prices)
        if event is None:
            continue
        event["block_time"] = fetch_block_time(event["block_height"], cache)
        events.append(event)
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

    con = runtime_connect(DB_NAME)
    try:
        ensure_tables(con)
        previous_height = last_scanned_height(con)
        heights = heights_to_scan(previous_height, safe_height)
        scanned = 0
        detected = 0
        inserted = 0
        token_prices = None
        validated_decimals = None
        block_time_cache = {}

        if heights:
            token_prices = fetch_token_prices()
            validated_decimals = validate_contract_decimals(safe_height)
            for start_height, end_height in chunk_ranges(heights):
                logs = fetch_transfer_logs(start_height, end_height)
                events = extract_large_transfers(
                    logs, token_prices, block_time_cache
                )
                inserted += save_events(con, events, observed_at)
                detected += len(events)
                scanned += end_height - start_height + 1
                save_scan_height(con, end_height, observed_at)
    finally:
        con.close()

    print("AG Crypto Analyst - Ethereum ERC-20 Whale Collector v0.9.0")
    print(
        f"İzlenen doğrulanmış kontrat: {len(TOKEN_CONFIGS)} | "
        f"eşik: ${USD_THRESHOLD:,.0f}"
    )
    if validated_decimals is not None:
        print(
            "On-chain decimals doğrulandı: "
            + ", ".join(sorted(validated_decimals))
        )
    print(
        f"Güvenli zincir ucu: {safe_height:,} | taranan blok: {scanned} | "
        f"tespit: {detected} | yeni kayıt: {inserted}"
    )
    if not heights:
        print("Yeni güvenli Ethereum bloğu yok; ERC-20 tarama yüksekliği korunuyor.")
    else:
        read_con = runtime_connect(DB_NAME)
        try:
            recent = read_con.execute("""
                SELECT txid, asset, amount_native, usd_estimate, block_height
                FROM whale_events
                WHERE chain=?
                  AND event_type='LARGE_ERC20_TRANSFER'
                  AND block_height BETWEEN ? AND ?
                ORDER BY usd_estimate DESC
                LIMIT 10
            """, (CHAIN, heights[0], heights[-1])).fetchall()
        finally:
            read_con.close()
        if recent:
            print("Büyük ERC-20 transferleri (yön/sahiplik bilinmiyor):")
            for txid, asset, amount, usd_estimate, height in recent:
                print(
                    f"- block={height:,} | {amount:,.4f} {asset} | "
                    f"~${usd_estimate:,.0f} | tx={txid[:18]}..."
                )
        else:
            print("Taranan güvenli bloklarda eşik üstü izlenen ERC-20 transferi yok.")
    print(
        "Not: Transfer olayı alım/satım veya borsa akışı anlamına gelmez; "
        "doğrulanmamış olay alarm üretmez."
    )
    return {
        "safe_height": safe_height,
        "blocks_scanned": scanned,
        "detected": detected,
        "inserted": inserted,
        "tracked_tokens": len(TOKEN_CONFIGS),
        "token_prices": token_prices,
    }


if __name__ == "__main__":
    main()
