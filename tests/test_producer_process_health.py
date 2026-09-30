"""Long-lived health describes loaded code even when the checkout changes."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runtime import process_identity as identity
from runtime import behavior_learning_worker, trade_learning_worker, technical_kline_worker
from scripts import collect_closed_behavior_minute as observer


class ProducerProcessHealthTests(unittest.TestCase):
    def test_process_identity_captured_once_is_not_reloaded_on_health_updates(self):
        source = {"git_sha": "a" * 40, "git_dirty": False, "pid": 101,
                  "process_started_at": "2026-09-30T01:00:00+00:00"}
        state = identity.WorkerHealthState(source)
        source["git_sha"] = "b" * 40
        with patch.object(identity.subprocess, "run", side_effect=AssertionError("health re-read checkout")):
            for status in ("HEALTHY", "DEGRADED", "HEALTHY"):
                health = state.decorate({"status": status})
                self.assertEqual(health["process_source"]["git_sha"], "a" * 40)

    def test_source_lookup_failure_is_unknown_and_never_claims_clean(self):
        with patch.object(identity.subprocess, "run", side_effect=OSError("unavailable")):
            source = identity.capture_process_source()
        self.assertIsNone(source["git_sha"])
        self.assertIsNone(source["git_dirty"])
        self.assertGreater(source["pid"], 0)
        self.assertTrue(source["process_started_at"])

    def test_success_error_timestamps_survive_running_and_recovery(self):
        state = identity.WorkerHealthState()
        state.decorate({"status": "HEALTHY", "at": "success1"})
        state.decorate({"status": "DEGRADED", "at": "failure1", "error": "cache failed"})
        running = state.decorate({"status": "RUNNING", "at": "cycle2"})
        self.assertEqual(running["last_success_at"], "success1")
        self.assertEqual(running["last_error_at"], "failure1")
        recovered = state.decorate({"status": "HEALTHY", "at": "success2"})
        self.assertEqual(recovered["last_success_at"], "success2")
        self.assertEqual(recovered["last_error_at"], "failure1")
        self.assertEqual(recovered["last_error"], "cache failed")

    def test_learning_workers_publish_identity_versions_and_failure_history(self):
        for worker in (behavior_learning_worker, trade_learning_worker):
            with self.subTest(worker=worker.__name__), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "health.json"
                with patch.object(worker, "HEALTH_PATH", path), patch.object(worker, "_HEALTH_STATE", identity.WorkerHealthState()):
                    worker.write_health({"status": "HEALTHY", "at": "success"})
                    worker.write_health({"status": "DEGRADED", "at": "error", "error": "db unavailable"})
                    health = json.loads(path.read_text())
                self.assertEqual(health["process_source"], identity.PROCESS_SOURCE)
                self.assertTrue(health["core_version"])
                self.assertTrue(health["producer_version"])
                self.assertEqual(health["last_success_at"], "success")
                self.assertEqual(health["last_error_at"], "error")
                if worker is behavior_learning_worker:
                    self.assertEqual(health["cache_contract_version"], "PAPER_BEHAVIOR_PATTERN_CACHE_V2")

    def test_technical_worker_publishes_version_and_startup_error(self):
        worker = technical_kline_worker
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            with patch.object(worker, "HEALTH_PATH", path), patch.object(worker, "_HEALTH_STATE", identity.WorkerHealthState()):
                worker.write_health("STARTUP_FAILED", error="database unavailable")
            health = json.loads(path.read_text())
        self.assertEqual(health["process_source"], identity.PROCESS_SOURCE)
        self.assertEqual(health["producer_version"], "BINANCE_TECHNICAL_KLINE_PRODUCER_V1")
        self.assertEqual(health["last_error"], "database unavailable")
        self.assertTrue(health["last_error_at"])
        self.assertIsNone(health["last_success_at"])

    def test_observer_failure_replaces_old_health_and_hash_covers_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            args = SimpleNamespace(cycle_timeout_seconds=10, health_output=str(path))
            with patch.object(observer, "_HEALTH_STATE", identity.WorkerHealthState()):
                observer.write_health(path, {"status": "HEALTHY"})
                with patch.object(observer, "run_cycle", side_effect=RuntimeError("source unavailable")), patch.object(observer.signal, "signal"), patch.object(observer.signal, "alarm"):
                    with self.assertRaises(RuntimeError):
                        observer.run_bounded_cycle(args)
            health = json.loads(path.read_text())
        digest = health.pop("health_sha256")
        self.assertEqual(digest, hashlib.sha256(json.dumps(health, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
        self.assertEqual(health["status"], "ERROR")
        self.assertEqual(health["process_source"], identity.PROCESS_SOURCE)
        self.assertTrue(health["last_success_at"])
        self.assertTrue(health["last_error_at"])
        self.assertIn("source unavailable", health["last_error"])


if __name__ == "__main__":
    unittest.main()
