import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from data import binance_live_evidence as live


def initial_message(phase):
    payload = {"s": "BTCUSDT", "E": int(live.time.time() * 1000)}
    payload.update({"e": "depthUpdate", "b": [["100", "1"]], "a": [["101", "1"]]}
                   if phase == "public" else {"e": "markPriceUpdate", "r": "0.001"})
    return json.dumps(payload)


class BinanceLiveEvidenceTests(unittest.TestCase):
    def test_transport_timings_survive_receiver_cancellation(self):
        timings = {}
        socket = AsyncMock()

        async def run():
            with patch.object(live.websockets, "connect", return_value=socket) as connect:
                with patch.object(live.time, "monotonic", side_effect=[1, 2, 5, 7]):
                    with self.assertRaises(asyncio.CancelledError):
                        async with live.measured_connection("wss://example", timings=timings):
                            raise asyncio.CancelledError()
                self.assertEqual(connect.call_args.kwargs["close_timeout"], 1.0)
        socket.__aexit__.return_value = False
        asyncio.run(run())
        self.assertEqual(timings, {
            "socket_connect": 1000, "socket_observe": 3000, "socket_close": 2000,
        })
        socket.__aexit__.assert_awaited_once()

    def test_before_sampling_hook_can_still_return_no_deadline(self):
        @asynccontextmanager
        async def connection(*_args, phase, **_kwargs):
            sent = False
            async def recv():
                nonlocal sent
                if not sent:
                    sent = True
                    return initial_message(phase)
                await asyncio.Future()
            yield SimpleNamespace(recv=recv)
        hook = AsyncMock(return_value=None)
        with patch.object(live, "measured_connection", connection):
            snapshots = asyncio.run(live.collect_minute_snapshots(
                {"deep_stream_instruments": ["BTC-USDT-SWAP"]},
                {"selected": [{"instrument": "BTC-USDT-SWAP", "symbol": "BTCUSDT"}]},
                samples=1, hydration_timeout_seconds=0.1, before_sampling=hook,
            ))
        hook.assert_awaited_once()
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["collected_timestamp_ms"],
                         snapshots[0]["instruments"][0]["collected_timestamp_ms"])

    def test_dead_market_receiver_interrupts_hydration_alignment_and_sampling_waits(self):
        for failure_phase in ("hydration", "alignment", "sampling"):
            with self.subTest(phase=failure_phase):
                failed, closed, stamps = asyncio.Event(), [], []
                @asynccontextmanager
                async def connection(_url, *, phase, **_kwargs):
                    sent = False
                    async def recv():
                        nonlocal sent
                        if not sent:
                            sent = True
                            return initial_message(phase)
                        if phase == "market":
                            await failed.wait()
                            raise OSError("connection lost")
                        await asyncio.Future()
                    try:
                        yield SimpleNamespace(recv=recv)
                    finally:
                        closed.append(phase)
                async def before_sampling():
                    if failure_phase == "alignment":
                        failed.set()
                        await asyncio.Future()
                    return None
                def snapshot(_states, stamp, *_connected):
                    stamps.append(stamp)
                    failed.set()
                    return {"collected_timestamp_ms": stamp}
                if failure_phase == "hydration":
                    failed.set()
                with patch.object(live, "measured_connection", connection), patch.object(
                    live, "_snapshot", side_effect=snapshot
                ):
                    with self.assertRaisesRegex(ConnectionError, "market receiver failed.*connection lost"):
                        asyncio.run(asyncio.wait_for(live.collect_minute_snapshots(
                            {"deep_stream_instruments": ["BTC-USDT-SWAP"]},
                            {"selected": [{"instrument": "BTC-USDT-SWAP", "symbol": "BTCUSDT"}]},
                            samples=4, cadence_seconds=10,
                            hydration_timeout_seconds=60,
                            before_sampling=before_sampling,
                        ), timeout=1))
                self.assertEqual(len(stamps), 1 if failure_phase == "sampling" else 0)
                self.assertCountEqual(closed, ["public", "market"])

    def test_hydration_requires_connected_channels_and_some_complete_evidence(self):
        for mode in ("opening", "empty", "partial"):
            with self.subTest(mode=mode):
                closed = []
                @asynccontextmanager
                async def connection(_url, *, phase, **_kwargs):
                    sent = False
                    async def recv():
                        nonlocal sent
                        if mode != "empty" and not sent:
                            sent = True
                            return initial_message(phase)
                        await asyncio.Future()
                    try:
                        if mode == "opening" and phase == "public":
                            await asyncio.Future()
                        yield SimpleNamespace(recv=recv)
                    finally:
                        closed.append(phase)
                hook = AsyncMock(return_value=None)
                symbols = ("BTC", "ETH") if mode == "partial" else ("BTC",)
                async def run():
                    return await asyncio.wait_for(live.collect_minute_snapshots(
                        {"deep_stream_instruments": [f"{symbol}-USDT-SWAP" for symbol in symbols]},
                        {"selected": [{"instrument": f"{symbol}-USDT-SWAP", "symbol": f"{symbol}USDT"}
                                      for symbol in symbols]},
                        samples=1, hydration_timeout_seconds=0.001, before_sampling=hook,
                    ), timeout=1)
                with patch.object(live, "measured_connection", connection):
                    if mode == "partial":
                        snapshots = asyncio.run(run())
                        self.assertEqual(snapshots[0]["complete_instrument_count"], 1)
                        self.assertEqual(snapshots[0]["instrument_count"], 2)
                        hook.assert_awaited_once()
                    else:
                        with self.assertRaisesRegex(ConnectionError, "after hydration"):
                            asyncio.run(run())
                        hook.assert_not_awaited()
                self.assertCountEqual(closed, ["public", "market"])

    def test_snapshot_normalizes_websocket_book_flow_and_funding(self):
        state = live._DeepState(
            symbol="BTCUSDT", instrument="BTC-USDT-SWAP",
            bids=[["100", "2"], ["99", "1"]],
            asks=[["101", "1"], ["102", "1"]],
            book_timestamp_ms=1000,
            funding_rate=0.0001,
            funding_timestamp_ms=995,
            next_funding_timestamp_ms=5000,
            trade_buy_notional=200.0,
            trade_sell_notional=101.0,
            trade_count=2,
            trade_oldest_ms=990,
            trade_newest_ms=998,
        )
        result = live._snapshot(
            {"BTCUSDT": state}, 1100,
            public_connected=True, trade_connected=True,
        )
        row = result["instruments"][0]
        self.assertTrue(row["required_sources_available"])
        self.assertGreater(
            row["recent_trades"]["buy_notional_usd_estimate"],
            row["recent_trades"]["sell_notional_usd_estimate"],
        )
        self.assertEqual(row["open_interest"]["available"], False)
        self.assertEqual(
            row["source_states"]["open_interest"],
            "UNAVAILABLE_CANONICAL_WS",
        )
        self.assertEqual(row["funding"]["rate"], 0.0001)
        self.assertIn("20", row["order_book"]["depths"])
        self.assertEqual(result["transport"], "USD_M_ROUTED_WEBSOCKET_STREAMS")

    def test_broad_snapshot_joins_market_ticker_with_public_book(self):
        tickers = {
            "BTCUSDT": {
                "e": "24hrTicker", "s": "BTCUSDT", "st": 1,
                "c": "100", "o": "95", "q": "10000000",
            },
            "CMUSDT": {
                "e": "24hrTicker", "s": "CMUSDT", "st": 2,
                "c": "10", "o": "9", "q": "10000000",
            },
        }
        books = {
            "BTCUSDT": {
                "e": "bookTicker", "s": "BTCUSDT", "st": 1,
                "b": "99.9", "B": "2", "a": "100.1", "A": "3",
            }
        }
        with patch.object(
            live, "_collect_ticker_rows", new=AsyncMock(return_value=tickers)
        ), patch.object(
            live, "_collect_book_rows", new=AsyncMock(return_value=books)
        ):
            rows = asyncio.run(live.fetch_market_tickers())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["s"], "BTCUSDT")
        self.assertEqual(rows[0]["b"], "99.9")
        self.assertEqual(rows[0]["a"], "100.1")
        self.assertEqual(rows[0]["st"], 1)

    def test_technical_kline_universe_covers_broad_selected_not_only_deep(self):
        universe = {
            "selected": [
                {"instrument": "BTC-USDT-SWAP", "symbol": "BTCUSDT"},
                {"instrument": "ETH-USDT-SWAP", "symbol": "ETHUSDT"},
                {"instrument": "AR-USDT-SWAP", "symbol": "ARUSDT"},
            ]
        }
        self.assertEqual(
            live._technical_kline_symbols(universe),
            ["ARUSDT", "BTCUSDT", "ETHUSDT"],
        )

    def test_current_routed_endpoints_are_split_by_channel_class(self):
        self.assertEqual(
            live.ALL_MARKET_TICKER_URL,
            "wss://fstream.binance.com/market/ws/!ticker@arr",
        )
        self.assertEqual(
            live.ALL_BOOK_TICKER_URL,
            "wss://fstream.binance.com/public/ws/!bookTicker",
        )
        self.assertTrue(live.PUBLIC_STREAM_BASE.endswith("/public/stream?streams="))
        self.assertTrue(live.MARKET_STREAM_BASE.endswith("/market/stream?streams="))

    def test_cm_payload_is_rejected_from_usdm_evidence(self):
        states = {
            "BTCUSDT": live._DeepState(
                symbol="BTCUSDT", instrument="BTC-USDT-SWAP"
            )
        }
        live._apply_public(states, {
            "e": "depthUpdate", "s": "BTCUSDT", "E": 1000, "st": 2,
            "b": [["100", "1"]], "a": [["101", "1"]],
        }, 1000)
        self.assertEqual(states["BTCUSDT"].bids, [])
        live._apply_trade(states, {
            "e": "aggTrade", "s": "BTCUSDT", "E": 1000, "T": 1000,
            "st": 2, "p": "100", "q": "1", "m": False,
        }, 1000)
        self.assertEqual(states["BTCUSDT"].trade_count, 0)

    def test_trade_window_resets_after_snapshot(self):
        state = live._DeepState(
            symbol="BTCUSDT", instrument="BTC-USDT-SWAP",
            bids=[["100", "2"]], asks=[["101", "1"]],
            book_timestamp_ms=1000, funding_rate=0.0,
            funding_timestamp_ms=995, trade_buy_notional=50,
            trade_count=1, trade_oldest_ms=990, trade_newest_ms=990,
        )
        live._snapshot(
            {"BTCUSDT": state}, 1100,
            public_connected=True, trade_connected=True,
        )
        self.assertEqual(state.trade_count, 0)
        self.assertEqual(state.trade_buy_notional, 0.0)


if __name__ == "__main__":
    unittest.main()
