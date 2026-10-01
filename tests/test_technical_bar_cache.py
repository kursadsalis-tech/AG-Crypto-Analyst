import csv
import io
import sqlite3
from database.sqlite_policy import connect_local
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

    def test_bootstrap_replaces_narrow_index_without_leaving_a_duplicate(self):
        con = sqlite3.connect(":memory:")
        store.ensure_table(con)
        con.execute("DROP INDEX ix_technical_minute_bars_symbol_close")
        con.execute("CREATE INDEX ix_technical_minute_bars_symbol_close ON technical_minute_bars(symbol,market_type,close_time_ms)")
        store.ensure_table(con)
        self.assertIn("source", [row[2] for row in con.execute(
            "PRAGMA index_info(ix_technical_minute_bars_symbol_close)")])
        indexes = [row[1] for row in con.execute("PRAGMA index_list(technical_minute_bars)")]
        self.assertEqual(sum("symbol_close" in name for name in indexes), 1)
        con.close()

    def test_recent_bar_read_uses_ordered_range_without_history_sort(self):
        con = sqlite3.connect(":memory:")
        store.ensure_table(con)
        plan = con.execute(f"""EXPLAIN QUERY PLAN
            SELECT open_time_ms,close_time_ms,open,high,low,close,
                   volume,quote_volume,trade_count,taker_buy_quote_volume,source
            FROM {store.TABLE}
            WHERE symbol=? AND market_type=? AND provider=? AND close_time_ms<=?
            ORDER BY close_time_ms DESC LIMIT ?""",
            ("BTCUSDT", store.MARKET_TYPE, store.PROVIDER, NOW, 24)).fetchall()
        details = " ".join(str(row[3]) for row in plan)
        self.assertIn("ix_technical_minute_bars_symbol_close", details)
        self.assertIn("COVERING INDEX", details)
        self.assertNotIn("TEMP B-TREE", details)
        con.close()

    def test_hot_read_paths_do_not_run_schema_ddl(self):
        con = sqlite3.connect(":memory:")
        store.ensure_table(con)
        store.upsert_bars(con, [bar()])
        statements = []
        con.set_trace_callback(statements.append)

        loaded = store.load_recent(con, "BTCUSDT", limit=10, before_ms=NOW)
        count = store.recent_count(con, "BTCUSDT", since_ms=NOW - 120_000)

        self.assertEqual(len(loaded), 1)
        self.assertEqual(count, 1)
        ddl = [
            statement for statement in statements
            if statement.lstrip().upper().startswith(("CREATE TABLE", "CREATE INDEX"))
        ]
        self.assertEqual(ddl, [])
        con.close()

    def test_missing_cache_table_fails_closed_without_ddl(self):
        con = sqlite3.connect(":memory:")
        statements = []
        con.set_trace_callback(statements.append)

        self.assertEqual(store.load_recent(con, "BTCUSDT"), [])
        self.assertEqual(
            store.recent_count(con, "BTCUSDT", since_ms=NOW - 120_000), 0
        )
        self.assertFalse(any(
            statement.lstrip().upper().startswith(("CREATE TABLE", "CREATE INDEX"))
            for statement in statements
        ))
        con.close()

    def test_repeated_persistence_uses_plain_sqlite_and_no_schema_ddl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "technical.db"
            store.bootstrap_schema(path)
            statements = []
            original = connect_local

            def traced_connect(*args, **kwargs):
                con = original(*args, **kwargs)
                con.set_trace_callback(statements.append)
                return con

            from unittest import mock
            with mock.patch(
                "database.technical_bar_store.connect_local",
                side_effect=traced_connect,
            ):
                store.persist_bars(path, [bar()], now_ms=NOW)

            normalized = "\n".join(statements).upper()
            self.assertNotIn("CREATE TABLE", normalized)
            self.assertNotIn("CREATE INDEX", normalized)

    def test_technical_writer_does_not_use_runtime_connection(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "database/technical_bar_store.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("from database.runtime_store import connect", source)
        self.assertIn("connect_local(database_path", source)

    def test_retention_index_exists_and_writer_commits_before_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "technical.db"
            store.bootstrap_schema(path)
            with sqlite3.connect(path) as con:
                indexes = {
                    row[1] for row in con.execute(
                        "PRAGMA index_list(technical_minute_bars)"
                    ).fetchall()
                }
            self.assertIn("ix_technical_minute_bars_close", indexes)

        source = (
            Path(__file__).resolve().parents[1]
            / "database/technical_bar_store.py"
        ).read_text(encoding="utf-8")
        start = source.index("def _persist_bars_once(")
        end = source.index("\n\ndef persist_bars(", start)
        block = source[start:end]
        self.assertLess(
            block.index("connection.commit()"),
            block.index("SELECT COUNT(*)"),
        )

    def test_live_flush_releases_writer_between_batches_and_skips_counts(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "technical.db"
            store.bootstrap_schema(path)
            with sqlite3.connect(path) as other:
                other.execute("CREATE TABLE writer_probe(value INTEGER)")
            observations = []
            def other_writer(_delay):
                with sqlite3.connect(path, timeout=0) as other:
                    other.execute("INSERT INTO writer_probe VALUES(1)")
                    observations.append(other.execute(f"SELECT COUNT(*) FROM {store.TABLE}").fetchone()[0])
            bars = [bar(symbol=f"COIN{i}USDT") for i in range(35)]
            with mock.patch.object(store.time, "sleep", side_effect=other_writer):
                result = store.persist_bars(path, bars, now_ms=NOW, include_counts=False)
            self.assertEqual(observations, [16, 32])
            self.assertEqual(result["upserted"], 35)
            self.assertIsNone(result["row_count"])
            self.assertFalse(result["historical_counts_computed"])
            self.assertIn("max_write_transaction", result["timings_ms"])

    def test_partial_flush_retry_is_idempotent(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "technical.db"
            store.bootstrap_schema(path)
            original = store._upsert_prepared
            calls = 0
            def fail_second_batch(connection, prepared):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise sqlite3.OperationalError("database is locked")
                return original(connection, prepared)
            bars = [bar(symbol=f"COIN{i}USDT") for i in range(35)]
            with mock.patch.object(store, "_upsert_prepared", side_effect=fail_second_batch), mock.patch.object(store.time, "sleep"):
                result = store.persist_bars(path, bars, now_ms=NOW, include_counts=False)
            with sqlite3.connect(path) as con:
                self.assertEqual(con.execute(f"SELECT COUNT(*) FROM {store.TABLE}").fetchone()[0], 35)
            self.assertEqual(result["upserted"], 35)

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
