import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from analysis import hierarchical_market_context as hierarchy


class HierarchicalMarketContextTests(unittest.TestCase):
    def setUp(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.candles = []
        for index in range(24 * 150):
            opened = start + timedelta(hours=index)
            price = 100.0 + index * 0.01
            self.candles.append({
                "open_time": opened,
                "close_time": opened + timedelta(hours=1) - timedelta(milliseconds=1),
                "open": price, "high": price + .1, "low": price - .1,
                "close": price + .02, "quote_volume": 1000.0,
                "taker_buy_quote_volume": 550.0,
            })

    def test_aligned_layers_confirm_same_direction_only(self):
        index = len(self.candles) - 1
        context = hierarchy.build(
            self.candles, index, self.candles, index, True
        )
        self.assertEqual(context["state"], "ALIGNED")
        self.assertEqual(context["direction"], "UP")
        self.assertTrue(hierarchy.confirms_forecast(context, "UP"))
        self.assertFalse(hierarchy.confirms_forecast(context, "DOWN"))
        self.assertFalse(context["action_authority"])

    def test_risk_block_is_final(self):
        index = len(self.candles) - 1
        context = hierarchy.build(
            self.candles, index, self.candles, index, False
        )
        self.assertIn("RISK_BLOCK", context["reasons"])
        self.assertFalse(hierarchy.confirms_forecast(context, "UP"))

    def test_conflicting_setup_cannot_become_signal(self):
        values = {name: 0.0 for name in hierarchy.mtf.FEATURE_NAMES}
        values.update({
            "higher_timeframe_score": 1.0,
            "lower_timeframe_score": 1.0,
            "4h_long_return": -0.02,
            "4h_mean_spread": -0.01,
            "1h_latest_return": 0.01,
            "1h_mean_spread": 0.01,
        })
        vector = [values[name] for name in hierarchy.mtf.FEATURE_NAMES]
        with mock.patch.object(hierarchy.mtf, "vector", return_value=vector):
            context = hierarchy.build(
                self.candles, len(self.candles) - 1,
                self.candles, len(self.candles) - 1, True,
            )
        self.assertIn("BIG_PICTURE_SETUP_CONFLICT", context["reasons"])
        self.assertEqual(context["direction"], "NEUTRAL")

    def test_missing_flow_does_not_invent_confirmation(self):
        for candle in self.candles[-24:]:
            candle["taker_buy_quote_volume"] = None
        context = hierarchy.build(
            self.candles, len(self.candles) - 1,
            self.candles, len(self.candles) - 1, True,
        )
        self.assertEqual(context["flow"]["state"], "MISSING")
        self.assertLess(context["confirmations"], 4)

    def test_soft_disagreement_reduces_confidence_without_hard_block(self):
        index = len(self.candles) - 1
        context = hierarchy.build(
            self.candles, index, self.candles, index, True
        )
        opposite = hierarchy.assess_forecast(context, "DOWN")
        self.assertIn(opposite["tier"], {"LOW", "MEDIUM"})
        self.assertNotEqual(opposite["tier"], "BLOCK")

    def test_risk_remains_a_hard_block_in_soft_grading(self):
        index = len(self.candles) - 1
        context = hierarchy.build(
            self.candles, index, self.candles, index, False
        )
        self.assertEqual(
            hierarchy.assess_forecast(context, "UP")["tier"], "BLOCK"
        )


if __name__ == "__main__":
    unittest.main()
