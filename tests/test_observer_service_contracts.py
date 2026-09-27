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
        self.assertIn("Type=simple", service)
        self.assertIn("--continuous", service)
        self.assertIn("Restart=on-failure", service)
        self.assertIn("TimeoutStopSec=15", service)
        self.assertNotIn("AGCA_BINANCE_REQUEST_TIMEOUT_SECONDS", service)

    def test_macro_timer_is_not_installed_by_repository_files(self):
        macro = (ROOT / "deploy/agca-exact-macro-observer.timer").read_text()
        self.assertIn("OnUnitInactiveSec=1min", macro)
        self.assertIn("Persistent=false", macro)

    def test_technical_kline_worker_is_long_lived_binance_paper_only(self):
        service = (ROOT / "deploy/agca-technical-kline-worker.service").read_text()
        self.assertIn("Type=simple", service)
        self.assertIn("Environment=AGCA_EXECUTION_MODE=PAPER_ONLY", service)
        self.assertIn("AGCA_BINANCE_WS_OPEN_TIMEOUT_SECONDS=10", service)
        self.assertIn("-m runtime.technical_kline_worker", service)
        self.assertIn("Restart=always", service)
        self.assertIn("MemoryMax=256M", service)
        self.assertIn("CPUQuota=40%", service)

    def test_behavior_learning_specialist_is_low_priority_and_paper_only(self):
        service = (
            ROOT / "deploy/agca-behavior-learning-worker.service"
        ).read_text()
        self.assertIn("Type=simple", service)
        self.assertIn("Environment=AGCA_EXECUTION_MODE=PAPER_ONLY", service)
        self.assertIn("-m runtime.behavior_learning_worker", service)
        self.assertIn("Restart=always", service)
        self.assertIn("Nice=15", service)
        self.assertIn("CPUQuota=50%", service)
        self.assertIn("MemoryMax=256M", service)

    def test_trade_learning_specialist_is_low_priority_and_paper_only(self):
        service = (
            ROOT / "deploy/agca-trade-learning-worker.service"
        ).read_text()
        self.assertIn("Type=simple", service)
        self.assertIn("Environment=AGCA_EXECUTION_MODE=PAPER_ONLY", service)
        self.assertIn("-m runtime.trade_learning_worker", service)
        self.assertIn("Restart=always", service)
        self.assertIn("Nice=15", service)
        self.assertIn("CPUQuota=50%", service)
        self.assertIn("MemoryMax=384M", service)

    def test_realtime_worker_requires_background_behavior_cache(self):
        service = (ROOT / "deploy/agca-paper-realtime-worker.service").read_text()
        self.assertIn(
            "Environment=AGCA_BEHAVIOR_PATTERN_CACHE_REQUIRED=1",
            service,
        )

    def test_attestation_service_declares_both_observer_freshness_limits(self):
        service = (ROOT / "deploy/agca-cloud-attestation.service").read_text()
        self.assertIn("AGCA_MARKET_BEHAVIOR_HEALTH_MAX_AGE_SECONDS=180", service)
        self.assertIn("AGCA_EXACT_MACRO_HEALTH_MAX_AGE_SECONDS=180", service)


if __name__ == "__main__":
    unittest.main()
