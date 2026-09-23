import unittest
from pathlib import Path


class DeployWorkerRestartTests(unittest.TestCase):
    def test_deploy_restarts_realtime_worker(self):
        script = Path("deploy/install_gce_free_worker.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "sudo systemctl restart agca-paper-realtime-worker.service",
            script,
        )
        self.assertNotIn(
            "sudo systemctl enable --now agca-paper-realtime-worker.service",
            script,
        )


if __name__ == "__main__":
    unittest.main()
