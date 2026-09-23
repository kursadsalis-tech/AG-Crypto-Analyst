"""Canonical Binance USD-M WebSocket market evidence.

The active GCE runtime is WebSocket-first.  Binance retired the legacy USD-M
Futures websocket base URL in April 2026 and split streams between /public and
/market.  This module follows that current routing and never falls back to a
different exchange.

Raw frames remain memory-only; callers persist compact closed-minute features.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import ssl
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

import certifi
import websockets

from data.binance_technical_bars import normalize_closed_1m_kline


VERSION = "BINANCE_WS_LIVE_EVIDENCE_V4_TECHNICAL_BARS"
PUBLIC_RAW_BASE = "wss://fstream.binance.com/public/ws"
PUBLIC_STREAM_BASE = "wss://fstream.binance.com/public/stream?streams="
MARKET_RAW_BASE = "wss://fstream.binance.com/market/ws"
MARKET_STREAM_BASE = "wss://fstream.binance.com/market/stream?streams="
ALL_MARKET_TICKER_URL = f"{MARKET_RAW_BASE}/!ticker@arr"
ALL_BOOK_TICKER_URL = f"{PUBLIC_RAW_BASE}/!bookTicker"
MAX_EVENT_AGE_MS = 5_000
OPEN_TIMEOUT_SECONDS = float(
    os.getenv("AGCA_BINANCE_WS_OPEN_TIMEOUT_SECONDS", "10")
)


@asynccontextmanager
async def measured_connection(url, *, timings=None, phase="socket", **kwargs):
    """Separate handshake, observation and close latency without frame logging."""
    # These bounded snapshots have already consumed their required evidence.
    # A slow peer close must not delay publication of the closed minute.
    kwargs.setdefault("close_timeout", 1.0)
    started = time.monotonic()
    entered = closing = None
    try:
        async with websockets.connect(url, **kwargs) as socket:
            entered = time.monotonic()
            try:
                yield socket
            finally:
                closing = time.monotonic()
    finally:
        finished = time.monotonic()
        if timings is not None:
            timings[f"{phase}_connect"] = round(
                ((entered or finished) - started) * 1000, 3
            )
            if entered is not None and closing is not None:
                timings[f"{phase}_observe"] = round((closing - entered) * 1000, 3)
                timings[f"{phase}_close"] = round((finished - closing) * 1000, 3)


def _is_um(message):
    if not isinstance(message, dict):
        return False
    value = message.get("st")
    return value is None or int(value) == 1


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


def _technical_kline_symbols(universe):
    """Return every broad eligible USD-M symbol needing continuous 1m bars."""
    return sorted({
        _symbol(row) for row in universe.get("selected", [])
    })


async def collect_closed_technical_bars(universe, *, observe_until_ms):
    """Collect only broad-universe closed 1m klines through a boundary.

    Used when the full observer starts too late in a minute to collect its
    behavior sample span.  Keeping this lightweight kline stream alive during
    the alignment wait prevents gaps in the local technical cache.
    """
    symbols = _technical_kline_symbols(universe)
    if not symbols:
        return []

    params = [f"{symbol.lower()}@kline_1m" for symbol in symbols]
    url = MARKET_STREAM_BASE + "/".join(params)
    context = ssl.create_default_context(cafile=certifi.where())
    bars = []
    async with websockets.connect(
        url,
        ssl=context,
        open_timeout=OPEN_TIMEOUT_SECONDS,
        ping_interval=20,
        ping_timeout=10,
        max_queue=4096,
    ) as socket:
        while int(time.time() * 1000) < int(observe_until_ms):
            remaining = (
                int(observe_until_ms) - int(time.time() * 1000)
            ) / 1000.0
            try:
                async with asyncio.timeout(max(0.05, min(1.0, remaining))):
                    raw = await socket.recv()
            except TimeoutError:
                continue
            message = _normalize_message(raw)
            if isinstance(message, dict) and message.get("e") == "kline":
                bar = normalize_closed_1m_kline(message)
                if bar is not None:
                    bars.append(bar)
    return bars


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


async def _collect_ticker_rows(duration_seconds, timings=None):
    context = ssl.create_default_context(cafile=certifi.where())
    rows = {}
    async with measured_connection(
        ALL_MARKET_TICKER_URL, timings=timings, phase="ticker",
        ssl=context,
        open_timeout=OPEN_TIMEOUT_SECONDS,
        ping_interval=20,
        ping_timeout=10,
        max_queue=256,
    ) as socket:
        deadline = time.monotonic() + float(duration_seconds)
        while time.monotonic() < deadline:
            timeout = max(0.05, deadline - time.monotonic())
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                break
            payload = json.loads(raw)
            if not isinstance(payload, list):
                continue
            for row in payload:
                if (
                    isinstance(row, dict)
                    and _is_um(row)
                    and str(row.get("s") or "").upper().endswith("USDT")
                ):
                    rows[str(row["s"]).upper()] = row
    return rows


async def _collect_book_rows(duration_seconds, timings=None):
    context = ssl.create_default_context(cafile=certifi.where())
    rows = {}
    async with measured_connection(
        ALL_BOOK_TICKER_URL, timings=timings, phase="book",
        ssl=context,
        open_timeout=OPEN_TIMEOUT_SECONDS,
        ping_interval=20,
        ping_timeout=10,
        max_queue=2048,
    ) as socket:
        deadline = time.monotonic() + float(duration_seconds)
        while time.monotonic() < deadline:
            timeout = max(0.05, deadline - time.monotonic())
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                break
            row = json.loads(raw)
            if (
                isinstance(row, dict)
                and _is_um(row)
                and str(row.get("s") or "").upper().endswith("USDT")
            ):
                rows[str(row["s"]).upper()] = row
    return rows


async def fetch_market_tickers(timeout_seconds=8, *, timings=None):
    """Return broad USD-M ticker rows enriched with live best bid/ask.

    Binance's routed Market all-ticker stream no longer carries best bid/ask.
    Broad discovery therefore joins Market !ticker@arr with Public !bookTicker.
    Both are canonical Binance streams and merged by exact symbol.
    """
    duration = max(2.0, float(timeout_seconds))
    ticker_task = asyncio.create_task(_collect_ticker_rows(min(duration, 3.0), timings))
    book_task = asyncio.create_task(_collect_book_rows(min(duration, 6.0), timings))
    tickers, books = await asyncio.gather(ticker_task, book_task)

    merged = []
    for symbol, ticker in tickers.items():
        book = books.get(symbol)
        if not book:
            continue
        merged.append({
            **ticker,
            "b": book.get("b"),
            "B": book.get("B"),
            "a": book.get("a"),
            "A": book.get("A"),
            "book_event_time": book.get("E"),
        })
    if not merged:
        raise TimeoutError(
            "Binance routed websocket broad snapshot had no ticker/book overlap"
        )
    return merged


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
    if not isinstance(message, dict) or not _is_um(message):
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
    if (
        not isinstance(message, dict)
        or not _is_um(message)
        or message.get("e") != "aggTrade"
    ):
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
            trade_connected and state.funding_rate is not None
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
        "transport": "USD_M_ROUTED_WEBSOCKET_STREAMS",
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
    closed_bar_sink=None,
    observe_until_ms=None,
    timings=None,
):
    """Collect deep evidence and exact closed 1m bars over one observer minute."""
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

    # Exact 1m technical bars must be continuous for the broad eligible
    # universe, not only for the rotating 12-symbol deep-attention slice.
    # Otherwise every rotation creates gaps and the adaptive pre-trade planner
    # correctly fails closed forever with an unusable contiguous tail.
    technical_symbols = (
        _technical_kline_symbols(universe)
        if closed_bar_sink is not None else []
    )

    public_params = [
        f"{symbol.lower()}@depth20@500ms" for symbol in sorted(states)
    ]
    market_params = [
        item
        for symbol in sorted(states)
        for item in (
            f"{symbol.lower()}@aggTrade",
            f"{symbol.lower()}@markPrice@1s",
        )
    ]
    technical_params = [
        f"{symbol.lower()}@kline_1m" for symbol in technical_symbols
    ]
    public_url = PUBLIC_STREAM_BASE + "/".join(public_params)
    market_url = MARKET_STREAM_BASE + "/".join(market_params)
    technical_url = MARKET_STREAM_BASE + "/".join(technical_params)

    context = ssl.create_default_context(cafile=certifi.where())
    stop = asyncio.Event()
    connectivity = {"public": False, "market": False, "technical": False}

    async def public_receiver():
        async with measured_connection(
            public_url, timings=timings, phase="public",
            ssl=context,
            open_timeout=OPEN_TIMEOUT_SECONDS,
            ping_interval=20,
            ping_timeout=10,
            max_queue=4096,
        ) as socket:
            connectivity["public"] = True
            while not stop.is_set():
                try:
                    async with asyncio.timeout(1):
                        raw = await socket.recv()
                except TimeoutError:
                    continue
                message = _normalize_message(raw)
                _apply_public(states, message, int(time.time() * 1000))

    async def market_receiver():
        async with measured_connection(
            market_url, timings=timings, phase="market",
            ssl=context,
            open_timeout=OPEN_TIMEOUT_SECONDS,
            ping_interval=20,
            ping_timeout=10,
            max_queue=4096,
        ) as socket:
            connectivity["market"] = True
            while not stop.is_set():
                try:
                    async with asyncio.timeout(1):
                        raw = await socket.recv()
                except TimeoutError:
                    continue
                message = _normalize_message(raw)
                received_ms = int(time.time() * 1000)
                if isinstance(message, dict) and message.get("e") == "markPriceUpdate":
                    _apply_public(states, message, received_ms)
                else:
                    _apply_trade(states, message, received_ms)

    async def technical_receiver():
        if not technical_params:
            return
        async with measured_connection(
            technical_url, timings=timings, phase="technical",
            ssl=context,
            open_timeout=OPEN_TIMEOUT_SECONDS,
            ping_interval=20,
            ping_timeout=10,
            max_queue=4096,
        ) as socket:
            connectivity["technical"] = True
            while not stop.is_set():
                try:
                    async with asyncio.timeout(1):
                        raw = await socket.recv()
                except TimeoutError:
                    continue
                message = _normalize_message(raw)
                if isinstance(message, dict) and message.get("e") == "kline":
                    bar = normalize_closed_1m_kline(message)
                    if bar is not None and closed_bar_sink is not None:
                        closed_bar_sink.append(bar)

    tasks = [
        asyncio.create_task(public_receiver()),
        asyncio.create_task(market_receiver()),
        asyncio.create_task(technical_receiver()),
    ]
    try:
        hydrate_deadline = time.monotonic() + float(hydration_timeout_seconds)
        while time.monotonic() < hydrate_deadline:
            hydrated = all(
                state.bids and state.asks and state.funding_rate is not None
                for state in states.values()
            )
            if hydrated and connectivity["market"]:
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
                connectivity["market"],
            ))
        if observe_until_ms is not None:
            while int(time.time() * 1000) < int(observe_until_ms):
                await asyncio.sleep(min(
                    0.10,
                    max(0.0, (int(observe_until_ms) - int(time.time() * 1000)) / 1000.0),
                ))
        return snapshots
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
