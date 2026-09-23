import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from scripts import collect_closed_behavior_minute as observer


class MarketBehaviorObserverTests(unittest.TestCase):
    def test_alignment_catches_up_kline_when_behavior_window_is_too_short(self):
        target, catchup = observer.collection_alignment(30_000, 38)
        self.assertEqual(target, 120_000)
        self.assertEqual(catchup, 60_000)

    def test_alignment_uses_next_minute_when_full_behavior_span_fits(self):
        target, catchup = observer.collection_alignment(20_000, 38)
        self.assertEqual(target, 60_000)
        self.assertIsNone(catchup)

    def test_pre_refresh_guard_protects_imminent_boundary(self):
        self.assertEqual(
            observer.pre_refresh_technical_boundary(55_000, guard_seconds=10),
            60_000,
        )
        self.assertIsNone(
            observer.pre_refresh_technical_boundary(40_000, guard_seconds=10)
        )

    def test_cached_technical_universe_uses_only_recent_binance_usdm_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "technical.db"
            import sqlite3
            with sqlite3.connect(path) as con:
                con.execute("""CREATE TABLE technical_minute_bars(
                    symbol TEXT, provider TEXT, market_type TEXT, close_time_ms INTEGER
                )""")
                con.executemany(
                    "INSERT INTO technical_minute_bars VALUES(?,?,?,?)",
                    [
                        ("BTCUSDT","BINANCE","USD_M_PERPETUAL",299_000),
                        ("ETHUSDT","BINANCE","USD_M_PERPETUAL",10_000),
                        ("ARBUSDT","OTHER","USD_M_PERPETUAL",299_000),
                        ("BCHUSDT","BINANCE","SPOT",299_000),
                    ],
                )
            result = observer.cached_technical_universe(
                str(path), 300_000, recency_minutes=1
            )
        self.assertEqual(result, {"selected": [{"symbol": "BTCUSDT"}]})

    def test_refresh_builds_market_wide_route(self):
        tickers = [{
            "s": "AAAUSDT", "c": "10", "o": "9", "q": "10000000",
            "b": "10", "a": "10.01",
        }]
        with patch.object(
            observer, "fetch_market_tickers",
            new=AsyncMock(return_value=tickers),
        ), patch.object(observer.time, "time", return_value=20_000_000):
            universe, route = observer.refresh_market_route()
        self.assertEqual(universe["selected_count"], 1)
        self.assertEqual(
            universe["selection_contract"]["exchange"], "BINANCE"
        )
        self.assertEqual(
            universe["selection_contract"]["transport"],
            "USD_M_ROUTED_WEBSOCKET_TICKER_PLUS_BOOK",
        )
        self.assertEqual(route["deep_stream_instruments"], ["AAA-USDT-SWAP"])


    def test_atomic_health_write_is_private_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            observer.write_json_atomic(path, {"status": "HEALTHY"})
            self.assertEqual(json.loads(path.read_text()), {"status": "HEALTHY"})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(tmp).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
