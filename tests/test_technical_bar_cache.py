import csv
import io
import sqlite3
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

from data import binance_technical_bars as technical
from database import technical_bar_store as store
from database.runtime_store import REMOTE_RUNTIME_TABLES


NOW = 1_790_000_000_000


def bar(symbol="BTCUSDT", opened=NOW - 60_000, source=technical.SOURCE_WS):
    return {
        "symbol": symbol,
        "open_time_ms": opened,
        "close_time_ms": opened + 59_999,
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "volume": 10.0,
        "quote_volume": 1005.0,
        "trade_count": 20,
        "taker_buy_quote_volume": 600.0,
        "source": source,
    }


class TechnicalBarCacheTests(unittest.TestCase):
    def test_only_closed_usdm_one_minute_kline_is_normalized(self):
        message = {
            "e": "kline", "E": NOW, "st": 1, "s": "BTCUSDT",
            "k": {
                "t": NOW - 60_000, "T": NOW - 1,
                "s": "BTCUSDT", "i": "1m", "x": True,
                "o": "100", "h": "102", "l": "99", "c": "101",
                "v": "10", "q": "1005", "n": 20, "Q": "600",
            },
        }
        result = technical.normalize_closed_1m_kline(message)
        self.assertEqual(result["symbol"], "BTCUSDT")
        self.assertEqual(result["source"], technical.SOURCE_WS)

        open_bar = {**message, "k": {**message["k"], "x": False}}
        self.assertIsNone(technical.normalize_closed_1m_kline(open_bar))
        coin_m = {**message, "st": 2}
        self.assertIsNone(technical.normalize_closed_1m_kline(coin_m))

    def test_store_is_bounded_local_and_loads_in_causal_order(self):
        con = sqlite3.connect(":memory:")
        store.ensure_table(con)
        first = bar(opened=NOW - 120_000)
        second = bar(opened=NOW - 60_000)
        self.assertEqual(store.upsert_bars(con, [second, first]), 2)
        loaded = store.load_recent(con, "BTCUSDT", limit=10, before_ms=NOW)
        self.assertEqual(
            [row["open_time_ms"] for row in loaded],
            [NOW - 120_000, NOW - 60_000],
        )
        self.assertEqual(
            store.symbols_needing_bootstrap(
                con, ["BTCUSDT", "ETHUSDT"], now_ms=NOW, minimum_bars=2,
                lookback_hours=1,
            ),
            [{"symbol": "ETHUSDT", "count": 0}],
        )
        con.close()

    def test_persist_bars_waits_through_transient_writer_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "technical.db"
            blocker = sqlite3.connect(path, timeout=1, check_same_thread=False)
            blocker.execute("CREATE TABLE lock_probe(id INTEGER PRIMARY KEY)")
            blocker.commit()
            blocker.execute("BEGIN IMMEDIATE")
            blocker.execute("INSERT INTO lock_probe DEFAULT VALUES")

            def release():
                time.sleep(0.15)
                blocker.commit()

            releaser = threading.Thread(target=release)
            releaser.start()
            result = store.persist_bars(path, [bar()], now_ms=NOW)
            releaser.join(timeout=2)
            blocker.close()

            self.assertFalse(releaser.is_alive())
            self.assertEqual(result["upserted"], 1)
            with sqlite3.connect(path) as check:
                count = check.execute(
                    f"SELECT COUNT(*) FROM {store.TABLE}"
                ).fetchone()[0]
            self.assertEqual(count, 1)

    def test_technical_bar_cache_is_never_remote_mirrored(self):
        self.assertNotIn(store.TABLE, REMOTE_RUNTIME_TABLES)

    def test_archive_parser_accepts_headerless_exact_futures_klines(self):
        buffer = io.BytesIO()
        text = io.StringIO()
        writer = csv.writer(text)
        writer.writerow([
            NOW - 60_000, "100", "102", "99", "101", "10",
            NOW - 1, "1005", "20", "6", "600", "0",
        ])
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("BTCUSDT-1m.csv", text.getvalue())
        rows = technical._parse_archive_bytes("BTCUSDT", buffer.getvalue())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], technical.SOURCE_ARCHIVE)
        self.assertEqual(rows[0]["close"], 101.0)

    def test_microsecond_archive_timestamps_are_normalized_to_milliseconds(self):
        opened_us = (NOW - 60_000) * 1000
        closed_us = (NOW - 1) * 1000
        buffer = io.BytesIO()
        text = io.StringIO()
        writer = csv.writer(text)
        writer.writerow([
            opened_us, "100", "102", "99", "101", "10",
            closed_us, "1005", "20", "6", "600", "0",
        ])
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("BTCUSDT-1m.csv", text.getvalue())
        rows = technical._parse_archive_bytes("BTCUSDT", buffer.getvalue())
        self.assertEqual(rows[0]["open_time_ms"], NOW - 60_000)
        self.assertEqual(rows[0]["close_time_ms"], NOW - 1)


if __name__ == "__main__":
    unittest.main()
