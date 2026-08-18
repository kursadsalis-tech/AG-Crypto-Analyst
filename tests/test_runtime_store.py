import os
import sqlite3
import tempfile
import types
import unittest
from unittest import mock

from database import runtime_store
from paper_trading import crypto_profit_agent
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

    def test_remote_failure_retries_and_keeps_runtime_table_pending(self):
        con = runtime_store.connect(":memory:")
        con.execute("CREATE TABLE paper_agent_portfolio (portfolio_id INTEGER PRIMARY KEY)")
        with mock.patch.dict(os.environ, {
            runtime_store.REMOTE_URL_ENV: "postgresql://invalid",
            "AGCA_REMOTE_RETRY_ATTEMPTS": "3",
            "AGCA_REMOTE_RETRY_BASE_SECONDS": "0",
        }):
            fake_psycopg = types.SimpleNamespace(
                connect=mock.Mock(side_effect=RuntimeError("remote unavailable"))
            )
            with mock.patch.dict("sys.modules", {"psycopg": fake_psycopg}):
                con.commit()
        self.assertEqual(fake_psycopg.connect.call_count, 3)
        self.assertIn("paper_agent_portfolio", con._remote_dirty_tables)
        health = con.execute("""
            SELECT status,error_type,consecutive_failures FROM remote_mirror_health
            WHERE table_name='paper_agent_portfolio'
        """).fetchone()
        self.assertEqual(health, ("degraded", "RuntimeError", 1))
        con.close()

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

    def test_non_runtime_tables_do_not_trigger_remote_connection(self):
        with mock.patch.dict(os.environ, {runtime_store.REMOTE_URL_ENV: "postgresql://unused"}):
            with mock.patch.object(runtime_store, "_remote_url") as remote_url:
                runtime_store._mirror_after_local_commit(
                    mock.Mock(), {"market_klines", "futures_hourly_market"}
                )
                remote_url.assert_not_called()

    def test_report_delivery_stays_pending_when_remote_mirror_fails(self):
        con = runtime_store.connect(":memory:")
        con.execute("""
            CREATE TABLE paper_agent_daily_reports (
                report_date TEXT PRIMARY KEY, remote_mirror_status TEXT,
                remote_mirrored_at TEXT, delivery_complete INTEGER
            )
        """)
        con.execute("INSERT INTO paper_agent_daily_reports VALUES('2026-08-18','PENDING',NULL,0)")
        con._remote_dirty_tables.clear()
        failed = runtime_store.MirrorResult(
            attempted=frozenset({"paper_agent_daily_reports"}),
            error_type="OperationalError",
        )
        with mock.patch.object(runtime_store, "_mirror_after_local_commit", return_value=failed):
            self.assertFalse(crypto_profit_agent._confirm_remote_report_delivery(
                con, "2026-08-18"
            ))
        row = con.execute("""
            SELECT remote_mirror_status,remote_mirrored_at,delivery_complete
            FROM paper_agent_daily_reports
        """).fetchone()
        self.assertEqual(row, ("PENDING", None, 0))
        con.close()

    def test_report_delivery_completes_after_successful_remote_mirror(self):
        con = runtime_store.connect(":memory:")
        con.execute("""
            CREATE TABLE paper_agent_daily_reports (
                report_date TEXT PRIMARY KEY, remote_mirror_status TEXT,
                remote_mirrored_at TEXT, delivery_complete INTEGER
            )
        """)
        con.execute("INSERT INTO paper_agent_daily_reports VALUES('2026-08-18','PENDING',NULL,0)")
        con._remote_dirty_tables.clear()
        success = runtime_store.MirrorResult(
            attempted=frozenset({"paper_agent_daily_reports"}),
            succeeded=frozenset({"paper_agent_daily_reports"}),
        )
        with mock.patch.object(runtime_store, "_mirror_after_local_commit", return_value=success):
            self.assertTrue(crypto_profit_agent._confirm_remote_report_delivery(
                con, "2026-08-18"
            ))
        row = con.execute("""
            SELECT remote_mirror_status,remote_mirrored_at,delivery_complete
            FROM paper_agent_daily_reports
        """).fetchone()
        self.assertEqual(row[0], "HEALTHY")
        self.assertIsNotNone(row[1])
        self.assertEqual(row[2], 1)
        con.close()


if __name__ == "__main__":
    unittest.main()
