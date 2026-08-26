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

    def test_source_attests_only_when_results_exist_or_first_window_error(self):
        source = Path(cloud_worker.__file__).read_text(encoding="utf-8")
        self.assertIn("if results:", source)
        self.assertIn('_record_attestation_safely("HEALTHY", last_results)', source)
        self.assertIn("if five_key != last_attested_five:", source)

    def test_attestation_transport_failure_does_not_escape_to_worker_loop(self):
        with patch.object(
            cloud_worker.cloud_attestation,
            "record",
            side_effect=TimeoutError("mirror timeout"),
        ):
            result = cloud_worker._record_attestation_safely("HEALTHY", {})
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
