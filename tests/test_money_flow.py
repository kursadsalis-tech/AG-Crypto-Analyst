import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from analysis import money_flow


class MoneyFlowTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute("""
            CREATE TABLE market_klines(
              symbol TEXT,interval TEXT,close_time TEXT,open REAL,close REAL,
              quote_volume REAL,taker_buy_quote_volume REAL
            )
        """)
        self.now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.con.close()

    def add(self, hours_ago, opened, closed, quote, buy):
        self.con.execute(
            "INSERT INTO market_klines VALUES('BTCUSDT','1h',?,?,?,?,?)",
            ((self.now - timedelta(hours=hours_ago)).isoformat(), opened, closed, quote, buy),
        )

    def test_buy_pressure_and_price_confirmation(self):
        for hours in range(24, 0, -1):
            self.add(hours, 100, 101, 1000, 600)
        result = money_flow.analyze_symbol(self.con, "BTCUSDT", self.now)
        window = result["windows"][4]
        self.assertEqual(result["state"], "OBSERVATION_ONLY")
        self.assertEqual(window["direction"], "BUY_PRESSURE")
        self.assertAlmostEqual(window["net_quote"], 800)
        self.assertTrue(window["price_confirms"])

    def test_invalid_taker_volume_fails_closed(self):
        self.add(1, 100, 101, 1000, 1001)
        result = money_flow.analyze_symbol(self.con, "BTCUSDT", self.now)
        self.assertEqual(result, {"state": "DATA_MISSING", "reason": "INVALID_FLOW_VALUE"})

    def test_open_future_close_candle_is_excluded(self):
        self.add(1, 100, 101, 1000, 600)
        self.add(-1, 101, 50, 1000, 0)
        result = money_flow.analyze_symbol(self.con, "BTCUSDT", self.now)
        self.assertEqual(result["state"], "OBSERVATION_ONLY")
        self.assertEqual(result["windows"][1]["direction"], "BUY_PRESSURE")


if __name__ == "__main__":
    unittest.main()
