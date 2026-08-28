import unittest

from analysis import forecast_core_v4_direction as direction


class RollingDirectionEnsembleTests(unittest.TestCase):
    def test_blend_changes_only_direction_probabilities_and_keeps_authority_closed(self):
        class Model:
            def predict_proba(self, matrix):
                return [[0.60, 0.25, 0.15]]
        rolling = {
            "model": Model(), "classes": ("UP", "DOWN", "NEUTRAL"),
            "feature_count": 12, "blend_weight": 0.5,
            "synthesized_indicator": "NONLINEAR_TECHNICAL_DIRECTION_COMPOSITE",
        }
        row = {
            "features": [0.0] * 12,
            "technical_features": [0.0] * 24,
            "risk_eligible": False,
        }
        incumbent = {
            "p_up": 0.40, "p_down": 0.40, "p_neutral": 0.20,
            "predicted_return_pct": 1.25, "target_price": 101.25,
            "expected_trade_return_pct": 0.5, "action": "BEKLE",
            "risk_adjusted_action": "BEKLE", "expected_net_return_pct": 0.0,
        }
        result = direction.blend(rolling, row, incumbent)
        self.assertAlmostEqual(result["p_up"], 0.50)
        self.assertAlmostEqual(result["p_down"], 0.325)
        self.assertAlmostEqual(result["p_neutral"], 0.175)
        self.assertEqual(result["target_price"], incumbent["target_price"])
        self.assertEqual(result["risk_adjusted_action"], "BEKLE")
        self.assertAlmostEqual(sum(result[k] for k in ("p_up", "p_down", "p_neutral")), 1.0)


if __name__ == "__main__":
    unittest.main()
