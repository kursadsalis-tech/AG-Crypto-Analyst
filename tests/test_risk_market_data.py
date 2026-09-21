import time
import sqlite3
import os
import json
import sys
from types import SimpleNamespace
from datetime import datetime, timezone
import unittest
from unittest import mock

from risk_manager import risk_manager


class RiskMarketDataTests(unittest.TestCase):
    def test_dynamic_risk_uses_fixed_binance_usdm_url_through_private_egress(self):
        now = int(time.time()*1000)
        rows = [[now-i*3600_000, "10", "11", "9", "10.5", "100",
                 now-i*3600_000+3599999, "1050"]
                for i in range(170, 0, -1)]
        cursor = mock.MagicMock()
        cursor.fetchone.return_value = (200, "application/json", json.dumps(rows))
        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        fake_psycopg = SimpleNamespace(connect=mock.Mock(return_value=conn))
        validated = object()
        with mock.patch.dict(os.environ, {"AGCA_REMOTE_DATABASE_URL": "fake-dsn"}), \
             mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}), \
             mock.patch.object(risk_manager, "validate_closed_klines",
                               return_value=validated) as validate, \
             mock.patch.object(risk_manager.requests, "get") as direct:
            result = risk_manager.fetch_dynamic_closed_1h_klines("1000PEPEUSDT")
        self.assertIs(result, validated)
        direct.assert_not_called()
        requested_url = cursor.execute.call_args_list[-1].args[1][0]
        self.assertIn("/fapi/v1/klines?symbol=1000PEPEUSDT", requested_url)
        self.assertIn("interval=1h", requested_url)
        self.assertIn("limit=170", requested_url)
        self.assertEqual(validate.call_args.kwargs["minimum_rows"], 169)

    def test_missing_private_egress_never_falls_back_to_okx_or_direct_rest(self):
        with mock.patch.dict(os.environ, {"AGCA_REMOTE_DATABASE_URL": ""}), \
             mock.patch.object(risk_manager, "fetch_okx_closed_1h_klines") as okx, \
             mock.patch.object(risk_manager.requests, "get") as direct:
            with self.assertRaisesRegex(RuntimeError, "PRIVATE_EGRESS_UNAVAILABLE"):
                risk_manager.fetch_dynamic_closed_1h_klines("NEWUSDT")
        okx.assert_not_called()
        direct.assert_not_called()

    def test_invalid_dynamic_symbol_rejected_before_private_connection(self):
        with mock.patch.dict(os.environ, {"AGCA_REMOTE_DATABASE_URL": "fake"}):
            for symbol in ("PEPE-USDT-SWAP", "BTCUSD", "BTCUSDT?other=1"):
                with self.assertRaisesRegex(RuntimeError, "INVALID_BINANCE"):
                    risk_manager.fetch_dynamic_closed_1h_klines(symbol)

    def test_dynamic_universe_keeps_native_symbols_and_rejects_okx_aliases(self):
        con = sqlite3.connect(":memory:")
        con.execute("""CREATE TABLE market_behavior_minute_features(
            instrument TEXT, minute_end_timestamp_ms INTEGER,
            eligible_for_learning INTEGER)""")
        instant = int(datetime.now(timezone.utc).timestamp() * 1000)
        con.executemany("INSERT INTO market_behavior_minute_features VALUES(?,?,1)", [
            ("1000PEPEUSDT", instant), ("BTCUSDT", instant),
            ("PEPE-USDT-SWAP", instant),
        ])
        self.assertEqual(risk_manager.behavior_risk_symbols(con),
                         ("1000PEPEUSDT", "BTCUSDT"))

    @mock.patch.object(risk_manager.requests, "get")
    def test_okx_rows_are_normalized_and_only_closed_candles_are_used(self, get):
        current_hour = int(time.time() // 3600 * 3600 * 1000)
        rows = []
        for offset in range(risk_manager.MIN_CLOSED_CANDLES, -1, -1):
            opened = current_hour - offset * 3_600_000
            rows.append([
                str(opened), "10", "11", "9", "10.5", "100",
                "100", "1050", "0" if offset == 0 else "1",
            ])
        response = mock.Mock()
        response.json.return_value = {"code": "0", "data": list(reversed(rows))}
        get.return_value = response

        candles = risk_manager.fetch_okx_closed_1h_klines("ZORAUSDT")

        self.assertEqual(len(candles), risk_manager.MIN_CLOSED_CANDLES)
        self.assertLess(int(candles[-1][6]), int(time.time() * 1000))
        self.assertEqual(float(candles[-1][7]), 1050.0)
        get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
