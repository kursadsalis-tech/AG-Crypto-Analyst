import os
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

from risk_manager import risk_cycle


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


if __name__ == "__main__":
    unittest.main()
