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
