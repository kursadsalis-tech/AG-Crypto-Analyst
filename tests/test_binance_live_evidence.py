import unittest

from data.binance_live_evidence import summarize_instrument


class BinanceLiveEvidenceTests(unittest.TestCase):
    def test_normalizes_book_flow_oi_and_funding(self):
        row = {"instrument": "BTC-USDT-SWAP", "symbol": "BTCUSDT"}
        books = {
            "E": 1000, "T": 999,
            "bids": [["100", "2"], ["99", "1"]],
            "asks": [["101", "1"], ["102", "1"]],
        }
        trades = [
            {"p": "100", "q": "2", "T": 990, "m": False},
            {"p": "101", "q": "1", "T": 995, "m": True},
        ]
        oi = {"openInterest": "10", "time": 996}
        funding = {
            "lastFundingRate": "0.0001",
            "nextFundingTime": 2000, "time": 997,
        }
        result = summarize_instrument(
            row, books, trades, oi, funding, collected_ms=1100
        )
        self.assertEqual(result["symbol"], "BTCUSDT")
        self.assertTrue(result["required_sources_available"])
        self.assertGreater(
            result["recent_trades"]["buy_notional_usd_estimate"],
            result["recent_trades"]["sell_notional_usd_estimate"],
        )
        self.assertAlmostEqual(result["open_interest"]["usd"], 1005.0)
        self.assertEqual(result["funding"]["rate"], 0.0001)
        self.assertIn("20", result["order_book"]["depths"])


if __name__ == "__main__":
    unittest.main()
