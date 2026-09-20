import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime import paper_selection_cycle as cycle


class PaperSelectionCycleTests(unittest.TestCase):
    def test_legacy_timer_delegates_without_mutating_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            with patch.object(cycle, "HEALTH_PATH", path), patch.object(
                cycle.crypto_profit_agent, "run_behavior_selection"
            ) as legacy:
                result = cycle.main()
            legacy.assert_not_called()
            payload = json.loads(path.read_text())
            self.assertEqual(payload["status"], "DELEGATED_TO_REALTIME_WORKER")
            self.assertEqual(payload["decision_status"], "LIVE_EVENT_STREAM_AUTHORITY")
            self.assertEqual(result["selected"], 0)
            self.assertFalse(payload["real_order_allowed"])
            self.assertEqual(payload["execution_mode"], "PAPER_ONLY")

    def test_non_paper_mode_is_rejected(self):
        with patch.dict("os.environ", {"AGCA_EXECUTION_MODE": "LIVE"}):
            with self.assertRaisesRegex(SystemExit, "PAPER_ONLY"):
                cycle.main()
