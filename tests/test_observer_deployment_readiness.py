import tempfile
import unittest
from pathlib import Path

from scripts.observer_deployment_readiness import REQUIRED_FILES, build_readiness


class ObserverDeploymentReadinessTests(unittest.TestCase):
    def test_vm_acceptance_is_required_after_rest_removal(self):
        result = build_readiness()
        self.assertFalse(result["ready_for_controlled_cloud_install"])
        self.assertIn("BINANCE_WS_OI_VM_ACCEPTANCE_PENDING",
                      result["failures"])
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
