import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ObserverServiceContractTests(unittest.TestCase):
    def test_exact_macro_service_is_bounded_and_paper_only(self):
        service = (ROOT / "deploy/agca-exact-macro-observer.service").read_text()
        self.assertIn("Environment=AGCA_EXECUTION_MODE=PAPER_ONLY", service)
        self.assertIn("AGCA_REMOTE_MIRROR_INLINE=0", service)
        self.assertIn("TimeoutStartSec=40", service)
        self.assertIn("MemoryMax=128M", service)
        self.assertIn("CPUQuota=25%", service)
        self.assertNotIn("systemctl enable", service)
        self.assertNotIn("may_send_real_orders", service)

    def test_behavior_observer_is_bound_to_canonical_binance_source(self):
        service=(ROOT/"deploy/agca-market-behavior-observer.service").read_text()
        self.assertIn("AGCA_BINANCE_WS_OPEN_TIMEOUT_SECONDS=10", service)
        self.assertIn("TimeoutStartSec=240", service)
        self.assertNotIn("AGCA_BINANCE_REQUEST_TIMEOUT_SECONDS", service)

    def test_observer_timers_are_not_installed_by_repository_files(self):
        macro = (ROOT / "deploy/agca-exact-macro-observer.timer").read_text()
        behavior = (ROOT / "deploy/agca-market-behavior-observer.timer").read_text()
        self.assertIn("OnUnitInactiveSec=1min", macro)
        self.assertIn("Persistent=false", macro)
        self.assertIn("Persistent=false", behavior)

    def test_attestation_service_declares_both_observer_freshness_limits(self):
        service = (ROOT / "deploy/agca-cloud-attestation.service").read_text()
        self.assertIn("AGCA_MARKET_BEHAVIOR_HEALTH_MAX_AGE_SECONDS=180", service)
        self.assertIn("AGCA_EXACT_MACRO_HEALTH_MAX_AGE_SECONDS=180", service)


if __name__ == "__main__":
    unittest.main()
