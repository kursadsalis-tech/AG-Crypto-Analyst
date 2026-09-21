"""Bounded Binance USD-M liquidation snapshots for PAPER market observation.

Binance's !forceOrder@arr publishes at most ONE liquidation order per symbol
per 1000ms. These observations are not an exhaustive liquidation tape.
No foreign symbol conversion and no inference that silence means no liquidations.
"""
from __future__ import annotations

import asyncio
import json
import ssl
import time

import certifi
import websockets


VERSION = "BINANCE_USDM_LIQUIDATION_OBSERVER_V1"
WS_URL = "wss://fstream.binance.com/public/ws"
STREAM = "!forceOrder@arr"
MAX_EVENT_AGE_MS = 2_000
MAX_EVENTS = 5_000


def normalize_event(payload, received_ms):
    """Return Binance-native executed notional or reject an unusable event."""
    if not isinstance(payload, dict) or payload.get("e") != "forceOrder":
        return None
    if payload.get("st", 1) != 1:
        return None
    order = payload.get("o")
    if not isinstance(order, dict):
        return None
    symbol = order.get("s")
    if not isinstance(symbol, str) or not symbol.endswith("USDT"):
        return None
    if payload.get("ps", symbol) not in (symbol, None):
        return None
    try:
        stamp = int(order.get("T"))
        event_stamp = int(payload.get("E"))
        price = float(order.get("ap") or 0)
        quantity = float(order.get("l") or 0)
    except (ValueError, TypeError, OverflowError):
        return None
    if (not 0 <= received_ms-stamp <= MAX_EVENT_AGE_MS
            or not 0 <= received_ms-event_stamp <= MAX_EVENT_AGE_MS):
        return None
    import math
    if not (math.isfinite(price) and math.isfinite(quantity)
            and price > 0 and quantity > 0):
        return None
    side = order.get("S")
    if side not in ("SELL", "BUY"):
        return None
    return {
        "instrument": symbol,
        "timestamp_ms": stamp,
        "liquidated_side": "LONG" if side == "SELL" else "SHORT",
        "bankruptcy_price": price,
        # Binance USD-M quantity is native base-asset quantity, not OKX lots.
        "contracts": quantity,
        "notional_usd_estimate": price*quantity,
        "source_exchange": "BINANCE_USD_M",
        "coverage": "LATEST_PER_SYMBOL_PER_1000MS_NOT_EXHAUSTIVE",
    }


def classify(*, subscribed, errors, events, overflow=False):
    if errors:
        state = "CHANNEL_ERROR"
    elif overflow:
        state = "CHANNEL_OVERFLOW"
    elif not subscribed:
        state = "SUBSCRIPTION_NOT_CONFIRMED"
    elif events:
        state = "AVAILABLE_EVENTS_OBSERVED"
    else:
        state = "AVAILABLE_NO_EVENT_IN_WINDOW"
    return {
        "state": state, "subscription_confirmed": subscribed,
        "error_count": len(errors), "normalized_event_count": len(events),
    }


async def probe(duration_seconds=8, contract_values=None):
    """Observe only; do not assert no liquidations when the socket is silent."""
    context = ssl.create_default_context(cafile=certifi.where())
    events, errors = [], []
    subscribed, overflow = False, False
    try:
        async with websockets.connect(
            WS_URL, ssl=context, open_timeout=10,
            ping_interval=20, ping_timeout=10, max_queue=256,
        ) as socket:
            await socket.send(json.dumps(
                {"method": "SUBSCRIBE", "params": [STREAM], "id": 1},
            ))
            deadline = time.monotonic() + max(1, duration_seconds)
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(
                        socket.recv(), timeout=min(1., max(0.01, deadline-time.monotonic()))
                    )
                except asyncio.TimeoutError:
                    continue
                received_ms = int(time.time()*1000)
                row = json.loads(raw)
                if not isinstance(row, dict):
                    continue
                if row.get("id") == 1 and row.get("result") is None:
                    subscribed = True
                    continue
                if row.get("code") is not None:
                    errors.append(str(row.get("code")))
                    break
                event = normalize_event(row, received_ms)
                if event is None:
                    continue
                if len(events) >= MAX_EVENTS:
                    overflow = True
                    break
                events.append(event)
    except (OSError, asyncio.TimeoutError, ValueError, RuntimeError,
            websockets.WebSocketException) as exc:
        errors.append(type(exc).__name__)
    state = classify(subscribed=subscribed, errors=errors, events=events,
                     overflow=overflow)
    # Keep existing bundle contract; all instrument identities are native.
    return {
        "version": VERSION, "provider": "BINANCE_USD_M",
        "channel": STREAM,
        "observation_window_seconds": duration_seconds,
        "coverage": "MAX_ONE_LIQUIDATION_PER_SYMBOL_PER_SECOND",
        **state, "events": events, "raw_messages_persisted": False,
        "trade_signal_generated": False, "paper_only_changed": False,
        "trading_authority_changed": False,
    }
