"""No-network private reference risk contract; no database mutations."""
import os
import time
import unittest
from unittest.mock import patch

from data import binance_risk_private_feeds as feeds
from risk_manager import risk_manager as risk


class PrivateRiskReferenceTests(unittest.TestCase):
    def test_only_fixed_official_urls_allowed_without_db_connection(self):
        for url in (
            "https://example.com/path",
            "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=170",
            "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=170&foo=1",
            "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT%26evil&interval=1h&limit=170",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(
                feeds.PrivateRiskFeedError, "UNAPPROVED"
            ):
                feeds._private_get("fake-url", url)
        with self.assertRaisesRegex(feeds.PrivateRiskFeedError, "EGRESS_UNAVAILABLE"):
            feeds._private_get("", feeds.SPOT_PRICE_URL)

    def test_btc_reference_keeps_independent_provider_and_price_deviation(self):
        responses = [
            {"symbol": "BTCUSDT", "price": "100"},
            {"bitcoin": {"usd": 101}},
        ]
        with patch.object(feeds, "_private_get", side_effect=responses) as get:
            result = feeds.btc_independent_validation("fake-url")
        self.assertGreater(result["deviation_pct"], 0)
        self.assertEqual(get.call_args_list[0].args[1], feeds.SPOT_PRICE_URL)
        self.assertEqual(get.call_args_list[1].args[1], feeds.INDEPENDENT_PRICE_URL)

    def test_independent_price_mismatch_still_blocks_risk(self):
        with patch.object(feeds, "_private_get", side_effect=[
            {"symbol": "BTCUSDT", "price": "100"},
            {"bitcoin": {"usd": 110}},
        ]):
            with self.assertRaisesRegex(ValueError, "DATA_BLOCKED"):
                feeds.btc_independent_validation("fake-url")

    def test_spot_hourly_validates_real_closed_contiguous_rows(self):
        now = int(time.time()*1000)
        hour = now//3600000 * 3600000
        rows = [
            [hour - offset*3600000, "10", "11", "9", "10.5", "100",
             hour - offset*3600000+3599999, "1050"]
            for offset in range(169, -1, -1)
        ]
        with patch.object(feeds, "_private_get", return_value=rows) as fetch:
            candles = feeds.spot_closed_hourly("fake-url", "BTCUSDT")
        self.assertEqual(len(candles), 169)
        self.assertLess(int(candles[-1][6]), now)
        self.assertIn("symbol=BTCUSDT&interval=1h&limit=170",
                      fetch.call_args.args[1])

    def test_no_reference_evidence_source_is_labeled_as_usdm(self):
        self.assertIn("SPOT", feeds.SPOT_KLINES_URL.upper())
        self.assertEqual(risk.DB_NAME, os.getenv("AGCA_DATABASE_PATH", "crypto_data.db"))


if __name__ == "__main__":
    unittest.main()
