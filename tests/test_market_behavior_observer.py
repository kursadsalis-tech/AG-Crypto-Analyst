import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import collect_closed_behavior_minute as observer


class MarketBehaviorObserverTests(unittest.TestCase):
    def test_refresh_builds_market_wide_route(self):
        exchange_info = {"symbols": [{
            "symbol": "AAAUSDT", "contractType": "PERPETUAL",
            "status": "TRADING", "quoteAsset": "USDT", "marginAsset": "USDT",
            "baseAsset": "AAA", "onboardDate": 1,
        }]}
        tickers = [{
            "symbol": "AAAUSDT", "lastPrice": "10", "openPrice": "9",
            "quoteVolume": "10000000",
        }]
        books = [{
            "symbol": "AAAUSDT", "bidPrice": "10", "askPrice": "10.01",
        }]
        with patch.object(
            observer, "fetch_json",
            side_effect=[exchange_info, tickers, books],
        ), patch.object(observer.time, "time", return_value=20_000_000):
            universe, route = observer.refresh_market_route()
        self.assertEqual(universe["selected_count"], 1)
        self.assertEqual(
            universe["selection_contract"]["exchange"], "BINANCE"
        )
        self.assertEqual(route["deep_stream_instruments"], ["AAA-USDT-SWAP"])


    def test_refresh_error_identifies_failed_endpoint(self):
        good_exchange = {"symbols": []}
        timeout = {
            "_transport_error": "TIMEOUT",
            "_endpoint": "/fapi/v1/ticker/24hr",
            "_attempts": 3,
        }
        good_books = []
        def fake_fetch(path, params=None):
            return {
                "/fapi/v1/exchangeInfo": good_exchange,
                "/fapi/v1/ticker/24hr": timeout,
                "/fapi/v1/ticker/bookTicker": good_books,
            }[path]
        with patch.object(observer, "fetch_json", side_effect=fake_fetch):
            with self.assertRaisesRegex(
                RuntimeError, "/fapi/v1/ticker/24hr"
            ):
                observer.refresh_market_route()

    def test_failure_health_is_v2_binance_and_hash_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            observer.write_failure_health(path, RuntimeError("transport down"))
            payload = json.loads(path.read_text())
            self.assertEqual(
                payload["health_contract_version"],
                "AGCA_MARKET_BEHAVIOR_HEALTH_V2_BINANCE_CANONICAL",
            )
            self.assertEqual(payload["status"], "DATA_UNAVAILABLE")
            self.assertEqual(payload["market_data_provider"], "BINANCE")
            self.assertFalse(payload["may_send_real_orders"])
            self.assertIn("transport down", payload["error"])
            self.assertIn("health_sha256", payload)

    def test_schema_migrates_before_market_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "runtime.db")
            con = sqlite3.connect(db)
            con.execute("""
                CREATE TABLE market_behavior_minute_features(
                    instrument TEXT NOT NULL,
                    minute_end_timestamp_ms INTEGER NOT NULL,
                    minute_start_timestamp_ms INTEGER NOT NULL,
                    sample_count INTEGER NOT NULL,
                    max_sample_gap_ms INTEGER NOT NULL,
                    book_imbalance_20_mean REAL NOT NULL,
                    book_imbalance_20_last REAL NOT NULL,
                    taker_imbalance_snapshot_mean REAL NOT NULL,
                    taker_imbalance_snapshot_last REAL NOT NULL,
                    spread_bps_mean REAL NOT NULL,
                    open_interest_usd_first REAL NOT NULL,
                    open_interest_usd_last REAL NOT NULL,
                    open_interest_usd_change REAL NOT NULL,
                    funding_rate_last REAL NOT NULL,
                    long_liquidation_notional_usd_estimate REAL NOT NULL,
                    short_liquidation_notional_usd_estimate REAL NOT NULL,
                    liquidation_event_count INTEGER NOT NULL,
                    quality_gates_json TEXT NOT NULL,
                    eligible_for_learning INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(instrument, minute_end_timestamp_ms)
                )
            """)
            con.commit()
            con.close()
            observer.migrate_behavior_schema(db)
            con = sqlite3.connect(db)
            try:
                columns = {
                    row[1] for row in con.execute(
                        "PRAGMA table_info(market_behavior_minute_features)"
                    )
                }
            finally:
                con.close()
            self.assertIn("market_data_provider", columns)

    def test_atomic_health_write_is_private_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            observer.write_json_atomic(path, {"status": "HEALTHY"})
            self.assertEqual(json.loads(path.read_text()), {"status": "HEALTHY"})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(tmp).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
