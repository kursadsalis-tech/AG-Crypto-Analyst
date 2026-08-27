import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from analysis import forecast_core_lab as core


UTC = timezone.utc


class ForecastMarketFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.end = datetime(2026, 8, 27, 0, 0, tzinfo=UTC)
        self.fetch_start = self.end - timedelta(days=2)
        self.base = {
            "start": self.fetch_start,
            "rows": 100,
            "missing_quote_volume": 0,
        }

    def tearDown(self):
        self.con.close()

    def test_trailing_hour_must_be_complete(self):
        stale = {**self.base, "end": self.end - timedelta(hours=1, milliseconds=1)}
        fresh = {**self.base, "end": self.end - timedelta(milliseconds=1)}
        with patch.object(core, "SYMBOLS", ("BTCUSDT",)), \
             patch.object(core, "stored_coverage", side_effect=[stale, fresh]), \
             patch("data.historical_market_data.create_market_klines_table"), \
             patch("data.historical_market_data.fetch_symbol_history", return_value=1) as fetch:
            result = core.ensure_historical_coverage(
                self.con, self.fetch_start, self.end
            )
        self.assertEqual(result["BTCUSDT"]["status"], "FETCHED_READY")
        fetch.assert_called_once_with(
            "BTCUSDT",
            int((self.end - timedelta(hours=1)).timestamp() * 1000),
            int((self.end - timedelta(milliseconds=1)).timestamp() * 1000),
        )

    def test_complete_trailing_hour_does_not_refetch(self):
        fresh = {**self.base, "end": self.end - timedelta(milliseconds=1)}
        with patch.object(core, "SYMBOLS", ("BTCUSDT",)), \
             patch.object(core, "stored_coverage", return_value=fresh), \
             patch("data.historical_market_data.create_market_klines_table"), \
             patch("data.historical_market_data.fetch_symbol_history") as fetch:
            result = core.ensure_historical_coverage(
                self.con, self.fetch_start, self.end
            )
        self.assertEqual(result["BTCUSDT"]["status"], "READY")
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
