import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock, patch

from paper_trading.futures_costs import (
    FUNDING_ENDPOINT, FundingEvent, FuturesCostError, directional_funding_payment,
    fetch_funding_history_via_postgres, liquidation_breached, liquidation_price,
)
from paper_trading.crypto_profit_agent import first_liquidation_event


class FuturesCostsTests(unittest.TestCase):
    def test_directional_funding_sign_and_mark_notional(self):
        events = [FundingEvent("BTCUSDT", 10, 0.001, 100), FundingEvent("BTCUSDT", 20, -0.0005, 120)]
        self.assertAlmostEqual(directional_funding_payment(direction="LONG", quantity=2, events=events), 0.08)
        self.assertAlmostEqual(directional_funding_payment(direction="SHORT", quantity=2, events=events), -0.08)

    def test_one_x_liquidation_thresholds_and_breach(self):
        self.assertEqual(liquidation_price(entry_price=100, direction="LONG"), 0)
        short = liquidation_price(entry_price=100, direction="SHORT")
        self.assertAlmostEqual(short, 200 / 1.005)
        self.assertFalse(liquidation_breached(direction="SHORT", threshold=short, low=90, high=190))
        self.assertTrue(liquidation_breached(direction="SHORT", threshold=short, low=90, high=200))

    def test_funding_transport_is_allowlisted_and_filters_interval(self):
        start = datetime(2026, 8, 24, tzinfo=timezone.utc)
        end = start + timedelta(hours=24)
        event_time = int((start + timedelta(hours=8)).timestamp() * 1000)
        body = json.dumps([{"symbol":"BTCUSDT","fundingTime":event_time,"fundingRate":"0.0001","markPrice":"100"}])
        cursor = MagicMock(); cursor.fetchone.return_value = (200, "application/json", body)
        connection = MagicMock(); connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
        fake_psycopg = MagicMock(); fake_psycopg.connect.return_value = connection
        with patch.dict("sys.modules", {"psycopg": fake_psycopg}):
            events = fetch_funding_history_via_postgres(
                database_url="postgres://example", symbol="BTCUSDT", start=start, end=end,
            )
        self.assertEqual(len(events), 1)
        _, parameters = cursor.execute.call_args.args
        self.assertTrue(parameters[0].startswith(FUNDING_ENDPOINT + "?"))
        self.assertNotIn("postgres://example", parameters[0])
        query = parse_qs(urlparse(parameters[0]).query)
        self.assertEqual(int(query["startTime"][0]), int(start.timestamp() * 1000) + 1)
        self.assertEqual(int(query["endTime"][0]), int(end.timestamp() * 1000))

    def test_rejects_bad_symbol_and_out_of_window_event(self):
        now = datetime.now(timezone.utc)
        with self.assertRaisesRegex(FuturesCostError, "INVALID_FUNDING_REQUEST"):
            fetch_funding_history_via_postgres(database_url="x", symbol="BTC/USDT", start=now, end=now+timedelta(hours=1))

    def test_first_candle_breach_is_used(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE market_klines(symbol TEXT,interval TEXT,close_time TEXT,low REAL,high REAL)")
        start = datetime(2026, 8, 24, tzinfo=timezone.utc)
        first = start + timedelta(hours=1)
        second = start + timedelta(hours=2)
        con.executemany("INSERT INTO market_klines VALUES(?,?,?,?,?)", [
            ("BTCUSDT", "1h", first.isoformat(), 90, 150),
            ("BTCUSDT", "1h", second.isoformat(), 80, 205),
        ])
        event = first_liquidation_event(con, "BTCUSDT", start, second, "SHORT", 199)
        self.assertEqual(event, second)


if __name__ == "__main__": unittest.main()
