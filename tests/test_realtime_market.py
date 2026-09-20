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
                        "arg": {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
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
            asyncio.run(realtime_market._okx_stream(\n                lambda: [("LAUSDT", "USD_M_PERPETUAL"), ("BTCUSDT", "USD_M_PERPETUAL")],\n                on_state, stop, lambda state, error: health.append((state, error)),\n            ))\n        self.assertEqual(seen, ["BTCUSDT"])
        self.assertIn(("DEGRADED", "OKX_SUBSCRIPTION_ERROR_51001"), health)

    def test_binance_trade_does_not_refresh_quote_or_liquidity_timestamp(self):
        states = {}
        message = {"e": "24hrTicker", "s": "DYNAMICUSDT", "E": NOW,
                   "c": "777", "b": "99", "a": "101", "B": "2", "A": "3"}
        state = realtime_market.apply_binance_message(states, message, NOW)[0]
        self.assertEqual((state.bid_quote_volume, state.ask_quote_volume), (198, 303))
        realtime_market.apply_binance_message(states, {
            "e": "aggTrade", "s": "DYNAMICUSDT", "E": NOW+1000,
            "p": "778", "q": "1", "m": False,
        }, NOW+1000)
        self.assertEqual(state.quote_timestamp_ms, NOW)
        self.assertEqual(state.liquidity_timestamp_ms, NOW)
        self.assertEqual(state.market_timestamp_ms, NOW+1000)

    def test_incremental_depth_cannot_be_mistaken_for_best_book(self):
        states = {"DYNAMICUSDT": LiveState("DYNAMICUSDT", "SPOT", best_bid=99, best_ask=101,
                                        quote_timestamp_ms=NOW-3000)}
        self.assertEqual(realtime_market.apply_binance_message(states, {
            "e": "depthUpdate", "s": "DYNAMICUSDT", "E": NOW,
            "b": [["90", "2"]], "a": [["110", "3"]],
        }, NOW), [])
        self.assertEqual(states["DYNAMICUSDT"].quote_timestamp_ms, NOW-3000)

    def test_older_ticker_cannot_overwrite_newer_quote(self):
        states = {}
        message = {"e": "24hrTicker", "s": "DYNAMICUSDT", "E": NOW,
                   "c": "777", "b": "99", "a": "101", "B": "2", "A": "3"}
        realtime_market.apply_binance_message(states, message, NOW)
        self.assertEqual(realtime_market.apply_binance_message(
            states, {**message, "E": NOW-1, "a": "90"}, NOW), [])
        self.assertEqual(states["DYNAMICUSDT"].best_ask, 101)

    def test_missing_binance_side_clears_previous_side_instead_of_reusing_it(self):
        states = {}
        message = {"e": "24hrTicker", "s": "DYNAMICUSDT", "E": NOW,
                   "c": "777", "b": "99", "a": "101", "B": "2", "A": "3"}
        realtime_market.apply_binance_message(states, message, NOW)
        message.pop("a")
        message["E"] += 1
        state = realtime_market.apply_binance_message(states, message, NOW+1)[0]
        self.assertIsNone(state.best_ask)
        self.assertIsNone(state.ask_quote_volume)
        self.assertIsNone(state.visible_quote_volume)

    def test_binance_usdm_ticker_authorizes_side_specific_executable_liquidity(self):
        states = {}
        message = {"e": "24hrTicker", "s": "SOLUSDT", "E": NOW,
                   "c": "150", "b": "149.9", "a": "150.1", "B": "4", "A": "3"}
        state = realtime_market.apply_binance_message(
            states, message, NOW, market_type="USD_M_PERPETUAL"
        )[0]
        self.assertEqual(state.market_type, "USD_M_PERPETUAL")
        self.assertAlmostEqual(state.bid_quote_volume, 149.9 * 4)
        self.assertAlmostEqual(state.ask_quote_volume, 150.1 * 3)
        self.assertEqual(state.quote_timestamp_ms, NOW)
        self.assertEqual(state.liquidity_timestamp_ms, NOW)
        self.assertEqual(state.quote_source, "BINANCE_USD_M_WS_TICKER_BID_ASK")

    def test_usdm_subscription_is_dynamic_and_does_not_include_spot(self):
        params = realtime_market.binance_subscription_params(
            [("SOLUSDT", "USD_M_PERPETUAL"), ("BTCUSDT", "SPOT")],
            "USD_M_PERPETUAL",
        )
        self.assertEqual(params, ["solusdt@ticker", "solusdt@aggTrade"])


if __name__ == "__main__":
    unittest.main()
