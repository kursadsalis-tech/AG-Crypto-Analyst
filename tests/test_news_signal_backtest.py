import sqlite3
import unittest
from unittest.mock import patch

from analysis.news_signal_backtest import resolve_backtest_spot_symbols


class NewsSignalBacktestSymbolTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute(
            """
            CREATE TABLE market_klines (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL
            )
            """
        )

    def tearDown(self):
        self.connection.close()

    @patch("analysis.news_signal_backtest.get_spot_symbols")
    def test_uses_local_market_symbols_when_binance_times_out(self, get_symbols):
        get_symbols.side_effect = TimeoutError("timed out")
        self.connection.executemany(
            "INSERT INTO market_klines (symbol, interval) VALUES (?, ?)",
            [
                ("BTCUSDT", "1h"),
                ("ETHUSDT", "1h"),
                ("ETHUSDT", "1h"),
                ("SOLUSDT", "4h"),
                ("BTCEUR", "1h"),
            ],
        )

        symbols, rule = resolve_backtest_spot_symbols(self.connection)

        self.assertEqual(symbols, {"BTC": "BTCUSDT", "ETH": "ETHUSDT"})
        self.assertEqual(rule, "local market_klines USDT symbols used as fallback")

    @patch("analysis.news_signal_backtest.get_spot_symbols")
    def test_raises_when_remote_and_local_symbols_are_unavailable(self, get_symbols):
        get_symbols.side_effect = TimeoutError("timed out")

        with self.assertRaisesRegex(RuntimeError, "yerel market_klines"):
            resolve_backtest_spot_symbols(self.connection)


if __name__ == "__main__":
    unittest.main()
