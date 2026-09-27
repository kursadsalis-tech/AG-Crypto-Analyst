import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from data import binance_liquidation_channel as liquidation


class BinanceLiquidationChannelTests(unittest.TestCase):
    def test_dynamic_absolute_close_follows_later_selected_minute(self):
        clock = SimpleNamespace(now=20.0)
        clock.time = clock.monotonic = lambda: clock.now
        target = [60_000]
        closed = []
        @asynccontextmanager
        async def connection(*_args, **_kwargs):
            try:
                yield SimpleNamespace(recv=AsyncMock())
            finally:
                closed.append(clock.now)
        async def receive(awaitable, *, timeout):
            awaitable.close()
            clock.now += timeout
            target[0] = 120_000
            raise asyncio.TimeoutError()
        with patch.object(liquidation, "time", clock), patch.object(
            liquidation, "measured_connection", connection
        ), patch.object(liquidation.asyncio, "wait_for", new=receive):
            result = asyncio.run(liquidation.probe(1, observe_until_ms=lambda: target[0]))
        self.assertEqual(closed, [120.0])
        self.assertEqual(result["observe_until_timestamp_ms"], 120_000)
        self.assertEqual(result["state"], "AVAILABLE_NO_EVENT_IN_WINDOW")

    def test_duration_only_probe_still_observes_requested_window(self):
        clock = SimpleNamespace(now=100.0)
        clock.time = clock.monotonic = lambda: clock.now
        @asynccontextmanager
        async def connection(*_args, **_kwargs):
            yield SimpleNamespace(recv=AsyncMock())
        async def receive(awaitable, *, timeout):
            awaitable.close()
            clock.now += timeout
            raise asyncio.TimeoutError()
        with patch.object(liquidation, "time", clock), patch.object(
            liquidation, "measured_connection", connection
        ), patch.object(liquidation.asyncio, "wait_for", new=receive):
            result = asyncio.run(liquidation.probe(3))
        self.assertEqual(clock.now, 103.0)
        self.assertEqual(result["observation_window_seconds"], 3)
        self.assertIsNone(result["observe_until_timestamp_ms"])

    def test_cancelled_dynamic_probe_closes_socket_and_propagates_cancellation(self):
        closed = []
        @asynccontextmanager
        async def connection(*_args, **_kwargs):
            try:
                yield SimpleNamespace(recv=AsyncMock(side_effect=asyncio.CancelledError))
            finally:
                closed.append(True)
        with patch.object(liquidation, "measured_connection", connection):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(liquidation.probe(observe_until_ms=lambda: 9_999_999_999_999))
        self.assertEqual(closed, [True])

    def test_sell_force_order_is_long_liquidation(self):
        event = liquidation.normalize_event({
            "e": "forceOrder", "E": 1000,
            "o": {
                "s": "BTCUSDT", "S": "SELL", "ap": "100",
                "z": "2", "T": 999,
            },
        })
        self.assertEqual(event["instrument"], "BTC-USDT-SWAP")
        self.assertEqual(event["liquidated_side"], "LONG")
        self.assertEqual(event["notional_usd_estimate"], 200.0)

    def test_current_market_route_and_cm_filter(self):
        self.assertEqual(
            liquidation.WS_URL,
            "wss://fstream.binance.com/market/ws/!forceOrder@arr",
        )
        self.assertIsNone(liquidation.normalize_event({
            "e": "forceOrder", "st": 2,
            "o": {
                "s": "BTCUSDT", "S": "SELL", "ap": "100",
                "z": "2", "T": 999,
            },
        }))

    def test_buy_force_order_is_short_liquidation(self):
        event = liquidation.normalize_event({
            "e": "forceOrder", "o": {
                "s": "ETHUSDT", "S": "BUY", "p": "50",
                "q": "3", "T": 999,
            },
        })
        self.assertEqual(event["liquidated_side"], "SHORT")


if __name__ == "__main__":
    unittest.main()
