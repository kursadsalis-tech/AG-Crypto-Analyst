import sqlite3
import tempfile
import unittest
from pathlib import Path

from runtime import technical_kline_worker as worker


class TechnicalKlineWorkerTests(unittest.TestCase):
    def test_subscription_params_are_exact_sorted_usdm_1m_klines(self):
        self.assertEqual(
            worker.subscription_params(["ETHUSDT", "BTCUSDT", "ETHUSDT"]),
            ["btcusdt@kline_1m", "ethusdt@kline_1m"],
        )

    def test_selected_symbols_use_canonical_broad_universe_filter(self):
        tickers = [
            {
                "s": "BTCUSDT", "st": 1, "c": "100", "o": "90",
                "q": "10000000", "b": "99.9", "a": "100.1",
            },
            {
                "s": "LOWUSDT", "st": 1, "c": "1", "o": "1",
                "q": "1000", "b": "0.999", "a": "1.001",
            },
        ]
        self.assertEqual(
            worker.selected_symbols(tickers, snapshot_ms=1234),
            ("BTCUSDT",),
        )

    def test_recent_seed_is_local_binance_usdm_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "technical.db"
            with sqlite3.connect(path) as con:
                con.execute("""CREATE TABLE technical_minute_bars(
                    symbol TEXT,
                    market_type TEXT,
                    close_time_ms INTEGER,
                    provider TEXT
                )""")
                con.executemany(
                    "INSERT INTO technical_minute_bars VALUES(?,?,?,?)",
                    [
                        ("BTCUSDT", "USD_M_PERPETUAL", 299_000, "BINANCE"),
                        ("ETHUSDT", "USD_M_PERPETUAL", 10_000, "BINANCE"),
                        ("ARBUSDT", "USD_M_PERPETUAL", 299_000, "OTHER"),
                        ("BCHUSDT", "SPOT", 299_000, "BINANCE"),
                    ],
                )
            result = worker.recent_cached_symbols(
                str(path), now_ms=300_000, minutes=1
            )
        self.assertEqual(result, ("BTCUSDT",))

    def test_worker_bootstraps_schema_once_and_reports_persist_latency(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "runtime/technical_kline_worker.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "await asyncio.to_thread(bootstrap_schema, DB_PATH)",
            source,
        )
        self.assertIn("last_persist_duration_ms", source)

    def test_universe_state_rejects_empty_live_refresh(self):
        state = worker.UniverseState(
            ("BTCUSDT",), now_ms=1000, retention_seconds=60
        )
        with self.assertRaises(ValueError):
            state.refresh((), 2000)
        self.assertEqual(state.symbols, ("BTCUSDT",))

    def test_universe_state_keeps_symbol_across_partial_snapshot(self):
        state = worker.UniverseState(
            ("BTCUSDT", "ETHUSDT"),
            now_ms=1_000,
            retention_seconds=60,
        )
        state.refresh(("BTCUSDT",), 20_000)
        self.assertEqual(state.symbols, ("BTCUSDT", "ETHUSDT"))
        self.assertEqual(state.selected_symbol_count, 1)
        self.assertEqual(state.retained_symbol_count, 1)

    def test_universe_state_retires_symbol_only_after_retention_window(self):
        state = worker.UniverseState(
            ("BTCUSDT", "ETHUSDT"),
            now_ms=1_000,
            retention_seconds=60,
        )
        state.refresh(("BTCUSDT",), 62_000)
        self.assertEqual(state.symbols, ("BTCUSDT",))
        self.assertEqual(state.retained_symbol_count, 0)


if __name__ == "__main__":
    unittest.main()
