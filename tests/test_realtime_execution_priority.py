import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RealtimeExecutionPriorityTests(unittest.TestCase):
    def test_opportunity_scan_isolated_from_execution_process_state(self):
        source = (ROOT / "runtime/paper_realtime_worker.py").read_text(
            encoding="utf-8"
        )
        process_start = source.index("    def process_state(")
        process_end = source.index("    async def on_state(", process_start)
        process_block = source[process_start:process_end]
        scan_start = source.index("    def scan_once(")
        scan_end = source.index("    async def scan_loop(", scan_start)
        scan_block = source[scan_start:scan_end]

        self.assertNotIn("select_new_positions(", process_block)
        self.assertIn("fill_pending_from_live_state(", process_block)
        self.assertIn("position_management.process_market_state(", process_block)
        self.assertIn("select_new_positions(", scan_block)
        self.assertIn(
            'thread_name_prefix="agca-paper-scan"',
            source,
        )
        self.assertIn("scan_loop(),", source)

    def test_scan_connection_is_bounded_and_schema_free(self):
        source = (ROOT / "runtime/paper_realtime_worker.py").read_text(
            encoding="utf-8"
        )
        start = source.index("    def scan_connection():")
        end = source.index("    def latest_any_execution_state(", start)
        block = source[start:end]

        self.assertIn("AGCA_SCAN_SQLITE_TIMEOUT_SECONDS", block)
        self.assertIn("PRAGMA busy_timeout=", block)
        self.assertNotIn("ensure_tables(", block)
        self.assertNotIn("position_management.ensure_tables(", block)

    def test_thread_local_runtime_connections_do_not_repeat_schema_ddl(self):
        source = (ROOT / "runtime/paper_realtime_worker.py").read_text(
            encoding="utf-8"
        )
        startup_end = source.index("    decision_executor = ThreadPoolExecutor(")
        self.assertIn("ensure_tables(con)", source[:startup_end])

        for name, end_name in (
            ("universe_connection", "refresh_symbol_cache_once"),
            ("decision_connection", "scan_connection"),
        ):
            start = source.index(f"    def {name}():")
            end = source.index(f"    def {end_name}(", start)
            block = source[start:end]
            self.assertNotIn("ensure_tables(", block)

    def test_realtime_thread_connections_use_plain_sqlite_after_startup(self):
        source = (ROOT / "runtime/paper_realtime_worker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("def _local_sqlite_connection(", source)
        self.assertIn("sqlite3.connect(DB_NAME", source)

        for name, end_name in (
            ("universe_connection", "refresh_symbol_cache_once"),
            ("decision_connection", "scan_connection"),
            ("scan_connection", "latest_any_execution_state"),
            ("maintenance_once", "maintenance"),
        ):
            start = source.index(f"    def {name}(")
            end = source.index(f"    def {end_name}(", start)
            block = source[start:end]
            self.assertIn("_local_sqlite_connection(", block)
            self.assertNotIn("connect(DB_NAME", block)
            self.assertNotIn("ensure_tables(", block)

    def test_scan_failure_updates_completion_and_duration_telemetry(self):
        source = (ROOT / "runtime/paper_realtime_worker.py").read_text(
            encoding="utf-8"
        )
        start = source.index("    def scan_once():")
        end = source.index("    async def scan_loop(", start)
        block = source[start:end]
        self.assertIn("finally:", block)
        self.assertIn("last_scan_completed_at = completed.isoformat()", block)
        self.assertIn("last_scan_duration_ms = round(", block)
        self.assertIn("last_scan_error = type(exc).__name__", block)

    def test_scan_runtime_observability_is_exposed_in_health(self):
        source = (ROOT / "runtime/paper_realtime_worker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"scan_runtime": {', source)
        self.assertIn('"started_at": last_scan_started_at', source)
        self.assertIn('"completed_at": last_scan_completed_at', source)
        self.assertIn('"duration_ms": last_scan_duration_ms', source)

    def test_execution_executor_is_distinct_from_scan_executor(self):
        source = (ROOT / "runtime/paper_realtime_worker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'thread_name_prefix="agca-paper-execution"',
            source,
        )
        self.assertIn(
            'thread_name_prefix="agca-paper-scan"',
            source,
        )
        self.assertIn("scan_executor.shutdown(wait=True)", source)


if __name__ == "__main__":
    unittest.main()
