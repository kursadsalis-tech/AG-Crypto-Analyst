import tempfile
import unittest
from pathlib import Path

from scripts.observer_deployment_readiness import REQUIRED_FILES, build_readiness


class ObserverDeploymentReadinessTests(unittest.TestCase):
    def test_repository_observer_package_is_ready_but_not_installed(self):
        result = build_readiness()
        self.assertTrue(result["ready_for_controlled_cloud_install"])
        self.assertEqual(result["failure_count"], 0)
        self.assertFalse(result["cloud_install_performed"])
        self.assertFalse(result["timers_started"])
        self.assertEqual(len(result["required_file_sha256"]), len(REQUIRED_FILES))
        self.assertTrue(result["paper_authority_safe"])
        self.assertTrue(all(result["local_only_tables"].values()))

    def test_missing_package_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = build_readiness(Path(tmp))
        self.assertFalse(result["ready_for_controlled_cloud_install"])
        self.assertGreater(result["failure_count"], 0)


if __name__ == "__main__":
    unittest.main()
