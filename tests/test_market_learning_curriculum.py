import unittest

from data.market_learning_curriculum import build_curriculum


class MarketLearningCurriculumTests(unittest.TestCase):
    def universe(self, count=24):
        return {
            "version": "TEST",
            "snapshot_timestamp_ms": 2_000_000_000_000,
            "selection_contract": {"uses_future_returns_or_model_scores": False},
            "selected": [
                {"instrument": f"C{i:02d}-USDT-SWAP", "listing_timestamp_ms": 1,
                 "quote_volume_24h_estimate_usd": float(count - i)}
                for i in range(count)
            ],
        }

    def test_uses_core_and_liquidity_bands_without_example_list(self):
        result = build_curriculum(self.universe(), dates=("2026-08-01",),
                                  core_count=4, bands=4, per_band=2)
        self.assertEqual(result["core_instruments"], [
            "C00-USDT-SWAP", "C01-USDT-SWAP", "C02-USDT-SWAP", "C03-USDT-SWAP",
        ])
        self.assertEqual(result["curriculum_instrument_count"], 12)
        self.assertFalse(result["selection_contract"]["uses_user_example_coin_list"])
        self.assertFalse(result["ready_for_unbiased_historical_model_test"])

    def test_excludes_symbol_before_minimum_listing_age(self):
        universe = self.universe(1)
        universe["selected"][0]["listing_timestamp_ms"] = 1_767_225_600_000  # 2026-01-01
        result = build_curriculum(universe, dates=("2026-02-01",),
                                  core_count=1, bands=1, per_band=0)
        self.assertEqual(result["archive_candidate_count"], 0)
        self.assertEqual(len(result["excluded_not_yet_mature"]), 1)

    def test_rejects_unproven_selection_contract(self):
        universe = self.universe()
        universe["selection_contract"]["uses_future_returns_or_model_scores"] = True
        with self.assertRaises(ValueError):
            build_curriculum(universe)


if __name__ == "__main__":
    unittest.main()
