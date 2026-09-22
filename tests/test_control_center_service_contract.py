import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ControlCenterServiceContractTests(unittest.TestCase):
    def test_control_center_service_is_local_read_only_paper_surface(self):
        service = (ROOT / "deploy/agca-control-center.service").read_text()
        self.assertIn("Environment=AGCA_EXECUTION_MODE=PAPER_ONLY", service)
        self.assertIn("Environment=AGCA_DATABASE_PATH=/var/lib/agca/crypto_data.db", service)
        self.assertIn("python -m control_center.server", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertNotIn("0.0.0.0", service)
        self.assertNotIn("may_send_real_orders", service)
        self.assertNotIn("systemctl enable", service)


if __name__ == "__main__":
    unittest.main()
