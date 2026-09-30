import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from control_center.snapshot_cache import SnapshotCache
from scripts import panel_cache_acceptance as probe


class PanelAcceptanceTests(unittest.TestCase):
    def run_probe(self, blocked=False):
        now = [0.0]
        release = threading.Event()
        calls = []
        def compute():
            calls.append(1)
            if blocked:
                release.wait(2)
            return {"read_only": True}
        cache = SnapshotCache(compute, clock=lambda: now[0])
        def get(path):
            if path == "/healthz":
                if not blocked:
                    cache._thread.join(1)
                return {"status": 200, "latency_seconds": .01, "body": {
                    "authority": "READ_ONLY", "snapshot_cache": cache.telemetry()}}
            body, status, _ = cache.response()
            return {"status": status, "latency_seconds": .01, "body": json.loads(body)}
        def sleep(seconds):
            now[0] += seconds
        try:
            with tempfile.TemporaryDirectory() as tmp, patch.object(probe, "get", side_effect=get), \
                    patch.object(probe.time, "monotonic", side_effect=lambda: now[0]), \
                    patch.object(probe.time, "sleep", side_effect=sleep), \
                    patch.object(probe, "scheduler_sample", return_value={"thread": {"state": "R"}}), \
                    patch.object(probe, "worker_sample", return_value={
                        "source": "canonical", "quotes_ms": {"BTCUSDT": 200, "ETHUSDT": 200},
                        "receive_lag_ms": 20,
                        "runtime_safety": {
                            "health_execution_mode": "PAPER_ONLY",
                            "health_real_order_allowed": False,
                            "market_execution_mode": "PAPER_ONLY",
                            "market_real_order_allowed": False,
                        }}), \
                    patch.object(probe.subprocess, "check_output", return_value="MainPID=123\nControlGroup=/panel\n"), \
                    patch("builtins.print"):
                output = Path(tmp) / "report.json"
                status = probe.main(output)
                report = json.loads(output.read_text())
        finally:
            release.set()
            cache._thread.join(1)
            cache.close()
        return status, report, len(calls)

    def test_short_probe_accepts_two_shared_generations(self):
        status, report, calls = self.run_probe()
        self.assertEqual(status, 0)
        self.assertTrue(report["passed"])
        self.assertEqual(calls, 2)
        self.assertEqual(len(report["requests"]), 6)
        self.assertEqual(report["cold"]["status"], 503)


    def test_runtime_safety_failure_is_recorded_with_exact_payload_values(self):
        with tempfile.TemporaryDirectory() as tmp,                 patch.object(probe, "worker_sample", return_value={
                    "source": "canonical",
                    "quotes_ms": {"BTCUSDT": 200, "ETHUSDT": 200},
                    "receive_lag_ms": 20,
                    "runtime_safety": {
                        "health_execution_mode": "PAPER_ONLY",
                        "health_real_order_allowed": False,
                        "market_execution_mode": "PAPER_ONLY",
                        "market_real_order_allowed": 0,
                    },
                }),                 patch.object(probe.subprocess, "check_output",
                             return_value="MainPID=123\nControlGroup=/panel\n"),                 patch("builtins.print"):
            output = Path(tmp) / "report.json"
            status = probe.main(output)
            report = json.loads(output.read_text())
        self.assertEqual(status, 1)
        self.assertIn("runtime safety mismatch", report["failure"])
        self.assertEqual(
            report["worker_baseline"]["runtime_safety"]["market_real_order_allowed"], 0
        )
        self.assertEqual(report.get("samples"), [])

    def test_stuck_refresh_stops_at_eight_seconds_and_preserves_diagnostics(self):
        status, report, calls = self.run_probe(blocked=True)
        self.assertEqual(status, 1)
        self.assertFalse(report["passed"])
        self.assertEqual(calls, 1)
        self.assertEqual(report["duration_seconds"], 8)
        self.assertIn("within 8 seconds", report["failure"])
        self.assertEqual(report["samples"][-1]["scheduler"]["thread"]["state"], "R")
