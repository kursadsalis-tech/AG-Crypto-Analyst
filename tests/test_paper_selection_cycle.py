import ast
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
            "decision_status": "FRESH_ELIGIBLE_BEHAVIOR",
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

    def test_missing_selection_evidence_is_data_blocked_even_with_fills(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            result = self.result()
            result.pop("decision_status")
            with patch.object(cycle, "HEALTH_PATH", path), patch.object(
                cycle.crypto_profit_agent, "run_behavior_selection",
                return_value=result,
            ):
                cycle.main()
            payload = json.loads(path.read_text())
            self.assertEqual(payload["status"], "DATA_BLOCKED")
            self.assertEqual(payload["decision_status"], "NO_SELECTION_EVIDENCE")
            self.assertFalse(payload["real_order_allowed"])

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


class HourlyPaperAuthorityTests(unittest.TestCase):
    def test_hourly_forecast_has_no_paper_execution_entrypoint(self):
        """Hourly maintenance must not independently open or close PAPER positions."""
        from decision_engine import hourly_forecast_job

        source = Path(hourly_forecast_job.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = {"crypto_profit_agent", "paper_selection_cycle", "paper_realtime_worker"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertFalse(any(part in forbidden for part in alias.name.split(".")))
            elif isinstance(node, ast.ImportFrom):
                self.assertFalse(any(part in forbidden for part in (node.module or "").split(".")))
                for alias in node.names:
                    self.assertNotIn(alias.name, forbidden)
        self.assertFalse(any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"run_behavior_selection", "select_new_positions", "fill_pending", "settle_open"}
            for node in ast.walk(tree)
        ))
