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
        self.assertEqual(tier.market_type, "SPOT")

    def test_independent_aligned_sources_raise_but_never_exceed_five_x(self):
        candidate = self.candidate()
        candidate["experiment_tier"] = "PROMOTION_READY"
        candidate["evidence"]["directional_signals"] = [
            {"source": name, "direction": "LONG"}
            for name in ("TECHNICAL", "NEWS", "ONCHAIN", "DERIVATIVES", "WHALE")
        ]
        candidate["evidence"]["execution_calibration"] = {"feedback": {
            "closed_count": 60, "independent_days": 12,
            "mean_net_return_pct": .30, "cost_after_win_rate": .65,
        }}
        candidate["evidence"]["learned_pattern"] = {
            "calibrated_net_edge_pct": .30, "calibrated_win_rate": .65,
        }
        tier = tier_for(candidate)
        self.assertEqual(tier.leverage, 5.0)
        self.assertEqual(tier.margin_fraction, .02)
        self.assertEqual(tier.market_type, "USD_M_PERPETUAL")

    def test_opposing_or_duplicate_sources_do_not_inflate_conviction(self):
        candidate = self.candidate()
        candidate["evidence"]["directional_signals"] = [
            {"source": "NEWS", "direction": "SHORT"},
            {"source": "NEWS", "direction": "LONG"},
            {"source": "NEWS", "direction": "LONG"},
        ]
        tier = tier_for(candidate)
        self.assertEqual(tier.independent_signal_count, 2)
        self.assertEqual(tier.leverage, 1.0)

    def test_explicit_binance_usdm_long_stays_on_same_market_even_at_one_x(self):
        candidate = self.candidate()
        candidate["market_type"] = "USD_M_PERPETUAL"
        tier = tier_for(candidate)
        self.assertEqual((tier.leverage, tier.market_type), (1.0, "USD_M_PERPETUAL"))

    def test_invalid_explicit_market_fails_closed(self):
        candidate = self.candidate()
        candidate["market_type"] = "OTHER"
        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_CANDIDATE_MARKET_TYPE"):
            tier_for(candidate)

    def test_single_source_short_stays_one_x_perpetual(self):
        candidate = self.candidate()
        candidate["direction"] = "SHORT"
        tier = tier_for(candidate)
        self.assertEqual((tier.leverage, tier.market_type), (1.0, "USD_M_PERPETUAL"))


if __name__ == "__main__":
    unittest.main()
