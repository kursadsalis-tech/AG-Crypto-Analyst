import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from paper_trading import execution_market_data as market
from paper_trading.execution_contract import ExecutionRules, OrderIntent
from paper_trading.execution_model import execute_paper

NOW = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
MS = int(NOW.timestamp() * 1000)


class ExecutionMarketDataTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.addCleanup(self.con.close)
        clock = patch.object(market, "datetime", wraps=datetime)
        self.clock = clock.start()
        self.clock.now.return_value = NOW
        self.addCleanup(clock.stop)

    def response(self, **overrides):
        row = {"symbol": "DYNAMICUSDT", "time": MS,
               "bidPrice": "99", "askPrice": "101", "bidQty": "10000", "askQty": "20000",
               "lastPrice": "777"}
        row.update(overrides)
        response = Mock()
        response.json.return_value = row
        return response

    def capture(self, purpose="ENTRY", observed_at=NOW):
        return market.capture_reference(
            self.con, position_id=1, symbol="DYNAMICUSDT", expected=NOW,
            purpose=purpose, observed_at=observed_at, side="BUY",
            market_type="USD_M_PERPETUAL",
        )

    def test_usdm_uses_selected_book_side_and_its_visible_size(self):
        with patch.object(market.requests, "get", return_value=self.response()) as get:
            buy = market.fetch_executable_quote("DYNAMICUSDT", "USD_M_PERPETUAL", "BUY")
            sell = market.fetch_executable_quote("DYNAMICUSDT", "USD_M_PERPETUAL", "SELL")
        self.assertEqual((buy.reference_price, buy.available_quote_volume), (101, 2020000))
        self.assertEqual((sell.reference_price, sell.available_quote_volume), (99, 990000))
        self.assertEqual(buy.quote_timestamp_ms, MS)
        self.assertEqual(get.call_args.args[0], "https://fapi.binance.com/fapi/v1/ticker/bookTicker")

    def test_retry_acquires_new_quote_instead_of_reusing_frozen_reference(self):
        market.ensure_table(self.con)
        self.con.execute("""INSERT INTO paper_execution_market_observations
            VALUES(1,'ENTRY','DYNAMICUSDT',?,?,?,777,100000,'OLD_LAST','hash',0)""",
            (NOW.isoformat(), NOW.isoformat(), NOW.isoformat()))
        with patch.object(market.requests, "get", side_effect=[self.response(), self.response(askPrice="102")]) as get:
            first, second = self.capture(), self.capture()
        self.assertEqual(get.call_count, 2)
        self.assertEqual((first.best_ask, second.best_ask), (101, 102))
        self.assertEqual(self.con.execute("SELECT reference_price FROM paper_execution_market_observations").fetchone()[0], 777)

    def test_due_close_acquires_current_book_never_historical_candle(self):
        self.clock.now.return_value = NOW + timedelta(seconds=10)
        with patch.object(market.requests, "get", return_value=self.response(time=MS+10000)) as get, \
                patch.object(market, "fetch_reference", side_effect=AssertionError("no candle")):
            result = self.capture("EXIT", NOW+timedelta(seconds=10))
        self.assertEqual(result.quote_timestamp_ms, MS+10000)
        self.assertEqual(get.call_count, 1)

    def test_late_or_not_yet_due_entry_does_not_backfill(self):
        with patch.object(market, "fetch_executable_quote", side_effect=AssertionError("no backfill")):
            self.assertIsNone(self.capture(observed_at=NOW+timedelta(minutes=11)))
            self.assertIsNone(self.capture(observed_at=NOW-timedelta(seconds=1)))

    def test_http_failure_has_no_okx_or_last_fallback(self):
        with patch.object(market.requests, "get", side_effect=market.requests.ConnectionError) as get:
            with self.assertRaisesRegex(ValueError, "SOURCE_UNAVAILABLE"):
                self.capture()
        self.assertEqual(get.call_count, 1)

    def test_timestamp_age_uses_response_observation_not_cycle_start(self):
        self.clock.now.return_value = NOW + timedelta(seconds=3)
        with patch.object(market.requests, "get", return_value=self.response()):
            captured = self.capture()
        result = execute_paper(OrderIntent("DYNAMICUSDT", "BUY", 100), captured,
                               ExecutionRules(.001, .01, 5))
        self.assertEqual(result.reason, "STALE_QUOTE")
        self.assertIsNone(result.fill)

    def test_missing_side_cannot_be_replaced_by_ticker_last(self):
        with patch.object(market.requests, "get", return_value=self.response(askPrice=None)):
            captured = self.capture()
        result = execute_paper(OrderIntent("DYNAMICUSDT", "BUY", 100), captured,
                               ExecutionRules(.001, .01, 5))
        self.assertEqual(result.reason, "MISSING_ASK")
        self.assertIsNone(result.fill)

    def test_spot_uses_timestamped_websocket_sides_not_last(self):
        socket = Mock()
        socket.recv.return_value = json.dumps({"e": "24hrTicker", "s": "DYNAMICUSDT", "E": MS,
                                                "b": "99", "a": "101", "B": "10000", "A": "20000", "c": "777"})
        with patch("websockets.sync.client.connect") as connect, \
                patch.object(market.requests, "get", side_effect=AssertionError("no REST fallback")):
            connect.return_value.__enter__.return_value = socket
            result = market.fetch_executable_quote("DYNAMICUSDT", "SPOT", "BUY")
        self.assertEqual((result.best_bid, result.best_ask, result.quote_timestamp_ms), (99, 101, MS))
        self.assertEqual(result.available_quote_volume, 2020000)

    def test_spot_websocket_outage_fails_closed(self):
        with patch("websockets.sync.client.connect", side_effect=OSError), \
                patch.object(market.requests, "get", side_effect=AssertionError("no fallback")):
            with self.assertRaisesRegex(ValueError, "SOURCE_UNAVAILABLE"):
                market.fetch_executable_quote("DYNAMICUSDT", "SPOT", "BUY")

    def test_wrong_symbol_response_is_rejected(self):
        with patch.object(market.requests, "get", return_value=self.response(symbol="WRONGUSDT")):
            with self.assertRaisesRegex(ValueError, "SYMBOL_MISMATCH"):
                self.capture()

    def test_execution_observations_remain_local_only(self):
        from database.runtime_store import REMOTE_RUNTIME_TABLES
        self.assertNotIn("paper_execution_market_observations", REMOTE_RUNTIME_TABLES)
        self.assertNotIn("paper_position_events", REMOTE_RUNTIME_TABLES)
