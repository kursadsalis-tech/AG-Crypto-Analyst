import unittest

from paper_trading.risk_tiering import tier_for


class RiskTieringTests(unittest.TestCase):
    def candidate(self):
        return {"direction": "LONG", "experiment_tier": "EXPLORATORY_LEARNING",
                "evidence": {"event": {"kind": "MARKET_STRUCTURE",
                                        "reported_direction": "OBSERVED"}}}

    def test_one_valid_behavior_signal_is_tradeable_at_small_one_x(self):
        tier = tier_for(self.candidate())
        self.assertEqual((tier.leverage, tier.margin_fraction), (1.0, .005))
        self.assertEqual(tier.signal_sources, ("MARKET_BEHAVIOR",))

    def test_independent_aligned_sources_raise_but_never_exceed_five_x(self):
        candidate = self.candidate()
        candidate["experiment_tier"] = "PROMOTION_READY"
        candidate["evidence"]["directional_signals"] = [
            {"source": name, "direction": "LONG"}
            for name in ("TECHNICAL", "NEWS", "ONCHAIN", "DERIVATIVES", "WHALE")
        ]
        tier = tier_for(candidate)
        self.assertEqual(tier.leverage, 5.0)
        self.assertEqual(tier.margin_fraction, .02)

    def test_opposing_or_duplicate_sources_do_not_inflate_conviction(self):
        candidate = self.candidate()
        candidate["evidence"]["directional_signals"] = [
            {"source": "NEWS", "direction": "SHORT"},
            {"source": "NEWS", "direction": "LONG"},
            {"source": "NEWS", "direction": "LONG"},
        ]
        tier = tier_for(candidate)
        self.assertEqual(tier.independent_signal_count, 2)
        self.assertEqual(tier.leverage, 2.0)


if __name__ == "__main__":
    unittest.main()
