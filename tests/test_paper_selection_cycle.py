import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime import paper_selection_cycle as cycle


class PaperSelectionCycleTests(unittest.TestCase):
    def result(self):
        return {
            "selected": 1, "filled": 1, "closed": 0, "cancelled": 0,
            "portfolio": {"open_positions": 1},
            "trade_learning": {"status": "COLLECTING_EVIDENCE"},
        }

    def test_success_health_is_small_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            with patch.object(cycle, "HEALTH_PATH", path), patch.object(
                cycle.crypto_profit_agent, "run_behavior_selection",
                return_value=self.result(),
            ):
                cycle.main()
            payload = json.loads(path.read_text())
            self.assertEqual(payload["status"], "SUCCESS")
            self.assertEqual(payload["selected"], 1)
            self.assertFalse(payload["real_order_allowed"])
            self.assertEqual(payload["execution_mode"], "PAPER_ONLY")

    def test_failure_is_recorded_and_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            with patch.object(cycle, "HEALTH_PATH", path), patch.object(
                cycle.crypto_profit_agent, "run_behavior_selection",
                side_effect=RuntimeError("broken"),
            ):
                with self.assertRaisesRegex(RuntimeError, "broken"):
                    cycle.main()
            payload = json.loads(path.read_text())
            self.assertEqual(payload["status"], "FAILED")
            self.assertFalse(payload["real_order_allowed"])
