import sqlite3
import time
import unittest
from unittest import mock

from risk_manager import risk_manager


class RiskMarketDataTests(unittest.TestCase):
    def test_runtime_connection_uses_extended_sqlite_busy_timeout(self):
        con = mock.Mock()
        with mock.patch.object(risk_manager, "runtime_connect", return_value=con) as connect:
            self.assertIs(risk_manager.runtime_connection(), con)
        connect.assert_called_once_with(risk_manager.DB_NAME, timeout=30)
        con.execute.assert_called_once_with("PRAGMA busy_timeout=30000")

    def test_dynamic_symbol_prefers_binance_usd_m(self):
        expected = object()
        with mock.patch.object(
            risk_manager, "fetch_closed_1h_klines", return_value=expected
        ) as fetch:
            self.assertIs(
                risk_manager.fetch_dynamic_closed_1h_klines("ZORAUSDT"),
                expected,
            )
        fetch.assert_called_once_with(
            "ZORAUSDT",
            url=risk_manager.BINANCE_USD_M_KLINES_URL,
            source_prefix="BINANCE_USD_M_KLINES",
        )

    def test_dynamic_symbol_falls_back_to_okx_swap(self):
        expected = object()
        with mock.patch.object(
            risk_manager,
            "fetch_closed_1h_klines",
            side_effect=RuntimeError("not on Binance USD-M"),
        ), mock.patch.object(
            risk_manager, "fetch_okx_closed_1h_klines", return_value=expected
        ) as fetch_okx:
            self.assertIs(
                risk_manager.fetch_dynamic_closed_1h_klines("NEWUSDT"),
                expected,
            )
        fetch_okx.assert_called_once_with("NEWUSDT")

    def test_save_snapshot_keeps_technical_context_local_latest_only(self):
        con = sqlite3.connect(":memory:")
        try:
            risk_manager.ensure_tables(con)
            policy = {
                "created_at": "2026-09-23T00:00:00+00:00",
                "policy_version": risk_manager.POLICY_VERSION,
                "market_state": "NORMAL",
                "coverage_count": 1,
                "coverage_total": 1,
                "max_total_exposure_pct": 60.0,
                "max_alt_exposure_pct": 25.0,
                "max_single_position_pct": 10.0,
                "risk_per_trade_pct": 0.75,
                "daily_loss_limit_pct": 2.0,
                "max_new_positions": 3,
                "new_positions_allowed": True,
                "policy_reasons": ["TEST"],
            }
            asset = {
                "symbol": "BTCUSDT",
                "as_of": "2026-09-22T23:59:59.999000+00:00",
                "price": 100.0,
                "return_24h_pct": 1.0,
                "range_24h_pct": 2.0,
                "hourly_volatility_pct": 0.5,
                "drawdown_7d_pct": -1.0,
                "quote_volume_24h": 1_000_000_000.0,
                "risk_score": 0,
                "risk_tier": "NORMAL",
                "max_position_pct": 10.0,
                "volatility_stop_reference_pct": 2.0,
                "new_position_allowed": True,
                "risk_reasons": ["TEST"],
            }
            context = {
                "version": risk_manager.realtime_technical_context.VERSION,
                "symbol": "BTCUSDT",
                "as_of": asset["as_of"],
                "generated_at": policy["created_at"],
                "market_scope": "REFERENCE_SPOT",
                "observation_only": True,
                "real_order_allowed": False,
                "timeframes": ["1h", "4h"],
                "advanced_1h": {},
                "structure_4h": {},
                "summary": {},
                "authority": {},
            }
            risk_manager.save_snapshot(
                con, policy, [asset], {}, [(context, 0.75)]
            )
            row = con.execute(
                "SELECT symbol,compute_ms,observation_only,real_order_allowed "
                "FROM paper_technical_context_latest"
            ).fetchone()
            self.assertEqual(row, ("BTCUSDT", 0.75, 1, 0))
        finally:
            con.close()

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
