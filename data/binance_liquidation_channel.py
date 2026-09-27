"""Observe Binance USD-M all-market liquidation orders in memory only."""
from __future__ import annotations

import asyncio
import json
import ssl
import time
from datetime import datetime, timezone

import certifi

from data.binance_live_evidence import measured_connection


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


async def probe(duration_seconds=8, contract_values=None, *, timings=None,
                observe_until_ms=None):
    """Observe for a duration, or follow a caller's dynamic absolute close."""
    del contract_values  # Binance USD-M quantities are base-asset quantities.
    context = ssl.create_default_context(cafile=certifi.where())
    events = []
    connected = False
    invalid = 0
    observation_started = None
    target_end_ms = None
    try:
        async with measured_connection(
            WS_URL, timings=timings, phase="liquidation", open_timeout=10, ping_interval=20, ping_timeout=10,
            ssl=context, max_queue=1024,
        ) as socket:
            connected = True
            observation_started = time.monotonic()
            deadline = observation_started + duration_seconds
            while True:
                if observe_until_ms is None:
                    remaining = deadline - time.monotonic()
                else:
                    target_end_ms = int(observe_until_ms())
                    remaining = (target_end_ms - int(time.time() * 1000)) / 1000.0
                if remaining <= 0:
                    break
                timeout = min(2.0, remaining)
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
        "observation_window_seconds": (
            duration_seconds if observe_until_ms is None else
            round(time.monotonic() - observation_started, 3)
            if observation_started is not None else 0.0
        ),
        "observe_until_timestamp_ms": target_end_ms,
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
