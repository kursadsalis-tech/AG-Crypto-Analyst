from pathlib import Path
import tempfile
import unittest

from scripts import cloud_job_runner
from scripts import install_ubuntu_cloud_runtime as installer


class CloudRuntimeTest(unittest.TestCase):
    def test_systemd_contract_is_fail_closed_and_non_retrospective(self):
        units = installer.payloads(
            Path("/opt/agca/app"),
            Path("/opt/agca/venv/bin/python"),
            "agca",
            "agca",
        )
        self.assertEqual(set(units), {
            "agca-live-cycle.service",
            "agca-live-cycle.timer",
            "agca-hourly-forecast.service",
            "agca-hourly-forecast.timer",
        })
        self.assertIn("Persistent=false", units["agca-live-cycle.timer"])
        self.assertIn("*-*-* *:00/5:00", units["agca-live-cycle.timer"])
        self.assertIn(
            "*-*-* *:02,07,12:00",
            units["agca-hourly-forecast.timer"],
        )
        self.assertNotIn(
            "AGCA_REMOTE_DATABASE_URL",
            "\n".join(units.values()),
        )
        self.assertIn(
            "scripts.cloud_job_runner analysis.forward_shadow_cycle",
            units["agca-live-cycle.service"],
        )

    def test_job_runner_module_allowlist(self):
        self.assertEqual(cloud_job_runner.ALLOWED_MODULES, {
            "analysis.forward_shadow_cycle",
            "decision_engine.hourly_forecast_job",
        })
        with self.assertRaises(SystemExit):
            cloud_job_runner.run_module(
                "not.allowed",
                str(Path(tempfile.gettempdir()) / "agca-test.lock"),
            )


if __name__ == "__main__":
    unittest.main()
