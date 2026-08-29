import unittest

from analysis import multi_timeframe_direction_lab as lab


class _FixedModel:
    classes_ = ("DOWN", "NEUTRAL", "UP")

    def __init__(self, probabilities):
        self.probabilities = probabilities

    def predict_proba(self, matrix):
        return [self.probabilities for _ in matrix]


class MultiTimeframeDirectionLabTests(unittest.TestCase):
    @staticmethod
    def row(regime, trade_return_pct=0.5):
        return {
            "hierarchical_context": {"regime": regime},
            "actual_direction": "UP",
            "trade_return_pct": trade_return_pct,
            "features": [0.0] * 12,
            "technical_features": [0.0],
            "multi_timeframe_features": [0.0],
        }

    def test_missing_regime_expert_uses_global_fallback(self):
        global_model = _FixedModel((0.1, 0.1, 0.8))
        payload = {
            "global": global_model,
            "experts": {
                regime: {"model": None, "training_rows": 10}
                for regime in ("TREND", "RANGE", "TRANSITION", "SHOCK")
            },
        }
        score = lab._score_experts(payload, [self.row("SHOCK")])
        self.assertEqual(score["accuracy"], 1.0)
        self.assertEqual(score["expert_usage"]["GLOBAL_FALLBACK"], 1)
        self.assertEqual(score["expert_usage"]["SHOCK"], 0)

    def test_available_regime_expert_is_selected(self):
        payload = {
            "global": _FixedModel((0.8, 0.1, 0.1)),
            "experts": {
                regime: {"model": None, "training_rows": 10}
                for regime in ("TREND", "RANGE", "TRANSITION", "SHOCK")
            },
        }
        payload["experts"]["TREND"] = {
            "model": _FixedModel((0.1, 0.1, 0.8)),
            "training_rows": lab.MIN_EXPERT_ROWS,
        }
        score = lab._score_experts(payload, [self.row("TREND")])
        self.assertEqual(score["accuracy"], 1.0)
        self.assertEqual(score["expert_usage"]["TREND"], 1)
        self.assertAlmostEqual(score["directional_net_utility_pct"], 0.4)

    def test_economic_policy_rejects_small_or_uncertain_samples(self):
        predictions = [{
            "regime": "TREND", "predicted": "UP", "confidence": 0.55,
            "utility": 1.0 if index % 2 == 0 else -1.0,
        } for index in range(lab.MIN_POLICY_CELL_ROWS - 1)]
        allowed, _ = lab._learn_economic_policy(predictions)
        self.assertEqual(allowed, set())

    def test_economic_policy_accepts_only_supported_positive_cell(self):
        predictions = [{
            "regime": "TREND", "predicted": "UP", "confidence": 0.55,
            "utility": 0.2,
        } for _ in range(lab.MIN_POLICY_CELL_ROWS)]
        allowed, evidence = lab._learn_economic_policy(predictions)
        key = ("TREND", "UP", lab._confidence_band(0.55))
        self.assertEqual(allowed, {key})
        self.assertTrue(evidence["TREND|UP|2"]["accepted"])


if __name__ == "__main__":
    unittest.main()
