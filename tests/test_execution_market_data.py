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

    def ticker_responses(self, okx_ticker, binance_ticker):
        def get(url, **kwargs):
            if url == market.OKX_TICKER_URL:
                return self.response({"code": "0", "data": [okx_ticker]})
            if url == market.BINANCE_SPOT_TICKER_URL:
                return self.response(binance_ticker)
            raise AssertionError(url)
        return get

    def test_dynamic_symbol_entry_is_captured_from_usdm_and_frozen(self):
        okx_ticker = {"last": "0.50", "volCcy24h": "720000000",
                      "ts": str(int(self.expected.timestamp() * 1000))}
        binance_ticker = {"symbol": "CRVUSDT", "price": "0.505"}
        with patch.object(market.requests, "get", side_effect=self.ticker_responses(
            okx_ticker, binance_ticker
        )) as get:
            first = market.capture_reference(
                self.con, position_id=41, symbol="CRVUSDT",
                expected=self.expected, purpose="ENTRY",
                observed_at=self.expected + timedelta(seconds=10),
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
            "OKX_USDT_SWAP_TICKER_WITH_BINANCE_SPOT_CROSSCHECK", 0, 64
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

    def test_spot_entry_uses_direct_spot_price_and_is_frozen(self):
        payload = {
            "symbol": "BTCUSDT", "lastPrice": "100", "quoteVolume": "1440000",
            "closeTime": int(self.expected.timestamp() * 1000),
        }
        okx = {"code": "0", "data": [{
            "last": "100", "volCcy24h": "14400",
            "ts": str(int(self.expected.timestamp() * 1000)),
        }]}
        responses = {
            market.BINANCE_SPOT_24H_URL: self.response(payload),
            market.OKX_TICKER_URL: self.response(okx),
        }
        with patch.object(
            market.requests, "get", side_effect=lambda url, **_: responses[url]
        ) as get:
            result = market.capture_reference(
                self.con, position_id=44, symbol="BTCUSDT",
                expected=self.expected, purpose="ENTRY",
                observed_at=self.expected + timedelta(seconds=2),
                market_type="SPOT",
            )
        self.assertEqual(result, (self.expected, 100.0, 1000.0))
        self.assertEqual(get.call_count, 2)
        stored = self.con.execute("""SELECT source,real_order_allowed
            FROM paper_execution_market_observations WHERE position_id=44""").fetchone()
        self.assertEqual(stored, (
            "BINANCE_SPOT_24H_TICKER_WITH_OKX_SPOT_MANAGEMENT_CHECK", 0,
        ))

    def test_spot_entry_rejects_missing_okx_management_instrument(self):
        payload = {
            "symbol": "LAUSDT", "lastPrice": "0.06", "quoteVolume": "1440000",
            "closeTime": int(self.expected.timestamp() * 1000),
        }
        responses = {
            market.BINANCE_SPOT_24H_URL: self.response(payload),
            market.OKX_TICKER_URL: self.response({"code": "51001", "data": []}),
        }
        with patch.object(
            market.requests, "get", side_effect=lambda url, **_: responses[url]
        ):
            with self.assertRaises(market.SpotManagementVenueUnavailable):
                market.capture_reference(
                    self.con, position_id=45, symbol="LAUSDT",
                    expected=self.expected, purpose="ENTRY",
                    observed_at=self.expected + timedelta(seconds=2),
                    market_type="SPOT",
                )

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
        okx_ticker = {"last": "100", "volCcy24h": "100000",
                      "ts": str(int(self.expected.timestamp() * 1000))}
        binance_ticker = {"symbol": "CRVUSDT", "price": "110"}
        with patch.object(market.requests, "get", side_effect=self.ticker_responses(
            okx_ticker, binance_ticker
        )):
            with self.assertRaisesRegex(ValueError, "deviation"):
                market.fetch_reference("CRVUSDT", self.expected, "ENTRY")

    def test_execution_observations_are_local_only(self):
        from database.runtime_store import REMOTE_RUNTIME_TABLES
        self.assertNotIn("paper_execution_market_observations", REMOTE_RUNTIME_TABLES)


if __name__ == "__main__":
    unittest.main()
