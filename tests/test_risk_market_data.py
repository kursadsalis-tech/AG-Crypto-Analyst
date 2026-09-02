import time
import unittest
from unittest import mock

from risk_manager import risk_manager


class RiskMarketDataTests(unittest.TestCase):
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
