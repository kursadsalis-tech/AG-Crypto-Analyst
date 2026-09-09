import unittest
import asyncio
import json
from unittest.mock import patch

from paper_trading import realtime_market
from paper_trading.realtime_market import LiveState, apply_message, subscription_args


NOW = 1_788_000_000_000


class RealtimeMarketTests(unittest.TestCase):
    def test_subscribes_execution_channels_for_dynamic_symbols(self):
        args = subscription_args(["CRVUSDT", "SOLUSDT"])
        self.assertEqual(len(args), 6)
        self.assertIn({"channel": "trades", "instId": "CRV-USDT-SWAP"}, args)

    def test_spot_position_subscribes_to_spot_instrument(self):
        args = subscription_args([("BTCUSDT", "SPOT")])
        self.assertEqual(len(args), 3)
        self.assertIn({"channel": "books5", "instId": "BTC-USDT"}, args)
        states = {}
        message = {"arg": {"channel": "tickers", "instId": "BTC-USDT"},
                   "data": [{"ts": str(NOW), "last": "100", "bidPx": "99.9",
                             "askPx": "100.1"}]}
        state = apply_message(states, message, NOW)[0]
        self.assertEqual((state.symbol, state.market_type), ("BTCUSDT", "SPOT"))

    def test_stale_event_is_rejected(self):
        states = {}
        message = {"arg": {"channel": "tickers", "instId": "CRV-USDT-SWAP"},
                   "data": [{"ts": str(NOW - 3000), "last": "1", "bidPx": ".9",
                             "askPx": "1.1"}]}
        self.assertEqual(apply_message(states, message, NOW), [])

    def test_book_and_trades_form_live_thesis_state(self):
        states = {}
        book = {"arg": {"channel": "books5", "instId": "CRV-USDT-SWAP"},
                "data": [{"ts": str(NOW), "bids": [["10", "3", "0", "1"]],
                          "asks": [["11", "1", "0", "1"]]}]}
        trades = {"arg": {"channel": "trades", "instId": "CRV-USDT-SWAP"},
                  "data": [{"ts": str(NOW), "px": "10", "sz": "2", "side": "buy"}]}
        apply_message(states, book, NOW)
        apply_message(states, trades, NOW)
        self.assertTrue(states["CRVUSDT"].thesis_supported("LONG"))
        self.assertFalse(states["CRVUSDT"].thesis_supported("SHORT"))

    def test_missing_flow_never_invents_thesis_support(self):
        self.assertIsNone(LiveState("BTCUSDT", last_price=100).thesis_supported("LONG"))

    def test_one_subscription_error_does_not_drop_other_symbols(self):
        class Socket:
            def __init__(self):
                self.messages = iter((
                    json.dumps({"event": "error", "code": "51001"}),
                    json.dumps({
                        "arg": {"channel": "tickers", "instId": "BTC-USDT"},
                        "data": [{"ts": str(NOW), "last": "100", "bidPx": "99.9",
                                  "askPx": "100.1"}],
                    }),
                ))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def send(self, _):
                return None

            async def recv(self):
                try:
                    return next(self.messages)
                except StopIteration:
                    stop.set()
                    await asyncio.sleep(0)
                    return json.dumps({})

        stop = asyncio.Event()
        seen, health = [], []

        async def on_state(state, _):
            seen.append(state.symbol)
            stop.set()

        with patch.object(realtime_market.websockets, "connect", return_value=Socket()), \
                patch.object(realtime_market.time, "time", return_value=NOW / 1000):
            asyncio.run(realtime_market.stream(
                lambda: [("LAUSDT", "SPOT"), ("BTCUSDT", "SPOT")],
                on_state, stop, lambda state, error: health.append((state, error)),
            ))
        self.assertEqual(seen, ["BTCUSDT"])
        self.assertIn(("DEGRADED", "OKX_SUBSCRIPTION_ERROR_51001"), health)


if __name__ == "__main__":
    unittest.main()
