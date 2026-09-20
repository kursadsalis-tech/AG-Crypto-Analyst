"""Normalized official exchange WebSocket events for PAPER position management."""
from __future__ import annotations

import asyncio
import json
import math
import ssl
import time
from dataclasses import dataclass, field

import certifi
import websockets


WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
BINANCE_SPOT_WS_URL = "wss://data-stream.binance.vision/ws"
BINANCE_USDM_WS_URL = "wss://fstream.binance.com/ws"
MAX_EVENT_AGE_MS = 2_000


def instrument(symbol: str, market_type: str = "USD_M_PERPETUAL") -> str:
    value = str(symbol).upper()
    if not value.endswith("USDT") or len(value) <= 4:
        raise ValueError("unsupported PAPER symbol")
    suffix = "-USDT" if market_type == "SPOT" else "-USDT-SWAP"
    return f"{value[:-4]}{suffix}"


@dataclass
class LiveState:
    symbol: str
    market_type: str = "USD_M_PERPETUAL"
    last_price: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    book_imbalance: float | None = None
    taker_imbalance: float | None = None
    visible_quote_volume: float | None = None
    market_timestamp_ms: int | None = None
    quote_timestamp_ms: int | None = None
    liquidity_timestamp_ms: int | None = None
    quote_source: str | None = None
    bid_quote_volume: float | None = None
    ask_quote_volume: float | None = None
    trade_buy_notional: float = 0.0
    trade_sell_notional: float = 0.0

    def thesis_supported(self, direction: str) -> bool | None:
        if self.book_imbalance is None or self.taker_imbalance is None:
            return None
        combined = self.book_imbalance + self.taker_imbalance
        return combined >= 0 if direction == "LONG" else combined <= 0


def _positive(value):
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError("non-positive market value")
    return parsed


def _quote_number(value):
    try:
        return _positive(value)
    except (TypeError, ValueError):
        return None


def _side_notional(price, size):
    quantity = _quote_number(size)
    return price * quantity if price is not None and quantity is not None else None


def apply_message(states: dict[str, LiveState], message: dict, received_ms: int):
    arg = message.get("arg") or {}
    channel, inst_id = arg.get("channel"), arg.get("instId")
    if not inst_id or not isinstance(message.get("data"), list):
        return []
    market_type = "USD_M_PERPETUAL" if inst_id.endswith("-SWAP") else "SPOT"
    symbol = inst_id.replace("-USDT-SWAP", "USDT").replace("-USDT", "USDT")
    state = states.setdefault(symbol, LiveState(symbol, market_type))
    updates = []
    for row in message["data"]:
        timestamp = int(row.get("ts", 0))
        if timestamp <= 0 or received_ms - timestamp > MAX_EVENT_AGE_MS or timestamp > received_ms + 500:
            continue
        if channel in {"tickers", "books5"}:
            if state.quote_timestamp_ms is not None and timestamp < state.quote_timestamp_ms:
                continue
            state.best_bid = state.best_ask = None
            state.bid_quote_volume = state.ask_quote_volume = None
            state.visible_quote_volume = None
            state.liquidity_timestamp_ms = None
            state.quote_timestamp_ms = timestamp
            state.quote_source = "OKX_" + channel.upper()
        if channel == "tickers":
            state.last_price = _positive(row["last"])
            state.best_bid = _quote_number(row.get("bidPx"))
            state.best_ask = _quote_number(row.get("askPx"))
            # OKX swap sizes are contracts, not base units. Without a verified
            # contract multiplier they cannot authorize executable liquidity.
            if market_type == "SPOT" and row.get("bidSz") and row.get("askSz"):
                state.bid_quote_volume = _side_notional(state.best_bid, row.get("bidSz"))
                state.ask_quote_volume = _side_notional(state.best_ask, row.get("askSz"))
                if state.bid_quote_volume is not None and state.ask_quote_volume is not None:
                    state.visible_quote_volume = state.bid_quote_volume + state.ask_quote_volume
                    state.liquidity_timestamp_ms = timestamp
        elif channel == "books5":
            bids = row.get("bids") or []
            asks = row.get("asks") or []
            bid_value = sum(_positive(level[0]) * _positive(level[1]) for level in bids)
            ask_value = sum(_positive(level[0]) * _positive(level[1]) for level in asks)
            total = bid_value + ask_value
            if total <= 0 or not bids or not asks:
                continue
            state.book_imbalance = (bid_value - ask_value) / total
            state.visible_quote_volume = total
            state.best_bid = _positive(bids[0][0])
            state.best_ask = _positive(asks[0][0])
            if market_type == "SPOT":
                state.bid_quote_volume = state.best_bid * _positive(bids[0][1])
                state.ask_quote_volume = state.best_ask * _positive(asks[0][1])
                state.liquidity_timestamp_ms = timestamp
        elif channel == "trades":
            notional = _positive(row["px"]) * _positive(row["sz"])
            if row.get("side") == "buy":
                state.trade_buy_notional += notional
            elif row.get("side") == "sell":
                state.trade_sell_notional += notional
            total = state.trade_buy_notional + state.trade_sell_notional
            state.taker_imbalance = (
                (state.trade_buy_notional - state.trade_sell_notional) / total
                if total else None
            )
        else:
            continue
        state.market_timestamp_ms = timestamp
        updates.append(state)
    return updates


def apply_binance_message(states: dict[str, LiveState], message: dict, received_ms: int, market_type="SPOT"):
    """Normalize Binance Spot ticker, depth and aggregate-trade events."""
    event = message.get("e")
    symbol = str(message.get("s") or "").upper()
    if event not in {"24hrTicker", "depthUpdate", "aggTrade"} or not symbol:
        return []
    timestamp = int(message.get("E", 0))
    if timestamp <= 0 or received_ms - timestamp > MAX_EVENT_AGE_MS or timestamp > received_ms + 500:
        return []
    state = states.setdefault(symbol, LiveState(symbol, market_type))
    if event == "24hrTicker":
        if state.quote_timestamp_ms is not None and timestamp < state.quote_timestamp_ms:
            return []
        state.best_bid = state.best_ask = None
        state.bid_quote_volume = state.ask_quote_volume = None
        state.visible_quote_volume = None
        state.liquidity_timestamp_ms = None
        state.quote_timestamp_ms = timestamp
        state.quote_source = "BINANCE_SPOT_WS_TICKER_BID_ASK"
        state.last_price = _positive(message["c"])
        state.best_bid = _quote_number(message.get("b"))
        state.best_ask = _quote_number(message.get("a"))
        bid_value = _side_notional(state.best_bid, message.get("B"))
        ask_value = _side_notional(state.best_ask, message.get("A"))
        state.bid_quote_volume = bid_value
        state.ask_quote_volume = ask_value
        state.liquidity_timestamp_ms = timestamp
        if bid_value is not None and ask_value is not None:
            total = bid_value + ask_value
            state.visible_quote_volume = total
            state.book_imbalance = (bid_value - ask_value) / total
        else:
            state.book_imbalance = None
    elif event == "depthUpdate":
        # This stream is not subscribed. Deltas without a synchronized snapshot
        # cannot prove the best executable side or refresh its timestamp.
        return []
    else:
        notional = _positive(message["p"]) * _positive(message["q"])
        # Binance `m` means the buyer is the maker, hence the aggressive side
        # is SELL.  This preserves taker-flow semantics used by the policy.
        if bool(message.get("m")):
            state.trade_sell_notional += notional
        else:
            state.trade_buy_notional += notional
        total = state.trade_buy_notional + state.trade_sell_notional
        state.taker_imbalance = (
            (state.trade_buy_notional - state.trade_sell_notional) / total
            if total else None
        )
    state.market_timestamp_ms = timestamp
    return [state]


def binance_subscription_params(symbols, market_type="SPOT"):
    """Timestamped Binance ticker/trade streams for executable PAPER markets."""
    market_type_filter = market_type
    normalized = {
        (item, "USD_M_PERPETUAL") if isinstance(item, str) else tuple(item)
        for item in symbols
    }
    return [
        f"{symbol.lower()}@{channel}"
        for symbol, item_market_type in sorted(normalized)
        if item_market_type == market_type_filter
        for channel in ("ticker", "aggTrade")
    ]


def subscription_args(symbols):
    normalized = {
        (item, "USD_M_PERPETUAL") if isinstance(item, str) else tuple(item)
        for item in symbols
    }
    return [
        {"channel": channel, "instId": instrument(symbol, market_type)}
        for symbol, market_type in sorted(normalized)
        for channel in ("tickers", "books5", "trades")
    ]


async def _okx_stream(symbols, on_state, stop_event, health_callback=None):
    """Reconnect forever; never expands PAPER authority or persists raw events."""
    context = ssl.create_default_context(cafile=certifi.where())
    states = {}
    backoff = 1
    while not stop_event.is_set():
        if not symbols():
            await asyncio.sleep(1)
            continue
        try:
            states.clear()
            async with websockets.connect(
                WS_URL, ssl=context, open_timeout=10, ping_interval=20,
                ping_timeout=10, max_queue=1024,
            ) as socket:
                active_args = subscription_args(symbols())
                await socket.send(json.dumps({
                    "id": str(int(time.time() * 1000)), "op": "subscribe",
                    "args": active_args,
                }))
                backoff = 1
                if health_callback:
                    health_callback("CONNECTED", None)
                while not stop_event.is_set():
                    desired_args = subscription_args(symbols())
                    if desired_args != active_args:
                        old = {json.dumps(row, sort_keys=True) for row in active_args}
                        new = {json.dumps(row, sort_keys=True) for row in desired_args}
                        removed = [json.loads(row) for row in sorted(old - new)]
                        added = [json.loads(row) for row in sorted(new - old)]
                        if removed:
                            await socket.send(json.dumps({"op": "unsubscribe", "args": removed}))
                        if added:
                            await socket.send(json.dumps({"op": "subscribe", "args": added}))
                        active_args = desired_args
                    try:
                        raw = await asyncio.wait_for(socket.recv(), timeout=1)
                    except asyncio.TimeoutError:
                        continue
                    received_ms = int(time.time() * 1000)
                    message = json.loads(raw)
                    if message.get("event") == "error":
                        if health_callback:
                            code = str(message.get("code") or "UNKNOWN")
                            health_callback("DEGRADED", f"OKX_SUBSCRIPTION_ERROR_{code}")
                        continue
                    for state in apply_message(states, message, received_ms):
                        await on_state(state, received_ms)
        except (OSError, asyncio.TimeoutError, ValueError, RuntimeError,
                websockets.WebSocketException) as exc:
            if health_callback:
                health_callback("DISCONNECTED", type(exc).__name__)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


async def _binance_stream(symbols, on_state, stop_event, health_callback=None, *, market_type="SPOT"):
    """Stream timestamped Binance executable bid/ask and side liquidity."""
    context = ssl.create_default_context(cafile=certifi.where())
    states, backoff = {}, 1
    while not stop_event.is_set():
        desired = binance_subscription_params(symbols(), market_type)
        if not desired:
            await asyncio.sleep(1)
            continue
        try:
            states.clear()
            async with websockets.connect(
                BINANCE_SPOT_WS_URL if market_type == "SPOT" else BINANCE_USDM_WS_URL, ssl=context, open_timeout=10,
                ping_interval=20, ping_timeout=10, max_queue=1024,
            ) as socket:
                active = desired
                await socket.send(json.dumps({"method": "SUBSCRIBE", "params": active, "id": 1}))
                backoff = 1
                if health_callback:
                    health_callback("CONNECTED", None)
                while not stop_event.is_set():
                    desired = binance_subscription_params(symbols(), market_type)
                    removed, added = sorted(set(active) - set(desired)), sorted(set(desired) - set(active))
                    if removed:
                        await socket.send(json.dumps({"method": "UNSUBSCRIBE", "params": removed, "id": 2}))
                    if added:
                        await socket.send(json.dumps({"method": "SUBSCRIBE", "params": added, "id": 3}))
                    active = desired
                    try:
                        raw = await asyncio.wait_for(socket.recv(), timeout=1)
                    except asyncio.TimeoutError:
                        continue
                    received_ms = int(time.time() * 1000)
                    message = json.loads(raw)
                    if message.get("code") is not None:
                        if health_callback:
                            health_callback("DEGRADED", f"BINANCE_SUBSCRIPTION_ERROR_{message['code']}")
                        continue
                    for state in apply_binance_message(states, message, received_ms, market_type=market_type):
                        await on_state(state, received_ms)
        except (OSError, asyncio.TimeoutError, ValueError, RuntimeError,
                websockets.WebSocketException) as exc:
            if health_callback:
                health_callback("DISCONNECTED", type(exc).__name__)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


async def stream(symbols, on_state, stop_event, health_callback=None):
    """Manage SPOT on Binance and legacy USD-M positions on OKX concurrently."""
    def spot_symbols():
        return [item for item in symbols()
                if not isinstance(item, str) and tuple(item)[1] == "SPOT"]

    def swap_symbols():
        return [item for item in symbols()
                if isinstance(item, str) or tuple(item)[1] != "SPOT"]

    async def idle_health():
        while not stop_event.is_set():
            if not symbols() and health_callback:
                health_callback("IDLE", None)
            await asyncio.sleep(1)

    await asyncio.gather(
        _binance_stream(spot_symbols, on_state, stop_event, health_callback, market_type="SPOT"),
        _binance_stream(swap_symbols, on_state, stop_event, health_callback, market_type="USD_M_PERPETUAL"),
        idle_health(),
    )
