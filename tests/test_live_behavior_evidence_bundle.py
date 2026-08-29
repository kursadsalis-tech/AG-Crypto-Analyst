import unittest

from data.live_behavior_evidence_bundle import build_bundle


class LiveBehaviorEvidenceBundleTests(unittest.TestCase):
    def test_joins_deep_event_and_escalates_outside_event(self):
        market = {"instruments": [{"instrument": "A", "required_sources_available": True}]}
        liquidations = {"state": "AVAILABLE_EVENTS_OBSERVED", "events": [
            {"instrument": "A", "liquidated_side": "LONG", "notional_usd_estimate": 10,
             "timestamp_ms": 1},
            {"instrument": "B", "liquidated_side": "SHORT", "notional_usd_estimate": 20,
             "timestamp_ms": 2},
        ]}
        result = build_bundle(market, liquidations)
        self.assertTrue(result["liquidation_channel_healthy"])
        self.assertEqual(result["instruments"][0]["liquidations"]["event_count"], 1)
        self.assertEqual(result["attention_escalation_instruments"], ["B"])
        self.assertFalse(result["trade_signal_generated"])

    def test_healthy_empty_window_is_not_missing(self):
        result = build_bundle(
            {"instruments": [{"instrument": "A"}]},
            {"state": "AVAILABLE_NO_EVENT_IN_WINDOW", "events": []},
        )
        self.assertTrue(result["liquidation_channel_healthy"])
        self.assertEqual(result["instruments"][0]["liquidations"]["event_count"], 0)


if __name__ == "__main__":
    unittest.main()
