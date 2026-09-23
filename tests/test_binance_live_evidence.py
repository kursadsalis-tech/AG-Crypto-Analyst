import unittest

from data import binance_live_evidence as live


class BinanceLiveEvidenceTests(unittest.TestCase):
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
        self.assertEqual(result["transport"], "USD_M_WEBSOCKET_STREAMS")

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
