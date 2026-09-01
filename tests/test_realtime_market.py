import unittest

from paper_trading.realtime_market import LiveState, apply_message, subscription_args


NOW = 1_788_000_000_000


class RealtimeMarketTests(unittest.TestCase):
    def test_subscribes_execution_channels_for_dynamic_symbols(self):
        args = subscription_args(["CRVUSDT", "SOLUSDT"])
        self.assertEqual(len(args), 6)
        self.assertIn({"channel": "trades", "instId": "CRV-USDT-SWAP"}, args)

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


if __name__ == "__main__":
    unittest.main()
