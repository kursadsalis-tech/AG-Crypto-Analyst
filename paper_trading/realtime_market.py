"""Normalized OKX public WebSocket events for PAPER execution only."""
from __future__ import annotations

import asyncio
import json
import ssl
import time
from dataclasses import dataclass, field

import certifi
import websockets


WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
MAX_EVENT_AGE_MS = 2_000


def instrument(symbol: str) -> str:
    value = str(symbol).upper()
    if not value.endswith("USDT") or len(value) <= 4:
        raise ValueError("unsupported PAPER symbol")
    return f"{value[:-4]}-USDT-SWAP"


@dataclass
class LiveState:
    symbol: str
    last_price: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    book_imbalance: float | None = None
    taker_imbalance: float | None = None
    visible_quote_volume: float | None = None
    market_timestamp_ms: int | None = None
    trade_buy_notional: float = 0.0
    trade_sell_notional: float = 0.0

    def thesis_supported(self, direction: str) -> bool | None:
        if self.book_imbalance is None or self.taker_imbalance is None:
            return None
        combined = self.book_imbalance + self.taker_imbalance
        return combined >= 0 if direction == "LONG" else combined <= 0


def _positive(value):
    parsed = float(value)
    if parsed <= 0:
        raise ValueError("non-positive market value")
    return parsed


def apply_message(states: dict[str, LiveState], message: dict, received_ms: int):
    arg = message.get("arg") or {}
    channel, inst_id = arg.get("channel"), arg.get("instId")
    if not inst_id or not isinstance(message.get("data"), list):
        return []
    symbol = inst_id.replace("-USDT-SWAP", "USDT")
    state = states.setdefault(symbol, LiveState(symbol))
    updates = []
    for row in message["data"]:
        timestamp = int(row.get("ts", 0))
        if timestamp <= 0 or received_ms - timestamp > MAX_EVENT_AGE_MS or timestamp > received_ms + 500:
            continue
        if channel == "tickers":
            state.last_price = _positive(row["last"])
            state.best_bid = _positive(row["bidPx"])
            state.best_ask = _positive(row["askPx"])
        elif channel == "books5":
            bids = row.get("bids") or []
            asks = row.get("asks") or []
            bid_value = sum(_positive(level[0]) * _positive(level[1]) for level in bids)
            ask_value = sum(_positive(level[0]) * _positive(level[1]) for level in asks)
            total = bid_value + ask_value
            if total <= 0:
                continue
            state.book_imbalance = (bid_value - ask_value) / total
            state.visible_quote_volume = total
            state.best_bid = _positive(bids[0][0])
            state.best_ask = _positive(asks[0][0])
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


def subscription_args(symbols):
    return [
        {"channel": channel, "instId": instrument(symbol)}
        for symbol in sorted(set(symbols))
        for channel in ("tickers", "books5", "trades")
    ]


async def stream(symbols, on_state, stop_event, health_callback=None):
    """Reconnect forever; never expands PAPER authority or persists raw events."""
    context = ssl.create_default_context(cafile=certifi.where())
    states = {}
    backoff = 1
    while not stop_event.is_set():
        if not symbols():
            if health_callback:
                health_callback("IDLE", None)
            await asyncio.sleep(1)
            continue
        try:
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
                        raise RuntimeError("OKX_SUBSCRIPTION_ERROR")
                    for state in apply_message(states, message, received_ms):
                        await on_state(state, received_ms)
        except (OSError, asyncio.TimeoutError, ValueError, RuntimeError,
                websockets.WebSocketException) as exc:
            if health_callback:
                health_callback("DISCONNECTED", type(exc).__name__)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
