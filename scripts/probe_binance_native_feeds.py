#!/usr/bin/env python3
"""Read-only bounded acceptance probe for Binance-native USD-M WebSocket feeds.

No REST, no credentials, no DB access or writes, no order paths. This script
MUST NOT be treated as proof that a full market observer is production-ready.
Use it on the execution VM to assess an allowed feed before changing services.
"""
from __future__ import annotations

import asyncio
import json
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path

import certifi
import websockets


PROBES = (
    ("all_book", "wss://fstream.binance.com/public/ws/!bookTicker", "bookTicker"),
    ("all_mini", "wss://fstream.binance.com/market/ws/!miniTicker@arr", "24hrMiniTicker"),
    ("depth20", "wss://fstream.binance.com/public/ws/btcusdt@depth20@500ms", "depthUpdate"),
    ("btc_trade", "wss://fstream.binance.com/market/ws/btcusdt@aggTrade", "aggTrade"),
    ("btc_mark", "wss://fstream.binance.com/market/ws/btcusdt@markPrice@1s", "markPriceUpdate"),
    ("liquidations", "wss://fstream.binance.com/market/ws/!forceOrder@arr", "forceOrder"),
)


def inspect_message(payload, expected, received_ms):
    """Return only safe, aggregate telemetry, never an unaudited raw payload."""
    if isinstance(payload, dict) and "data" in payload and "stream" in payload:
        payload = payload["data"]
    rows = payload if isinstance(payload, list) else [payload]
    count, latest_age, sample = 0, None, set()
    for row in rows:
        if not isinstance(row, dict) or row.get("e") != expected:
            continue
        if row.get("st", 1) != 1:
            continue
        symbol = (row.get("o") or {}).get("s") if expected == "forceOrder" else row.get("s")
        if not isinstance(symbol, str) or not symbol.endswith("USDT"):
            continue
        try:
            age = received_ms - int(row["E"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not 0 <= age <= 5000:
            continue
        count += 1
        latest_age = min(age, latest_age) if latest_age is not None else age
        if len(sample) < 4:
            sample.add(symbol)
    return {"valid_rows": count, "min_event_age_ms": latest_age,
            "sample_symbols": sorted(sample)}


async def probe_one(label, url, expected, duration_seconds=8):
    start = time.monotonic()
    total = 0
    samples = set()
    min_age = None
    messages = 0
    error = None
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        async with websockets.connect(
            url, open_timeout=4, close_timeout=1,
            ping_interval=20, ping_timeout=10, ssl=context,
            max_queue=32, max_size=2**20,
        ) as socket:
            while time.monotonic() - start < duration_seconds and messages < 30:
                remaining = duration_seconds - (time.monotonic() - start)
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=max(.05, remaining))
                except asyncio.TimeoutError:
                    break
                received_ms = int(time.time() * 1000)
                messages += 1
                value = inspect_message(json.loads(raw), expected, received_ms)
                total += value["valid_rows"]
                samples.update(value["sample_symbols"])
                if value["min_event_age_ms"] is not None:
                    min_age = min(value["min_event_age_ms"], min_age) if min_age is not None else value["min_event_age_ms"]
    except (OSError, asyncio.TimeoutError, ValueError, RuntimeError,
            websockets.WebSocketException) as exc:
        error = type(exc).__name__
    return {"stream": label, "messages": messages, "valid_rows": total,
            "sample_symbols": sorted(samples)[:8], "min_event_age_ms": min_age,
            "state": "ERROR" if error else "FRESH" if total else "NO_VALID_EVENT",
            "error_type": error}


def metadata_snapshot(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        stamp = datetime.fromisoformat(data["fetched_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
        symbol_rows = data["payload"]["symbols"]
        symbols = {r["symbol"] for r in symbol_rows if
                   r.get("status") == "TRADING" and
                   r.get("contractType") == "PERPETUAL" and
                   r.get("quoteAsset") == "USDT"}
        return {"state": "FRESH" if 0 <= age <= 7200 else "STALE",
                "age_seconds": round(age), "eligible_contract_count": len(symbols),
                "btc_present": "BTCUSDT" in symbols,
                "pepe_native_present": "1000PEPEUSDT" in symbols,
                "pepe_alias_present": "PEPEUSDT" in symbols}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"state": "INVALID", "error_type": type(exc).__name__}


async def main():
    diagnostics = await asyncio.gather(*(probe_one(*p) for p in PROBES))
    print(json.dumps({"mode": "READ_ONLY_NO_REST_NO_ORDERS",
                      "metadata": metadata_snapshot("/var/lib/agca/exchange-info-usdm.json"),
                      "streams": diagnostics,
                      "note": "NO_VALID_EVENT on sparse liquidation stream is not an outage or proof of zero liquidations",
                      }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
