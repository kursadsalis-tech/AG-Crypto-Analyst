import os
import sqlite3
import tempfile
import types
import unittest
from unittest import mock

from database import runtime_store
from scripts.configure_remote_runtime import build_connection_url


class RuntimeStoreTests(unittest.TestCase):
    def setUp(self):
        runtime_store._retry_after = 0.0

    def test_without_remote_url_behaves_like_sqlite(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as handle:
            with mock.patch.dict(os.environ, {}, clear=True):
                con = runtime_store.connect(handle.name)
                con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, value TEXT)")
                con.execute("INSERT INTO events(value) VALUES (?)", ("local",))
                con.commit()
                self.assertEqual(con.execute("SELECT value FROM events").fetchone()[0], "local")
                con.close()

    def test_remote_failure_does_not_break_local_commit(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as handle:
            with mock.patch.dict(os.environ, {runtime_store.REMOTE_URL_ENV: "postgresql://invalid"}):
                fake_psycopg = types.SimpleNamespace(
                    connect=mock.Mock(side_effect=RuntimeError("remote unavailable"))
                )
                with mock.patch.dict("sys.modules", {"psycopg": fake_psycopg}):
                    con = runtime_store.connect(handle.name)
                    con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, value TEXT)")
                    con.execute("INSERT INTO events(value) VALUES (?)", ("local",))
                    con.commit()
                    con.close()
                check = sqlite3.connect(handle.name)
                self.assertEqual(check.execute("SELECT value FROM events").fetchone()[0], "local")
                check.close()

    def test_rollback_discards_pending_remote_tables(self):
        con = runtime_store.connect(":memory:")
        con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY)")
        con.rollback()
        self.assertEqual(con._remote_dirty_tables, set())
        con.close()

    def test_context_manager_uses_dual_write_commit(self):
        con = runtime_store.connect(":memory:")
        with mock.patch.object(runtime_store, "_mirror_after_local_commit") as mirror:
            with con:
                con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY)")
                con.execute("INSERT INTO events VALUES (1)")
            mirror.assert_called_once()
        con.close()

    def test_environment_url_takes_priority_over_keychain(self):
        with mock.patch.dict(os.environ, {runtime_store.REMOTE_URL_ENV: "postgresql://env"}):
            with mock.patch("subprocess.run") as run:
                self.assertEqual(runtime_store._remote_url(), "postgresql://env")
                run.assert_not_called()

    def test_connection_builder_encodes_password_and_requires_ssl(self):
        result = build_connection_url(
            "postgresql://user:[YOUR-PASSWORD]@example.test/postgres",
            "a/b@c",
        )
        self.assertEqual(
            result,
            "postgresql://user:a%2Fb%40c@example.test/postgres?sslmode=require",
        )


if __name__ == "__main__":
    unittest.main()
