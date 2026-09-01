import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from paper_trading.crypto_profit_agent import (
    display_time, exact_close, exact_open, exact_planned_open, iso,
    next_tradable_hour_open,
)
from paper_trading.execution_model import simulated_fill


class CryptoProfitAgentFailClosedTests(unittest.TestCase):
    def test_operator_time_is_istanbul_first_with_explicit_utc(self):
        rendered = display_time("2026-09-01T01:00:00+00:00")
        self.assertEqual(
            rendered,
            "2026-09-01 04:00:00 İstanbul (2026-09-01 01:00:00 UTC)",
        )
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute("""
            CREATE TABLE market_klines (
                symbol TEXT, interval TEXT, open_time TEXT, close_time TEXT,
                open REAL, close REAL, quote_volume REAL
            )
        """)
        self.as_of = datetime(2026, 8, 24, 18, 59, 59, 999000,
                              tzinfo=timezone.utc)
        self.expected_open = self.as_of + timedelta(milliseconds=1)
        self.expected_close = self.expected_open + timedelta(hours=1) - timedelta(milliseconds=1)

    def tearDown(self):
        self.con.close()

    def insert_candle(self, *, offset=timedelta(), quote_volume=10_000):
        opened = self.expected_open + offset
        closed = opened + timedelta(hours=1) - timedelta(milliseconds=1)
        self.con.execute(
            "INSERT INTO market_klines VALUES (?,?,?,?,?,?,?)",
            ("BTCUSDT", "1h", iso(opened), iso(closed), 100.0, 101.0,
             quote_volume),
        )

    def test_non_exact_candle_is_not_used_as_entry_or_exit(self):
        self.insert_candle(offset=timedelta(minutes=1))
        self.assertIsNone(exact_open(self.con, "BTCUSDT", iso(self.as_of)))
        self.assertIsNone(exact_close(
            self.con, "BTCUSDT", iso(self.expected_close)
        ))

    def test_missing_quote_volume_cannot_become_strict_fill(self):
        self.insert_candle(quote_volume=None)
        _, price, volume = exact_open(self.con, "BTCUSDT", iso(self.as_of))
        self.assertIsNone(volume)
        self.assertIsNone(simulated_fill(
            reference_price=price, requested_notional=100, side="BUY",
            available_quote_volume=volume, require_liquidity=True,
        ))

    def test_database_outage_does_not_return_a_fill_candidate(self):
        self.con.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            exact_open(self.con, "BTCUSDT", iso(self.as_of))
        self.con = sqlite3.connect(":memory:")

    def test_planned_entry_is_strictly_after_decision_and_never_uses_prior_open(self):
        decision = datetime(2026, 8, 24, 18, 2, 7, tzinfo=timezone.utc)
        planned = next_tradable_hour_open(decision)
        self.assertEqual(planned, datetime(2026, 8, 24, 19, 0, tzinfo=timezone.utc))
        self.insert_candle()  # 19:00 exact open
        result = exact_planned_open(self.con, "BTCUSDT", planned)
        self.assertEqual(result[0], planned)
        self.assertGreater(result[0], decision)

    def test_planned_entry_does_not_accept_previous_hour(self):
        self.insert_candle()
        later = self.expected_open + timedelta(hours=1)
        self.assertIsNone(exact_planned_open(self.con, "BTCUSDT", later))


if __name__ == "__main__":
    unittest.main()
