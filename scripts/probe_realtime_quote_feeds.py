"""Read-only, bounded, independent Binance USD-M quote-feed shape probe.

Never opens PAPER positions or writes AGCA files. Candidate feeds are inspected
only; no feed becomes an execution authority on this probe's result alone.
"""
from __future__ import annotations

import asyncio
import json
import ssl
import time
from collections import defaultdict

import certifi
import websockets


SYMBOLS = ("BTCUSDT", "ETHUSDT")
CHANNELS = ("ticker", "depth5@500ms")
DURATION_SECONDS = 12.0
WS_URL = "wss://fstream.binance.com/ws"


def sample_shape(message: dict) -> dict | None:
    """Report only schema, freshness and positive best bid/ask/quantity checks."""
    symbol = message.get("s")
    if symbol not in SYMBOLS:
        return None
    event = message.get("e")
    if event == "24hrTicker":
        values = {key: message.get(key) for key in ("b", "B", "a", "A")}
        try:
            valid = all(float(value) > 0 for value in values.values())
        except (ValueError, TypeError):
            valid = False
        return {
            "channel": "ticker",
            "symbol": symbol,
            "has_fields": {key: value is not None for key, value in values.items()},
            "positive_both_sides": valid,
            "snapshot": valid,
        }
    if event == "depthUpdate":
        # Distinguish full top-of-book snapshots from incremental diffs.
        # An incremental diff is NEVER an executable bid/ask quote.
        bids = message.get("bids")
        asks = message.get("asks")
        if bids is None and asks is None:
            bids, asks = message.get("b"), message.get("a")
        try:
            valid = (
                isinstance(bids, list) and isinstance(asks, list)
                and len(bids) > 0 and len(asks) > 0
                and all(float(row[0]) > 0 and float(row[1]) > 0
                        for row in (bids[0], asks[0]))
            )
        except (ValueError, TypeError, IndexError):
            valid = False
        return {
            "channel": "depth5@500ms",
            "symbol": symbol,
            "positive_both_sides": bool(valid),
            "bid_levels": len(bids) if isinstance(bids, list) else 0,
            "ask_levels": len(asks) if isinstance(asks, list) else 0,
            "schema_keys": sorted(message),
            "snapshot": bool(
                valid and (
                    isinstance(message.get("bids"), list)
                    and isinstance(message.get("asks"), list)
                )
            ),
        }
    return None


async def main() -> None:
    counts = defaultdict(int)
    positive = defaultdict(int)
    ages = defaultdict(list)
    shapes = {}
    params = [
        f"{symbol.lower()}@{channel}"
        for symbol in SYMBOLS
        for channel in CHANNELS
    ]
    start = time.monotonic()
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        async with websockets.connect(
            WS_URL, ssl=context, open_timeout=8, max_queue=128,
        ) as socket:
            await socket.send(json.dumps({
                "method": "SUBSCRIBE", "params": params, "id": 1,
            }))
            start = time.monotonic()
            while time.monotonic() - start < DURATION_SECONDS:
                remaining = DURATION_SECONDS - (time.monotonic() - start)
                if remaining <= 0:
                    break
                try:
                    async with asyncio.timeout(min(2, remaining)):
                        raw = await socket.recv()
                except TimeoutError:
                    continue
                received = time.time() * 1000
                message = json.loads(raw)
                if message.get("code") is not None:
                    print("EXCHANGE ERROR:", message.get("code"))
                    continue
                if "result" in message:
                    print("SUBSCRIPTION ACK:", message.get("result"), flush=True)
                    continue
                shape = sample_shape(message)
                if shape is None:
                    continue
                key = (shape["symbol"], shape["channel"])
                counts[key] += 1
                if shape["positive_both_sides"]:
                    positive[key] += 1
                if key not in shapes:
                    shapes[key] = shape
                try:
                    ages[key].append(round((received - int(message["E"])) / 1000, 3))
                except (KeyError, ValueError, TypeError):
                    pass
    except Exception as exc:
        print("CONNECTION ERROR:", type(exc).__name__, str(exc)[:180], flush=True)
    finally:
        print("=== READ-ONLY FUTURES CANDIDATE FEED PROBE ===")
        print("DURATION_SECONDS:", round(time.monotonic() - start, 1))
        for symbol in SYMBOLS:
            for channel in CHANNELS:
                key = (symbol, channel)
                observed = ages[key]
                print(
                    symbol, channel,
                    "messages=", counts[key],
                    "positive_sides=", positive[key],
                    "min_event_age_s=", min(observed) if observed else None,
                    "max_event_age_s=", max(observed) if observed else None,
                    "sample_schema=", shapes.get(key),
                    flush=True,
                )
        print(
            "IMPORTANT: depthUpdate with incremental b/a fields is NOT a full "
            "book snapshot and must not become PAPER price authority."
        )


if __name__ == "__main__":
    asyncio.run(main())
