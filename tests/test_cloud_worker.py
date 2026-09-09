import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from runtime import cloud_worker


class CloudWorkerHealthTests(unittest.TestCase):
    def test_health_serializes_nested_runtime_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            at = datetime(2026, 8, 24, 19, 3, 44, tzinfo=timezone.utc)
            with patch.object(cloud_worker, "STATE_DIR", Path(tmp)):
                cloud_worker._write_health(
                    "HEALTHY", last_results={"hourly_job": {"as_of": at}}
                )

            payload = json.loads((Path(tmp) / "worker-health.json").read_text())
            self.assertEqual(payload["status"], "HEALTHY")
            self.assertEqual(
                payload["last_results"]["hourly_job"]["as_of"],
                "2026-08-24T19:03:44+00:00",
            )

    def test_health_rejects_unknown_types(self):
        with self.assertRaises(TypeError):
            cloud_worker._json_default(object())

    def test_worker_does_not_duplicate_independent_attestation_timer(self):
        source = Path(cloud_worker.__file__).read_text(encoding="utf-8")
        self.assertNotIn("cloud_attestation.record", source)
        self.assertNotIn("_record_attestation_safely", source)
        service = Path("deploy/agca-cloud-attestation.service").read_text()
        timer = Path("deploy/agca-cloud-attestation.timer").read_text()
        self.assertIn("runtime.cloud_attestation", service)
        self.assertIn("OnUnitActiveSec=5min", timer)

    def test_behavior_selection_is_isolated_from_heavy_worker(self):
        source = Path(cloud_worker.__file__).read_text(encoding="utf-8")
        self.assertNotIn("run_behavior_selection", source)
        service = Path("deploy/agca-paper-selection.service").read_text()
        timer = Path("deploy/agca-paper-selection.timer").read_text()
        self.assertIn("runtime.paper_selection_cycle", service)
        self.assertIn("AGCA_EXECUTION_MODE=PAPER_ONLY", service)
        self.assertIn("TimeoutStartSec=120", service)
        self.assertIn("OnUnitInactiveSec=60s", timer)
        self.assertNotIn("OnCalendar=", timer)


if __name__ == "__main__":
    unittest.main()
