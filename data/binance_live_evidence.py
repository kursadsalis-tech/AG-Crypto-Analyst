"""Canonical Binance USD-M WebSocket market evidence.

The GCE runtime can consume Binance Futures WebSocket streams but the Futures
REST market-data API may be unavailable from its network location.  This module
therefore keeps the active observer REST-free.

Raw frames live in memory only.  The caller persists only compact closed-minute
features.
"""
from __future__ import annotations

import asyncio
import json
import math
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import certifi
import websockets


VERSION = "BINANCE_WS_LIVE_EVIDENCE_V2"
RAW_WS_URL = "wss://fstream.binance.com/ws"
PUBLIC_WS_URL = "wss://fstream.binance.com/public/ws"
MAX_EVENT_AGE_MS = 5_000


def _instrument(symbol):
    value = str(symbol).upper()
    if not value.endswith("USDT") or "_" in value:
        raise ValueError("unsupported Binance USD-M perpetual symbol")
    return f"{value[:-4]}-USDT-SWAP"


def _symbol(instrument_row):
    value = str(instrument_row.get("symbol") or "").upper()
    if value:
        return value
    instrument = str(instrument_row["instrument"]).upper()
    if not instrument.endswith("-USDT-SWAP"):
        raise ValueError("invalid perpetual instrument")
    return instrument[:-10].replace("-", "") + "USDT"


def _positive(value):
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError("non-positive market value")
    return parsed


def _book_notional(levels, depth):
    total = 0.0
    for row in levels[:depth]:
        try:
            total += _positive(row[0]) * _positive(row[1])
        except (TypeError, ValueError, IndexError):
            continue
    return total


async def fetch_market_tickers(timeout_seconds=8):
    """Return one fresh all-market USD-M 24h ticker frame via WebSocket."""
    context = ssl.create_default_context(cafile=certifi.where())
    async with websockets.connect(
        RAW_WS_URL,
        ssl=context,
        open_timeout=10,
        ping_interval=20,
        ping_timeout=10,
        max_queue=128,
    ) as socket:
        await socket.send(json.dumps({
            "method": "SUBSCRIBE", "params": ["!ticker@arr"], "id": 1,
        }))
        deadline = time.monotonic() + float(timeout_seconds)
        while time.monotonic() < deadline:
            timeout = max(0.05, deadline - time.monotonic())
            raw = await asyncio.wait_for(socket.recv(), timeout=timeout)
            message = json.loads(raw)
            if isinstance(message, dict) and message.get("result") is None and "id" in message:
                continue
            payload = message.get("data") if isinstance(message, dict) else message
            if isinstance(payload, list):
                rows = [
                    row for row in payload
                    if isinstance(row, dict)
                    and str(row.get("s") or "").upper().endswith("USDT")
                ]
                if rows:
                    return rows
        raise TimeoutError("Binance all-market ticker stream did not produce a snapshot")


@dataclass
class _DeepState:
    symbol: str
    instrument: str
    bids: list = field(default_factory=list)
    asks: list = field(default_factory=list)
    book_timestamp_ms: int = 0
    funding_rate: float | None = None
    funding_timestamp_ms: int = 0
    next_funding_timestamp_ms: int = 0
    trade_buy_notional: float = 0.0
    trade_sell_notional: float = 0.0
    trade_count: int = 0
    trade_oldest_ms: int = 0
    trade_newest_ms: int = 0

    def reset_trade_window(self):
        self.trade_buy_notional = 0.0
        self.trade_sell_notional = 0.0
        self.trade_count = 0
        self.trade_oldest_ms = 0
        self.trade_newest_ms = 0


def _normalize_message(raw):
    message = json.loads(raw)
    if isinstance(message, dict) and "data" in message and "stream" in message:
        return message["data"]
    return message


def _apply_public(states, message, received_ms):
    if not isinstance(message, dict):
        return
    event = str(message.get("e") or "")
    symbol = str(message.get("s") or "").upper()
    state = states.get(symbol)
    if state is None:
        return
    timestamp = int(message.get("E") or message.get("T") or 0)
    if timestamp <= 0 or received_ms - timestamp > MAX_EVENT_AGE_MS:
        return

    if event == "depthUpdate":
        bids = message.get("b") or []
        asks = message.get("a") or []
        if bids and asks:
            state.bids = bids[:20]
            state.asks = asks[:20]
            state.book_timestamp_ms = timestamp
    elif event == "markPriceUpdate":
        try:
            state.funding_rate = float(message.get("r"))
        except (TypeError, ValueError):
            return
        state.funding_timestamp_ms = timestamp
        state.next_funding_timestamp_ms = int(message.get("T") or 0)


def _apply_trade(states, message, received_ms):
    if not isinstance(message, dict) or message.get("e") not in {"aggTrade", "trade"}:
        return
    symbol = str(message.get("s") or "").upper()
    state = states.get(symbol)
    if state is None:
        return
    timestamp = int(message.get("T") or message.get("E") or 0)
    if timestamp <= 0 or received_ms - timestamp > MAX_EVENT_AGE_MS:
        return
    try:
        price = _positive(message.get("p"))
        quantity = _positive(message.get("q"))
    except (TypeError, ValueError):
        return
    notional = price * quantity
    # m=true means buyer is maker -> aggressive seller.
    if bool(message.get("m")):
        state.trade_sell_notional += notional
    else:
        state.trade_buy_notional += notional
    state.trade_count += 1
    state.trade_oldest_ms = (
        timestamp if state.trade_oldest_ms <= 0
        else min(state.trade_oldest_ms, timestamp)
    )
    state.trade_newest_ms = max(state.trade_newest_ms, timestamp)


def _snapshot(states, collected_ms, public_connected, trade_connected):
    rows = []
    for symbol, state in sorted(states.items()):
        bids = state.bids
        asks = state.asks
        best_bid = float(bids[0][0]) if bids else 0.0
        best_ask = float(asks[0][0]) if asks else 0.0
        midpoint = (
            (best_bid + best_ask) / 2.0
            if best_bid > 0 and best_ask > best_bid else 0.0
        )
        depths = {}
        for depth in (5, 20):
            bid_value = _book_notional(bids, depth)
            ask_value = _book_notional(asks, depth)
            total = bid_value + ask_value
            depths[str(depth)] = {
                "bid_visible_notional_usd_estimate": bid_value,
                "ask_visible_notional_usd_estimate": ask_value,
                "imbalance": (bid_value - ask_value) / total if total else 0.0,
            }

        trade_total = state.trade_buy_notional + state.trade_sell_notional
        book_available = (
            public_connected and midpoint > 0 and state.book_timestamp_ms > 0
        )
        funding_available = (
            public_connected and state.funding_rate is not None
            and state.funding_timestamp_ms > 0
        )
        trade_channel_available = bool(trade_connected)

        source_states = {
            "order_book": "AVAILABLE" if book_available else "MISSING",
            "recent_trades": (
                "AVAILABLE" if trade_channel_available and state.trade_count > 0
                else "AVAILABLE_ZERO_FLOW" if trade_channel_available
                else "MISSING"
            ),
            # No alternate source is allowed. OI remains explicitly unavailable
            # until a Binance-canonical websocket/local-history source exists.
            "open_interest": "UNAVAILABLE_CANONICAL_WS",
            "funding": "AVAILABLE" if funding_available else "MISSING",
            "liquidations": "SEPARATE_WEBSOCKET",
        }
        rows.append({
            "instrument": state.instrument,
            "symbol": symbol,
            "collected_timestamp_ms": int(collected_ms),
            "source_states": source_states,
            "required_sources_available": bool(
                book_available and funding_available and trade_channel_available
            ),
            "order_book": {
                "exchange_timestamp_ms": state.book_timestamp_ms,
                "age_ms": (
                    int(collected_ms) - state.book_timestamp_ms
                    if state.book_timestamp_ms else None
                ),
                "levels_bid": len(bids),
                "levels_ask": len(asks),
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread_bps": (
                    (best_ask - best_bid) / midpoint * 10_000.0
                    if midpoint else None
                ),
                "depths": depths,
            },
            "recent_trades": {
                "count": state.trade_count,
                "newest_exchange_timestamp_ms": state.trade_newest_ms,
                "oldest_exchange_timestamp_ms": state.trade_oldest_ms,
                "buy_notional_usd_estimate": state.trade_buy_notional,
                "sell_notional_usd_estimate": state.trade_sell_notional,
                "taker_imbalance": (
                    (state.trade_buy_notional - state.trade_sell_notional)
                    / trade_total if trade_total else 0.0
                ),
            },
            "open_interest": {
                "available": False,
                "exchange_timestamp_ms": 0,
                "contracts": 0.0,
                "base_currency": 0.0,
                "usd": 0.0,
            },
            "funding": {
                "rate": float(state.funding_rate or 0.0),
                "next_funding_timestamp_ms": state.next_funding_timestamp_ms,
                "exchange_timestamp_ms": state.funding_timestamp_ms,
                "observation_time_basis": "BINANCE_MARK_PRICE_WEBSOCKET",
            },
            "trade_signal_generated": False,
        })
        state.reset_trade_window()

    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": "BINANCE",
        "transport": "USD_M_WEBSOCKET_STREAMS",
        "instrument_count": len(rows),
        "complete_instrument_count": sum(
            bool(row["required_sources_available"]) for row in rows
        ),
        "instruments": rows,
        "raw_payloads_persisted": False,
        "open_interest_state": "UNAVAILABLE_CANONICAL_WS",
        "trade_signal_generated": False,
        "paper_only_changed": False,
        "trading_authority_changed": False,
    }


async def collect_minute_snapshots(
    route,
    universe,
    *,
    samples=5,
    cadence_seconds=10.0,
    hydration_timeout_seconds=6.0,
):
    """Collect deep evidence over one observer minute with persistent sockets."""
    universe_rows = {
        row["instrument"]: row for row in universe.get("selected", [])
    }
    deep = [
        name for name in route.get("deep_stream_instruments", [])
        if name in universe_rows
    ]
    states = {
        _symbol(universe_rows[name]): _DeepState(
            symbol=_symbol(universe_rows[name]), instrument=name
        )
        for name in deep
    }
    if not states:
        return []

    public_params = [
        item
        for symbol in sorted(states)
        for item in (
            f"{symbol.lower()}@depth20@500ms",
            f"{symbol.lower()}@markPrice@1s",
        )
    ]
    trade_params = [
        f"{symbol.lower()}@aggTrade" for symbol in sorted(states)
    ]
    context = ssl.create_default_context(cafile=certifi.where())
    stop = asyncio.Event()
    connectivity = {"public": False, "trade": False}

    async def public_receiver():
        async with websockets.connect(
            PUBLIC_WS_URL,
            ssl=context,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=10,
            max_queue=4096,
        ) as socket:
            await socket.send(json.dumps({
                "method": "SUBSCRIBE", "params": public_params, "id": 11,
            }))
            connectivity["public"] = True
            while not stop.is_set():
                try:
                    async with asyncio.timeout(1):
                        raw = await socket.recv()
                except TimeoutError:
                    continue
                message = _normalize_message(raw)
                if isinstance(message, dict) and message.get("result") is None and "id" in message:
                    continue
                _apply_public(states, message, int(time.time() * 1000))

    async def trade_receiver():
        async with websockets.connect(
            RAW_WS_URL,
            ssl=context,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=10,
            max_queue=4096,
        ) as socket:
            await socket.send(json.dumps({
                "method": "SUBSCRIBE", "params": trade_params, "id": 12,
            }))
            connectivity["trade"] = True
            while not stop.is_set():
                try:
                    async with asyncio.timeout(1):
                        raw = await socket.recv()
                except TimeoutError:
                    continue
                message = _normalize_message(raw)
                if isinstance(message, dict) and message.get("result") is None and "id" in message:
                    continue
                _apply_trade(states, message, int(time.time() * 1000))

    tasks = [
        asyncio.create_task(public_receiver()),
        asyncio.create_task(trade_receiver()),
    ]
    try:
        hydrate_deadline = time.monotonic() + float(hydration_timeout_seconds)
        while time.monotonic() < hydrate_deadline:
            hydrated = all(
                state.bids and state.asks and state.funding_rate is not None
                for state in states.values()
            )
            if hydrated and connectivity["trade"]:
                break
            await asyncio.sleep(0.05)

        snapshots = []
        started = time.monotonic()
        for index in range(int(samples)):
            target = started + index * float(cadence_seconds)
            await asyncio.sleep(max(0.0, target - time.monotonic()))
            snapshots.append(_snapshot(
                states,
                int(time.time() * 1000),
                connectivity["public"],
                connectivity["trade"],
            ))
        return snapshots
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
