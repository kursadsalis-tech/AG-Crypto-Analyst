import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from paper_trading import execution_market_data as market


class ExecutionMarketDataTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        market.ensure_table(self.con)
        self.expected = datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.con.close()

    def response(self, payload):
        result = Mock()
        result.raise_for_status.return_value = None
        result.json.return_value = payload
        return result

    def source_responses(self, okx_row, binance_row):
        def get(url, **kwargs):
            if url == market.OKX_CANDLES_URL:
                return self.response({"code": "0", "data": [okx_row]})
            if url == market.BINANCE_SPOT_KLINES_URL:
                return self.response([binance_row])
            raise AssertionError(url)
        return get

    def test_dynamic_symbol_entry_is_captured_from_usdm_and_frozen(self):
        close = self.expected + timedelta(hours=1) - timedelta(milliseconds=1)
        binance_row = [
            int(self.expected.timestamp() * 1000), "0.50", "0.51", "0.49",
            "0.505", "100", int(close.timestamp() * 1000), "250000", 10,
            "50", "125000", "0",
        ]
        okx_row = [int(self.expected.timestamp() * 1000), "0.50", "0.51",
                   "0.49", "0.505", "100", "200000", "250000", "0"]
        with patch.object(market.requests, "get", side_effect=self.source_responses(
            okx_row, binance_row
        )) as get:
            first = market.capture_reference(
                self.con, position_id=41, symbol="CRVUSDT",
                expected=self.expected, purpose="ENTRY",
                observed_at=self.expected + timedelta(minutes=2),
            )
        self.assertEqual(first, (self.expected, 0.5, 250000.0))
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args.kwargs["params"]["symbol"], "CRVUSDT")
        with patch.object(market, "fetch_reference", side_effect=AssertionError(
            "immutable observation must be reused"
        )):
            second = market.capture_reference(
                self.con, position_id=41, symbol="CRVUSDT",
                expected=self.expected, purpose="ENTRY",
                observed_at=self.expected + timedelta(minutes=5),
            )
        self.assertEqual(second, first)
        stored = self.con.execute("""SELECT source,real_order_allowed,
            length(payload_sha256) FROM paper_execution_market_observations""").fetchone()
        self.assertEqual(stored, (
            "OKX_USDT_SWAP_WITH_BINANCE_SPOT_CROSSCHECK", 0, 64
        ))

    def test_late_entry_cannot_be_backfilled_with_future_liquidity(self):
        with patch.object(market, "fetch_reference", side_effect=AssertionError(
            "late entry must remain unavailable"
        )):
            result = market.capture_reference(
                self.con, position_id=42, symbol="DASHUSDT",
                expected=self.expected, purpose="ENTRY",
                observed_at=self.expected + timedelta(minutes=11),
            )
        self.assertIsNone(result)

    def test_exit_uses_exact_preceding_usdm_hour_close(self):
        opened = self.expected - timedelta(hours=1)
        close = self.expected - timedelta(milliseconds=1)
        binance_row = [
            int(opened.timestamp() * 1000), "50", "52", "49", "51",
            "100", int(close.timestamp() * 1000), "500000", 10,
            "50", "250000", "0",
        ]
        okx_row = [int(opened.timestamp() * 1000), "50", "52", "49", "51",
                   "100", "400000", "500000", "1"]
        with patch.object(market.requests, "get", side_effect=self.source_responses(
            okx_row, binance_row
        )):
            result = market.capture_reference(
                self.con, position_id=43, symbol="DASHUSDT",
                expected=self.expected, purpose="EXIT",
                observed_at=self.expected + timedelta(minutes=2),
            )
        self.assertEqual(result, (close, 51.0, 500000.0))

    def test_cross_source_price_conflict_fails_closed(self):
        close = self.expected + timedelta(hours=1) - timedelta(milliseconds=1)
        okx_row = [int(self.expected.timestamp() * 1000), "100", "101", "99",
                   "100", "100", "100000", "100000", "0"]
        binance_row = [int(self.expected.timestamp() * 1000), "110", "111", "109",
                       "110", "100", int(close.timestamp() * 1000), "100000"]
        with patch.object(market.requests, "get", side_effect=self.source_responses(
            okx_row, binance_row
        )):
            with self.assertRaisesRegex(ValueError, "deviation"):
                market.fetch_reference("CRVUSDT", self.expected, "ENTRY")

    def test_execution_observations_are_local_only(self):
        from database.runtime_store import REMOTE_RUNTIME_TABLES
        self.assertNotIn("paper_execution_market_observations", REMOTE_RUNTIME_TABLES)


if __name__ == "__main__":
    unittest.main()
