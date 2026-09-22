import unittest

from data.binance_market_universe import build_universe


class BinanceMarketUniverseTests(unittest.TestCase):
    def test_selects_live_liquid_usdt_perpetuals_without_age_veto(self):
        exchange = {"symbols": [
            {
                "symbol": "NEWUSDT", "contractType": "PERPETUAL",
                "status": "TRADING", "quoteAsset": "USDT",
                "marginAsset": "USDT", "baseAsset": "NEW",
                "onboardDate": 1_000,
            },
            {
                "symbol": "OLDUSDT", "contractType": "CURRENT_QUARTER",
                "status": "TRADING", "quoteAsset": "USDT",
                "marginAsset": "USDT", "baseAsset": "OLD",
                "onboardDate": 1,
            },
        ]}
        tickers = [
            {"symbol": "NEWUSDT", "lastPrice": "10", "openPrice": "9",
             "quoteVolume": "10000000"},
            {"symbol": "OLDUSDT", "lastPrice": "10", "openPrice": "9",
             "quoteVolume": "10000000"},
        ]
        books = [
            {"symbol": "NEWUSDT", "bidPrice": "9.99", "askPrice": "10.00"},
            {"symbol": "OLDUSDT", "bidPrice": "9.99", "askPrice": "10.00"},
        ]
        result = build_universe(
            exchange, tickers, books, snapshot_ms=2_000,
            min_quote_volume_usd=5_000_000,
        )
        self.assertEqual(result["selection_contract"]["exchange"], "BINANCE")
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["selected"][0]["symbol"], "NEWUSDT")
        self.assertEqual(result["selected"][0]["instrument"], "NEW-USDT-SWAP")
        self.assertIsNone(
            result["selection_contract"]["minimum_listing_age_days"]
        )

    def test_wide_or_illiquid_contract_is_excluded(self):
        exchange = {"symbols": [{
            "symbol": "THINUSDT", "contractType": "PERPETUAL",
            "status": "TRADING", "quoteAsset": "USDT",
            "marginAsset": "USDT", "baseAsset": "THIN",
        }]}
        tickers = [{
            "symbol": "THINUSDT", "lastPrice": "10", "openPrice": "10",
            "quoteVolume": "1000",
        }]
        books = [{"symbol": "THINUSDT", "bidPrice": "9", "askPrice": "11"}]
        result = build_universe(exchange, tickers, books, snapshot_ms=1)
        self.assertEqual(result["selected"], [])


if __name__ == "__main__":
    unittest.main()
