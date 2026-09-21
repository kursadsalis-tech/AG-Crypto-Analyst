"""Normalized official exchange WebSocket events for PAPER position management."""
from __future__ import annotations

import asyncio
import json
import math
import ssl
import time
import threading
from collections import deque
from dataclasses import dataclass, field, replace

import certifi
import websockets


WS_URL = "wss://ws.okx.com:8443/ws/v5/public"
BINANCE_SPOT_WS_URL = "wss://data-stream.binance.vision/ws"
BINANCE_USDM_WS_URL = "wss://fstream.binance.com/ws"
MAX_EVENT_AGE_MS = 2_000
BINANCE_DISPATCH_INTERVAL_SECONDS = 0.05


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
    trade_timestamp_ms: int | None = None
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
            state.taker_imbalance = ((state.trade_buy_notional - state.trade_sell_notional) / total if total else None)
            state.trade_timestamp_ms = timestamp
        else:
            continue
        state.market_timestamp_ms = timestamp
        updates.append(state)
    return updates


def apply_binance_message(states: dict[str, LiveState], message: dict, received_ms: int, market_type="SPOT"):
    """Normalize Binance ticker and aggregate-trade events for PAPER execution."""
    event = message.get("e")
    symbol = str(message.get("s") or "").upper()
    if event not in {"24hrTicker", "bookTicker", "depthUpdate", "aggTrade", "trade"} or not symbol:
        return []
    timestamp = int(message.get("E", 0))
    if timestamp <= 0 or received_ms - timestamp > MAX_EVENT_AGE_MS or timestamp > received_ms + 500:
        return []
    state = states.setdefault(symbol, LiveState(symbol, market_type))
    if event in {"24hrTicker", "bookTicker"}:
        if state.quote_timestamp_ms is not None and timestamp < state.quote_timestamp_ms:
            return []
        state.best_bid = state.best_ask = None
        state.bid_quote_volume = state.ask_quote_volume = None
        state.visible_quote_volume = None
        state.liquidity_timestamp_ms = None
        state.quote_timestamp_ms = timestamp
        state.quote_source = "BINANCE_SPOT_WS_TICKER_BID_ASK" if market_type == "SPOT" else "BINANCE_USD_M_WS_TICKER_BID_ASK"
        if event == "24hrTicker":
            state.last_price = _positive(message["c"])
        else:
            bid = _quote_number(message.get("b"))
            ask = _quote_number(message.get("a"))
            state.last_price = (bid + ask) / 2 if bid is not None and ask is not None else state.last_price
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
        return []
    else:
        notional = _positive(message["p"]) * _positive(message["q"])
        if bool(message.get("m")):
            state.trade_sell_notional += notional
        else:
            state.trade_buy_notional += notional
        total = state.trade_buy_notional + state.trade_sell_notional
        state.taker_imbalance = ((state.trade_buy_notional - state.trade_sell_notional) / total if total else None)
        state.trade_timestamp_ms = timestamp
    state.market_timestamp_ms = timestamp
    return [state]


def binance_subscription_params(symbols, market_type="SPOT", channels=None):
    """Timestamped Binance streams for executable PAPER markets."""
    market_type_filter = market_type
    normalized = {
        (item, "USD_M_PERPETUAL") if isinstance(item, str) else tuple(item)
        for item in symbols
    }
    if channels is None:
        channels = (
            ("ticker", "aggTrade")
            if market_type_filter == "SPOT"
            else ("bookTicker", "trade")
        )
    return [
        f"{symbol.lower()}@{channel}"
        for symbol, item_market_type in sorted(normalized)
        if item_market_type == market_type_filter
        for channel in channels
    ]


class LatestFlowCache:
    """Bounded, immutable flow snapshots; never share quote state across threads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._symbols = ()
        self._latest = {}

    def set_symbols(self, symbols):
        normalized = tuple(sorted({
            (item, "USD_M_PERPETUAL") if isinstance(item, str) else tuple(item)
            for item in symbols
        }))
        with self._lock:
            self._symbols = normalized
            allowed = set(normalized)
            self._latest = {key: value for key, value in self._latest.items()
                            if key in allowed}

    def symbols(self):
        with self._lock:
            return self._symbols

    def publish(self, state):
        key = (state.symbol, state.market_type)
        snapshot = (state.trade_timestamp_ms, state.taker_imbalance,
                    state.trade_buy_notional, state.trade_sell_notional)
        with self._lock:
            if key in self._symbols:
                self._latest[key] = snapshot

    def apply_to(self, state, now_ms):
        with self._lock:
            snapshot = self._latest.get((state.symbol, state.market_type))
        stamp, imbalance, buy, sell = snapshot or (None, None, 0.0, 0.0)
        state.trade_timestamp_ms = stamp
        state.taker_imbalance = (
            imbalance if stamp is not None and
            -500 <= now_ms - stamp <= MAX_EVENT_AGE_MS else None
        )
        state.trade_buy_notional = buy
        state.trade_sell_notional = sell


async def _flow_thread(cache, stop_event):
    """Own all trade sockets/tasks in a dedicated thread and asyncio loop."""
    loop = asyncio.new_event_loop()
    stopped = threading.Event()

    async def ingest():
        tasks = [asyncio.create_task(_binance_stream(
            cache.symbols, None, stopped, market_type=market_type,
            channels=channels, flow_sink=cache,
        )) for market_type, channels in (
            ("SPOT", ("aggTrade",)), ("USD_M_PERPETUAL", ("trade",)),
        )]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    task = loop.create_task(ingest())
    errors = []

    def run():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            errors.append(exc)
        finally:
            loop.close()

    thread = threading.Thread(target=run, name="agca-trade-flow", daemon=True)
    thread.start()
    try:
        while not stop_event.is_set():
            if not thread.is_alive():
                if errors:
                    raise errors[0]
                raise RuntimeError("trade flow thread stopped unexpectedly")
            await asyncio.sleep(0.05)
    finally:
        stopped.set()
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:  # Thread already closed its loop.
            pass
        await asyncio.to_thread(thread.join)

def _queue_latest_state(pending, order, queued, state, received_ms):
    """Coalesce a hot websocket stream to the newest immutable state per symbol."""
    symbol = state.symbol
    pending[symbol] = (replace(state), received_ms)
    if symbol not in queued:
        order.append(symbol)
        queued.add(symbol)


def _pop_latest_state(pending, order, queued):
    """Return one fair, latest-only symbol state from the bounded dirty set."""
    while order:
        symbol = order.popleft()
        queued.discard(symbol)
        item = pending.pop(symbol, None)
        if item is not None:
            return item
    return None



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


async def _binance_stream(
    symbols, on_state, stop_event, health_callback=None, *,
    market_type="SPOT", channels=None, shared_states=None, flow_cache=None,
    flow_sink=None,
):
    """Keep Binance ingest independent from the heavier PAPER decision callback.

    The receiver task continuously drains websocket traffic and coalesces it to
    one immutable latest state per symbol. A separate dispatcher consumes those
    latest states at a bounded cadence. Intermediate events are intentionally
    dropped: PAPER execution needs the newest executable state, not a historical
    queue of every book/trade tick.
    """
    context = ssl.create_default_context(cafile=certifi.where())
    states = shared_states if shared_states is not None else {}
    backoff = 1
    while not stop_event.is_set():
        desired = binance_subscription_params(
            symbols(), market_type, channels=channels
        )
        if not desired:
            await asyncio.sleep(1)
            continue
        try:
            if shared_states is None:
                states.clear()
            async with websockets.connect(
                BINANCE_SPOT_WS_URL if market_type == "SPOT" else BINANCE_USDM_WS_URL,
                ssl=context,
                open_timeout=10,
                ping_interval=20,
                ping_timeout=10,
                max_queue=1024,
            ) as socket:
                active = desired
                await socket.send(json.dumps({
                    "method": "SUBSCRIBE", "params": active, "id": 1
                }))
                backoff = 1
                pending = {}
                dirty_order = deque()
                dirty_symbols = set()

                async def receiver():
                    # Hot path: only drain/parse/coalesce websocket events.
                    # Never query the database or run PAPER logic here.
                    #
                    # websockets.recv() may complete immediately while its
                    # internal queue is continuously non-empty (BTC/ETH can
                    # generate thousands of events per second). Without an
                    # explicit cooperative yield this task can monopolize the
                    # asyncio loop and starve the dispatcher for seconds.
                    messages_since_yield = 0
                    while not stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(socket.recv(), timeout=1)
                        except asyncio.TimeoutError:
                            continue
                        received_ms = int(time.time() * 1000)
                        message = json.loads(raw)
                        if message.get("code") is not None:
                            if health_callback:
                                health_callback(
                                    "DEGRADED",
                                    f"BINANCE_SUBSCRIPTION_ERROR_{message['code']}",
                                )
                            continue
                        if flow_sink is not None and message.get("e") not in {"trade", "aggTrade"}:
                            continue
                        if flow_cache is not None and message.get("e") not in {"bookTicker", "24hrTicker"}:
                            continue
                        for state in apply_binance_message(
                            states, message, received_ms,
                            market_type=market_type,
                        ):
                            if flow_sink is not None:
                                flow_sink.publish(state)
                                continue
                            if flow_cache is not None:
                                flow_cache.apply_to(state, received_ms)
                            _queue_latest_state(
                                pending, dirty_order, dirty_symbols,
                                state, received_ms,
                            )
                        messages_since_yield += 1
                        if messages_since_yield >= 32:
                            messages_since_yield = 0
                            await asyncio.sleep(0)

                async def refresh_subscriptions():
                    nonlocal active
                    while not stop_event.is_set():
                        await asyncio.sleep(1.0)
                        desired_now = binance_subscription_params(
                            symbols(), market_type, channels=channels
                        )
                        removed = sorted(set(active) - set(desired_now))
                        added = sorted(set(desired_now) - set(active))
                        if removed:
                            await socket.send(json.dumps({
                                "method": "UNSUBSCRIBE",
                                "params": removed,
                                "id": 2,
                            }))
                        if added:
                            await socket.send(json.dumps({
                                "method": "SUBSCRIBE",
                                "params": added,
                                "id": 3,
                            }))
                        active = desired_now
                        if flow_sink is not None:
                            active_symbols = {param.split("@")[0].upper() for param in active}
                            for symbol in tuple(states):
                                if symbol not in active_symbols:
                                    states.pop(symbol, None)

                async def dispatcher():
                    while not stop_event.is_set():
                        await asyncio.sleep(BINANCE_DISPATCH_INTERVAL_SECONDS)
                        item = _pop_latest_state(
                            pending, dirty_order, dirty_symbols
                        )
                        if item is None:
                            continue
                        state, observed_ms = item
                        now_ms = int(time.time() * 1000)
                        stamp_ms = state.market_timestamp_ms
                        # Fail closed: never hand an already stale market state
                        # to PAPER execution. A fresh replacement, if received,
                        # will be queued by receiver independently.
                        if (
                            stamp_ms is not None
                            and now_ms - stamp_ms > MAX_EVENT_AGE_MS
                        ):
                            continue
                        if flow_cache is not None and (
                            state.trade_timestamp_ms is None or
                            now_ms - state.trade_timestamp_ms > MAX_EVENT_AGE_MS
                        ):
                            state.taker_imbalance = None
                        await on_state(state, observed_ms)

                if health_callback:
                    health_callback("CONNECTED", None)
                # Explicitly cancel siblings before reconnecting or closing.
                coroutines = [receiver(), refresh_subscriptions()]
                if flow_sink is None:
                    coroutines.append(dispatcher())
                tasks = [asyncio.create_task(coro) for coro in coroutines]
                try:
                    await asyncio.gather(*tasks)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except (OSError, asyncio.TimeoutError, ValueError, RuntimeError,
                websockets.WebSocketException) as exc:
            if health_callback:
                health_callback("DISCONNECTED", type(exc).__name__)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


async def stream(symbols, on_state, stop_event, health_callback=None):
    """Keep Binance executable quotes on this loop; isolate trade ingest."""
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

    cache = LatestFlowCache()
    cache.set_symbols(symbols())

    async def refresh_flow_symbols():
        while not stop_event.is_set():
            cache.set_symbols(symbols())
            await asyncio.sleep(0.5)

    tasks = [asyncio.create_task(coro) for coro in (
        _binance_stream(
            spot_symbols, on_state, stop_event, health_callback,
            market_type="SPOT", channels=("ticker",), flow_cache=cache,
        ),
        _binance_stream(
            swap_symbols, on_state, stop_event, health_callback,
            market_type="USD_M_PERPETUAL", channels=("bookTicker",),
            flow_cache=cache,
        ),
        _flow_thread(cache, stop_event), refresh_flow_symbols(), idle_health(),
    )]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
