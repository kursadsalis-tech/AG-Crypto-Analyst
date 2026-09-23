"""Observe Binance USD-M all-market liquidation orders in memory only."""
from __future__ import annotations

import asyncio
import json
import ssl
import time
from datetime import datetime, timezone

import certifi
import websockets


VERSION = "BINANCE_LIQUIDATION_CHANNEL_V2_ROUTED"
WS_URL = "wss://fstream.binance.com/market/ws/!forceOrder@arr"


def _instrument(symbol):
    value = str(symbol).upper()
    if not value.endswith("USDT"):
        return None
    return f"{value[:-4]}-USDT-SWAP"


def normalize_event(message):
    if not isinstance(message, dict) or message.get("e") != "forceOrder":
        return None
    if message.get("st") is not None and int(message["st"]) != 1:
        return None
    order = message.get("o") or {}
    try:
        symbol = str(order["s"]).upper()
        side = str(order["S"]).upper()
        price = float(order.get("ap") or order.get("p") or 0.0)
        quantity = float(order.get("z") or order.get("q") or 0.0)
        timestamp_ms = int(order.get("T") or message.get("E") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    instrument = _instrument(symbol)
    if not instrument or price <= 0 or quantity <= 0 or timestamp_ms <= 0:
        return None
    liquidated_side = "LONG" if side == "SELL" else (
        "SHORT" if side == "BUY" else "UNKNOWN"
    )
    return {
        "instrument": instrument,
        "timestamp_ms": timestamp_ms,
        "liquidated_side": liquidated_side,
        "bankruptcy_price": price,
        "contracts": quantity,
        "notional_usd_estimate": price * quantity,
    }


async def probe(duration_seconds=8, contract_values=None):
    del contract_values  # Binance USD-M quantities are base-asset quantities.
    context = ssl.create_default_context(cafile=certifi.where())
    events = []
    connected = False
    invalid = 0
    try:
        async with websockets.connect(
            WS_URL, open_timeout=10, ping_interval=20, ping_timeout=10,
            ssl=context, max_queue=1024,
        ) as socket:
            connected = True
            deadline = time.monotonic() + duration_seconds
            while time.monotonic() < deadline:
                timeout = min(2.0, max(0.01, deadline - time.monotonic()))
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    continue
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError, json.JSONDecodeError):
                    invalid += 1
                    continue
                event = normalize_event(message)
                if event is None:
                    invalid += 1
                    continue
                events.append(event)
    except Exception:
        connected = False

    state = (
        "AVAILABLE_EVENTS_OBSERVED" if connected and events
        else "AVAILABLE_NO_EVENT_IN_WINDOW" if connected
        else "CHANNEL_ERROR"
    )
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": "BINANCE",
        "channel": "!forceOrder@arr",
        "instrument_type": "USD_M_PERPETUAL",
        "observation_window_seconds": duration_seconds,
        "state": state,
        "subscription_confirmed": connected,
        "error_count": 0 if connected else 1,
        "data_message_count": len(events),
        "event_row_count": len(events),
        "events": events,
        "normalized_event_count": len(events),
        "invalid_event_count": invalid,
        "raw_messages_persisted": False,
        "trade_signal_generated": False,
        "paper_only_changed": False,
        "trading_authority_changed": False,
    }
