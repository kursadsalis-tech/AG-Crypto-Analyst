import os
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

from risk_manager import risk_cycle
from risk_manager import risk_manager as manager


class RiskCycleSqliteContentionTests(unittest.TestCase):
    def test_runtime_connection_waits_through_transient_writer_lock(self):
        handle, path = tempfile.mkstemp(prefix="agca-risk-cycle-", suffix=".db")
        os.close(handle)
        blocker = sqlite3.connect(path, timeout=1, check_same_thread=False)
        try:
            blocker.execute("CREATE TABLE lock_probe(id INTEGER PRIMARY KEY, value TEXT)")
            blocker.commit()
            blocker.execute("BEGIN IMMEDIATE")
            blocker.execute("INSERT INTO lock_probe(value) VALUES('held')")

            def release_lock():
                time.sleep(0.15)
                blocker.commit()

            releaser = threading.Thread(target=release_lock)
            releaser.start()
            with mock.patch.object(risk_cycle, "DB_NAME", path):
                con = risk_cycle.runtime_connection()
                try:
                    con.execute(
                        "INSERT INTO lock_probe(value) VALUES('after-contention')"
                    )
                    con.commit()
                finally:
                    con.close()
            releaser.join(timeout=2)
            self.assertFalse(releaser.is_alive())

            check = sqlite3.connect(path)
            try:
                rows = check.execute(
                    "SELECT value FROM lock_probe ORDER BY id"
                ).fetchall()
            finally:
                check.close()
            self.assertEqual(rows, [("held",), ("after-contention",)])
        finally:
            blocker.close()
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


    def test_cycle_state_retries_with_fresh_connection(self):
        first = mock.MagicMock()
        second = mock.MagicMock()
        with (
            mock.patch.object(
                risk_cycle, "runtime_connection",
                side_effect=[first, second],
            ) as connect_mock,
            mock.patch.object(risk_cycle, "ensure_state_table"),
            mock.patch.object(
                risk_cycle, "save_state",
                side_effect=[
                    sqlite3.OperationalError("database is locked"),
                    None,
                ],
            ) as save_mock,
            mock.patch.object(risk_cycle.time, "sleep") as sleep_mock,
        ):
            risk_cycle.persist_state("SUCCESS", "ok", attempts=2)

        self.assertEqual(connect_mock.call_count, 2)
        self.assertEqual(save_mock.call_count, 2)
        first.close.assert_called_once()
        second.close.assert_called_once()
        sleep_mock.assert_called_once()


class RiskSnapshotSqliteContentionTests(unittest.TestCase):
    def test_snapshot_retry_does_not_repeat_market_data_acquisition(self):
        first = mock.MagicMock()
        second = mock.MagicMock()
        with (
            mock.patch.object(
                manager, "runtime_connection",
                side_effect=[first, second],
            ) as connect_mock,
            mock.patch.object(manager, "ensure_tables"),
            mock.patch.object(
                manager, "save_snapshot",
                side_effect=[
                    sqlite3.OperationalError("database is locked"),
                    77,
                ],
            ) as save_mock,
            mock.patch.object(manager.time, "sleep") as sleep_mock,
        ):
            snapshot_id = manager.persist_snapshot_with_retry(
                {"created_at": "test"}, [], {}, [], attempts=2
            )

        self.assertEqual(snapshot_id, 77)
        self.assertEqual(connect_mock.call_count, 2)
        self.assertEqual(save_mock.call_count, 2)
        first.rollback.assert_called_once()
        first.close.assert_called_once()
        second.close.assert_called_once()
        sleep_mock.assert_called_once()

    def test_snapshot_retry_does_not_hide_non_lock_errors(self):
        connection = mock.MagicMock()
        with (
            mock.patch.object(
                manager, "runtime_connection", return_value=connection
            ),
            mock.patch.object(manager, "ensure_tables"),
            mock.patch.object(
                manager, "save_snapshot",
                side_effect=sqlite3.OperationalError("disk I/O error"),
            ),
            mock.patch.object(manager.time, "sleep") as sleep_mock,
        ):
            with self.assertRaisesRegex(sqlite3.OperationalError, "disk I/O"):
                manager.persist_snapshot_with_retry(
                    {"created_at": "test"}, [], {}, [], attempts=3
                )

        sleep_mock.assert_not_called()
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
