import unittest

from data.okx_market_universe import build_universe


class OkxMarketUniverseTests(unittest.TestCase):
    def test_selects_by_tradability_not_example_list(self):
        day = 86_400_000
        snapshot = 200 * day
        instruments = {"code": "0", "data": [
            {"instId": "AAA-USDT-SWAP", "instType": "SWAP", "state": "live",
             "instCategory": "1", "settleCcy": "USDT", "baseCcy": "AAA", "ctVal": "1", "listTime": str(1 * day)},
            {"instId": "BBB-USDT-SWAP", "instType": "SWAP", "state": "live",
             "instCategory": "1", "settleCcy": "USDT", "baseCcy": "BBB", "ctVal": "1", "listTime": str(1 * day)},
        ]}
        tickers = {"code": "0", "data": [
            {"instId": "AAA-USDT-SWAP", "bidPx": "99.95", "askPx": "100.05",
             "last": "100", "vol24h": "100000"},
            {"instId": "BBB-USDT-SWAP", "bidPx": "99", "askPx": "101",
             "last": "100", "vol24h": "100000"},
        ]}
        result = build_universe(instruments, tickers, snapshot_ms=snapshot)
        self.assertEqual([row["instrument"] for row in result["selected"]], ["AAA-USDT-SWAP"])
        self.assertFalse(result["selection_contract"]["uses_user_example_coin_list"])
        self.assertEqual(result["excluded_counts"], {"WIDE_SPREAD": 1})

    def test_rejects_short_history_and_low_volume(self):
        day = 86_400_000
        snapshot = 200 * day
        instruments = {"code": "0", "data": [
            {"instId": "NEW-USDT-SWAP", "instType": "SWAP", "state": "live",
             "instCategory": "1", "settleCcy": "USDT", "ctVal": "1", "listTime": str(150 * day)},
            {"instId": "THIN-USDT-SWAP", "instType": "SWAP", "state": "live",
             "instCategory": "1", "settleCcy": "USDT", "ctVal": "1", "listTime": str(1 * day)},
        ]}
        tickers = {"code": "0", "data": [
            {"instId": "NEW-USDT-SWAP", "bidPx": "10", "askPx": "10.01",
             "last": "10", "vol24h": "1000000"},
            {"instId": "THIN-USDT-SWAP", "bidPx": "10", "askPx": "10.01",
             "last": "10", "vol24h": "100"},
        ]}
        result = build_universe(instruments, tickers, snapshot_ms=snapshot)
        self.assertEqual(result["selected"], [])
        self.assertEqual(result["excluded_counts"], {
            "LOW_QUOTE_VOLUME": 1,
            "SHORT_LISTING_HISTORY": 1,
        })

    def test_excludes_non_crypto_contract_categories(self):
        day = 86_400_000
        instruments = {"code": "0", "data": [
            {"instId": "XAU-USDT-SWAP", "instType": "SWAP", "state": "live",
             "instCategory": "4", "settleCcy": "USDT", "ctVal": "1", "listTime": str(day)},
        ]}
        tickers = {"code": "0", "data": [{
            "instId": "XAU-USDT-SWAP", "bidPx": "100", "askPx": "100.01",
            "last": "100", "vol24h": "1000000",
        }]}
        result = build_universe(instruments, tickers, snapshot_ms=200 * day)
        self.assertEqual(result["selected"], [])
        self.assertEqual(result["excluded_counts"], {"NOT_CRYPTO_CATEGORY": 1})


if __name__ == "__main__":
    unittest.main()
