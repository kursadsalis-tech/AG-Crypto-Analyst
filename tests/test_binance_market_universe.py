import unittest

from data.binance_market_universe import build_universe


class BinanceMarketUniverseTests(unittest.TestCase):
    def test_selects_liquid_usdt_perpetual_from_websocket_tickers(self):
        tickers = [
            {
                "s": "NEWUSDT", "c": "10", "o": "9", "q": "10000000",
                "b": "9.99", "a": "10.00",
            },
            {
                "s": "BTCUSDT_261225", "c": "10", "o": "9",
                "q": "10000000", "b": "9.99", "a": "10.00",
            },
            {
                "s": "CMUSDT", "st": 2, "c": "10", "o": "9",
                "q": "10000000", "b": "9.99", "a": "10.00",
            },
        ]
        result = build_universe(
            tickers, snapshot_ms=2_000,
            min_quote_volume_usd=5_000_000,
        )
        self.assertEqual(result["selection_contract"]["exchange"], "BINANCE")
        self.assertEqual(
            result["selection_contract"]["transport"],
            "USD_M_ROUTED_WEBSOCKET_TICKER_PLUS_BOOK",
        )
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["selected"][0]["symbol"], "NEWUSDT")
        self.assertEqual(result["selected"][0]["instrument"], "NEW-USDT-SWAP")
        self.assertIsNone(
            result["selection_contract"]["minimum_listing_age_days"]
        )

    def test_nonfinite_market_values_cannot_enter_broad_radar(self):
        for field in ("c", "q", "b", "a"):
            for value in ("nan", "inf", "-inf"):
                ticker = {"s": "BADUSDT", "c": "10", "o": "9", "q": "10000000",
                          "b": "10", "a": "10.001", field: value}
                self.assertEqual(build_universe([ticker], snapshot_ms=1)["selected"], [])

    def test_wide_or_illiquid_contract_is_excluded(self):
        tickers = [{
            "s": "THINUSDT", "c": "10", "o": "10", "q": "1000",
            "b": "9", "a": "11",
        }]
        result = build_universe(tickers, snapshot_ms=1)
        self.assertEqual(result["selected"], [])


if __name__ == "__main__":
    unittest.main()
