import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from database import technical_bar_store as store
from database.sqlite_policy import connect_local


class TechnicalWriterIsolationTests(unittest.TestCase):
    def test_slow_technical_writer_cannot_block_canonical_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            technical_db = str(Path(tmp) / "technical.db")
            canonical_db = str(Path(tmp) / "canonical.db")
            store.bootstrap_schema(technical_db)

            con = connect_local(canonical_db, timeout=0.25)
            con.execute(
                "CREATE TABLE fairness_probe("
                "id INTEGER PRIMARY KEY, value INTEGER)"
            )
            con.execute("INSERT INTO fairness_probe VALUES(1,0)")
            con.commit()
            con.close()

            now = 1_800_000_000_000
            bars = []
            for i in range(8):
                opened = now + i * 60_000
                bars.append({
                    "symbol": f"T{i}USDT",
                    "open_time_ms": opened,
                    "close_time_ms": opened + 59_999,
                    "open": 1.0,
                    "high": 1.1,
                    "low": 0.9,
                    "close": 1.0,
                    "volume": 10.0,
                    "quote_volume": 10.0,
                    "trade_count": 1,
                    "taker_buy_quote_volume": 5.0,
                    "source": "TEST",
                })

            original = store._upsert_prepared
            writer_entered = threading.Event()
            technical_error = []

            def slow_batch(connection, prepared):
                writer_entered.set()
                time.sleep(0.12 * len(prepared))
                return original(connection, prepared)

            def run_technical():
                try:
                    with mock.patch.object(
                        store, "_upsert_prepared", slow_batch
                    ):
                        store.persist_bars(
                            technical_db,
                            bars,
                            now_ms=now + 600_000,
                            include_counts=False,
                        )
                except Exception as exc:
                    technical_error.append(exc)

            thread = threading.Thread(target=run_technical)
            thread.start()
            self.assertTrue(writer_entered.wait(timeout=2))

            started = time.monotonic()
            con = connect_local(canonical_db, timeout=0.25)
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "UPDATE fairness_probe SET value=value+1 WHERE id=1"
            )
            con.commit()
            con.close()
            canonical_ms = (time.monotonic() - started) * 1000.0

            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(technical_error, [])
            self.assertLess(canonical_ms, 250.0)


if __name__ == "__main__":
    unittest.main()
