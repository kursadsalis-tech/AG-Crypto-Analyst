import unittest

from data.okx_liquidation_channel import classify, normalize_events


class OkxLiquidationChannelTests(unittest.TestCase):
    def test_confirmed_channel_without_event_is_not_missing_data(self):
        result = classify([{"event": "subscribe", "data": []}])
        self.assertEqual(result["state"], "AVAILABLE_NO_EVENT_IN_WINDOW")
        self.assertTrue(result["subscription_confirmed"])

    def test_counts_observed_event_rows(self):
        result = classify([
            {"event": "subscribe", "data": []},
            {"event": None, "data": [{}, {}]},
        ])
        self.assertEqual(result["state"], "AVAILABLE_EVENTS_OBSERVED")
        self.assertEqual(result["event_row_count"], 2)

    def test_channel_error_is_distinct(self):
        result = classify([{"event": "error", "data": []}])
        self.assertEqual(result["state"], "CHANNEL_ERROR")

    def test_normalizes_attributable_direction_and_notional(self):
        rows = [{
            "instId": "BTC-USDT-SWAP",
            "details": [
                {"side": "sell", "posSide": "long", "bkPx": "100", "sz": "2", "ts": "123"},
                {"side": "buy", "posSide": "short", "bkPx": "101", "sz": "3", "ts": "124"},
            ],
        }]
        events, invalid = normalize_events(rows, {"BTC-USDT-SWAP": 0.01})
        self.assertEqual(invalid, 0)
        self.assertEqual([row["liquidated_side"] for row in events], ["LONG", "SHORT"])
        self.assertEqual(events[0]["notional_usd_estimate"], 2.0)
        self.assertAlmostEqual(events[1]["notional_usd_estimate"], 3.03)

    def test_rejects_malformed_event_detail(self):
        events, invalid = normalize_events([{"instId": "A", "details": [{"sz": "1"}]}])
        self.assertEqual(events, [])
        self.assertEqual(invalid, 1)


if __name__ == "__main__":
    unittest.main()
