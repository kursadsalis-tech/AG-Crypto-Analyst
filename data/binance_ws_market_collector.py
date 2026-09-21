"""Bounded, read-only Binance USD-M WebSocket minute acquisition.

All-market discovery is incremental; deep subscriptions are selected only
from valid cached exchangeInfo. OI alone uses AGCA's existing private
Supabase HTTP egress. No direct Binance REST, other exchanges, API keys,
database writes, real orders, or long-lived raw snapshots.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
import json
import ssl
import time

import certifi
import websockets

from data.adaptive_market_scanner import build_attention_route
from data.binance_oi_postgres import BinanceOIError, MAX_BATCH, fetch_batch
from data.binance_ws_live_evidence import summarize_ws_instrument
from data.binance_ws_market_universe import from_ws_caches
from data.live_behavior_evidence_bundle import build_bundle
from data.binance_liquidation_channel import probe


VERSION = "BINANCE_USDM_WS_MINUTE_COLLECTOR_V1"
PUBLIC = "wss://fstream.binance.com/public/ws"
MARKET = "wss://fstream.binance.com/market/ws"
MAX_CACHE_SYMBOLS = 1024
MAX_TRADE_EVENTS = 128
MAX_WS_MESSAGE_BYTES = 2**21


def _stamp(row, wall_ms, limit_ms=5000):
    if not isinstance(row, dict) or row.get("st", 1) != 1:
        return False
    try:
        event_ms = int(row["E"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return 0 <= wall_ms-event_ms <= limit_ms


class BinanceWsMinuteCollector:
    def __init__(self, exchange_info, fetched_at, *, database_url, clock=None,
                 private_oi_fetch=fetch_batch):
        """Caller MUST validate metadata cache digest via load_cache beforehand."""
        if not database_url:
            raise ValueError("PRIVATE_OI_EGRESS_NOT_CONFIGURED")
        self.exchange_info = exchange_info
        self.fetched_at = fetched_at
        self.database_url = database_url
        self.clock = clock or (lambda: int(time.time()*1000))
        self.private_oi_fetch = private_oi_fetch
        self.allowed = frozenset(
            row["symbol"] for row in exchange_info["symbols"]
            if isinstance(row, dict) and isinstance(row.get("symbol"), str)
            and row.get("status") == "TRADING"
            and row.get("contractType") == "PERPETUAL"
            and row.get("quoteAsset") == "USDT" and row.get("marginAsset") == "USDT"
        )
        self.mini, self.books, self.depths, self.marks = {}, {}, {}, {}
        self.trades = defaultdict(lambda: deque(maxlen=MAX_TRADE_EVENTS))
        self.deep = frozenset()
        self.ack = {"public": False, "market": False, "deep_public": False,
                    "deep_market": False}
        self.errors = []
        self.received = defaultdict(int)
        self.public_ws = None
        self.market_ws = None
        self.tasks = []

    def ingest(self, message, *, channel, wall_ms=None):
        """Bounded pure ingest; reject unexpected symbols, types, times, errors."""
        if wall_ms is None:
            wall_ms = self.clock()
        if isinstance(message, dict) and "data" in message and "stream" in message:
            message = message["data"]
        if isinstance(message, list):
            for row in message:
                self.ingest(row, channel=channel, wall_ms=wall_ms)
            return
        if not isinstance(message, dict):
            return
        if message.get("code") is not None:
            self.errors.append(f"{channel}:SUBSCRIPTION_ERROR")
            return
        if message.get("result", "_missing") is None:
            subscription_id = message.get("id")
            ack_name = {1: "public", 2: "market", 3: "deep_public",
                        4: "deep_market"}.get(subscription_id)
            if ack_name:
                self.ack[ack_name] = True
            return
        event, symbol = message.get("e"), message.get("s")
        if event == "24hrMiniTicker" and channel == "market":
            if symbol in self.allowed and _stamp(message, wall_ms):
                self.mini[symbol] = message
                self.received["mini"] += 1
        elif event == "bookTicker" and channel == "public":
            if symbol in self.allowed and _stamp(message, wall_ms):
                self.books[symbol] = message
                self.received["books"] += 1
        elif symbol in self.deep and _stamp(message, wall_ms):
            if event == "depthUpdate" and channel == "public":
                self.depths[symbol] = message
                self.received["depths"] += 1
            elif event == "aggTrade" and channel == "market":
                self.trades[symbol].append(message)
                self.received["trades"] += 1
            elif event == "markPriceUpdate" and channel == "market":
                self.marks[symbol] = message
                self.received["marks"] += 1

    def discovery(self, *, now_ms=None):
        stamp = self.clock() if now_ms is None else now_ms
        return from_ws_caches(self.exchange_info, self.fetched_at, self.mini,
                              self.books, now_ms=stamp)

    def snapshot(self, route, oi_samples, *, at_ms=None):
        stamp = self.clock() if at_ms is None else at_ms
        names = route["deep_stream_instruments"]
        if not self.ack["deep_public"] or not self.ack["deep_market"]:
            # No confirmed live data subscriptions: do not claim readiness.
            return {"version": VERSION, "instrument_count": 0,
                    "complete_instrument_count": 0, "instruments": [],
                    "source_error": "DEEP_SUBSCRIPTIONS_NOT_CONFIRMED"}
        rows = [summarize_ws_instrument(
            {"instrument": name, "exchange": "BINANCE_USD_M",
             "market_type": "USD_M_PERPETUAL"},
            depth=self.depths.get(name),
            trades=list(self.trades.get(name, ())),
            mark=self.marks.get(name), oi=oi_samples.get(name),
            received_ms=stamp,
        ) for name in names]
        return {"version": VERSION, "instrument_count": len(rows),
                "complete_instrument_count": sum(r["required_sources_available"] for r in rows),
                "instruments": rows, "raw_payloads_persisted": False,
                "source_exchange": "BINANCE_USD_M"}

    async def _listen(self, socket, channel):
        try:
            async for raw in socket:
                wall_ms = self.clock()
                self.ingest(json.loads(raw), channel=channel, wall_ms=wall_ms)
                if self.errors:
                    break
        except (OSError, ValueError, RuntimeError, websockets.WebSocketException) as exc:
            self.errors.append(f"{channel}:{type(exc).__name__}")
        finally:
            if not self.errors:
                self.errors.append(f"{channel}:STREAM_CLOSED")

    async def run(self, *, samples=4, cadence_seconds=10., warmup_seconds=6):
        if not 4 <= samples <= 5 or not 5 <= cadence_seconds <= 15:
            raise ValueError("UNBOUNDED_OR_UNDERSAMPLED_MINUTE")
        context = ssl.create_default_context(cafile=certifi.where())
        async with websockets.connect(
            PUBLIC, ssl=context, open_timeout=8, close_timeout=1,
            max_size=MAX_WS_MESSAGE_BYTES, max_queue=128,
            ping_interval=20, ping_timeout=10,
        ) as public, websockets.connect(
            MARKET, ssl=context, open_timeout=8, close_timeout=1,
            max_size=MAX_WS_MESSAGE_BYTES, max_queue=128,
            ping_interval=20, ping_timeout=10,
        ) as market:
            self.public_ws, self.market_ws = public, market
            self.tasks = [asyncio.create_task(self._listen(public, "public")),
                          asyncio.create_task(self._listen(market, "market"))]
            try:
                await public.send(json.dumps({"method": "SUBSCRIBE",
                                              "params": ["!bookTicker"], "id": 1}))
                await market.send(json.dumps({"method": "SUBSCRIBE",
                                              "params": ["!miniTicker@arr"], "id": 2}))
                await asyncio.sleep(max(2, min(warmup_seconds, 12)))
                if self.errors or not all((self.ack["public"], self.ack["market"])):
                    raise RuntimeError("BROAD_WS_SUBSCRIPTIONS_UNAVAILABLE")
                universe = self.discovery()
                route = build_attention_route(universe, opportunity_slots=10)
                names = route["deep_stream_instruments"]
                if len(names) > MAX_BATCH or len(set(names)) != len(names):
                    raise RuntimeError("UNBOUNDED_DEEP_ROUTE")
                self.deep = frozenset(names)
                await public.send(json.dumps({"method": "SUBSCRIBE",
                    "params": [f"{sym.lower()}@depth20@500ms" for sym in names],
                    "id": 3}))
                await market.send(json.dumps({"method": "SUBSCRIBE",
                    "params": [channel for sym in names
                               for channel in (f"{sym.lower()}@aggTrade",
                                               f"{sym.lower()}@markPrice@1s")],
                    "id": 4}))
                # Wait for ACK and first deep frames without trusting a mere connect.
                await asyncio.sleep(3)
                if self.errors or not all((self.ack["deep_public"],
                                           self.ack["deep_market"])):
                    raise RuntimeError("DEEP_WS_SUBSCRIPTIONS_UNAVAILABLE")
                span_ms = int(((samples-1)*cadence_seconds + 8)*1000)
                now_ms = self.clock()
                end_ms = (now_ms//60000+1)*60000
                if end_ms-now_ms < span_ms:
                    await asyncio.sleep(max(0, (end_ms-now_ms)/1000+.08))
                    now_ms = self.clock()
                    end_ms = (now_ms//60000+1)*60000
                # Liquidation observation is separate; no event does not mean 0.
                liq_duration = max(8, (end_ms-self.clock())/1000)
                liq_task = asyncio.create_task(probe(liq_duration))
                snapshots, oi_errors = [], []
                started = time.monotonic()
                try:
                    for index in range(samples):
                        await asyncio.sleep(max(0, started+index*cadence_seconds-time.monotonic()))
                        if self.errors:
                            raise RuntimeError("BINANCE_WS_STREAM_ERROR")
                        try:
                            oi = await asyncio.to_thread(
                                self.private_oi_fetch, self.database_url,
                                names, self.allowed)
                        except BinanceOIError as exc:
                            oi_errors.append(str(exc))
                            oi = {}
                        snapshot = self.snapshot(route, oi)
                        snapshots.append(snapshot)
                finally:
                    # Never leave a liquidation observer running past this cycle.
                    liquidation = await liq_task
                bundles = [build_bundle(snapshot, liquidation) for snapshot in snapshots]
                return {"universe": universe, "route": route,
                        "minute_end_ms": end_ms, "bundles": bundles,
                        "transport": {"version": VERSION,
                                      "received": dict(self.received),
                                      "oi_error_count": len(oi_errors),
                                      "oi_error_codes": sorted(set(oi_errors)),
                                      "websocket_errors": list(self.errors),
                                      "subscription_confirmed": dict(self.ack),
                                      "liquidation_state": liquidation["state"],
                                      "raw_payloads_persisted": False}}
            finally:
                for task in self.tasks:
                    task.cancel()
                await asyncio.gather(*self.tasks, return_exceptions=True)
