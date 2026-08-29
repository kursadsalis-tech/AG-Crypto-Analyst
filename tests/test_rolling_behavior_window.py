import unittest

from data.rolling_behavior_window import aggregate_closed_minute


def sample(timestamp, oi, imbalance=0.2):
    return {
        "instrument": "AAA-USDT-SWAP", "collected_timestamp_ms": timestamp,
        "required_sources_available": True,
        "order_book": {"spread_bps": 1, "depths": {"20": {"imbalance": imbalance}}},
        "recent_trades": {"taker_imbalance": -0.1},
        "open_interest": {"usd": oi}, "funding": {"rate": 0.0001},
        "liquidations": {"events": []},
    }


class RollingBehaviorWindowTests(unittest.TestCase):
    def test_only_closed_minute_samples_are_used(self):
        end = 120_000
        bundle = {"liquidation_channel_healthy": True, "instruments": [
            sample(61_000, 100), sample(75_000, 101), sample(90_000, 103),
            sample(110_000, 105), sample(120_000, 999),
        ]}
        result = aggregate_closed_minute([bundle], end)
        row = result["rows"][0]
        self.assertEqual(row["sample_count"], 4)
        self.assertEqual(row["open_interest_usd_change"], 5)
        self.assertTrue(row["eligible_for_learning"])

    def test_future_sample_cannot_change_prior_minute(self):
        end = 120_000
        base = {"liquidation_channel_healthy": True,
                "instruments": [sample(61_000, 100), sample(75_000, 101),
                                sample(90_000, 102), sample(110_000, 103)]}
        before = aggregate_closed_minute([base], end)
        base["instruments"].append(sample(130_000, 999, -1))
        after = aggregate_closed_minute([base], end)
        self.assertEqual(before["rows"], after["rows"])

    def test_gap_or_missing_channel_blocks_learning(self):
        bundle = {"liquidation_channel_healthy": False, "instruments": [
            sample(61_000, 100), sample(70_000, 101), sample(80_000, 102), sample(119_000, 103),
        ]}
        row = aggregate_closed_minute([bundle], 120_000)["rows"][0]
        self.assertFalse(row["eligible_for_learning"])
        self.assertFalse(row["quality_gates"]["maximum_gap"])
        self.assertFalse(row["quality_gates"]["liquidation_channel"])

    def test_deduplicates_repeated_liquidation_event(self):
        event = {"instrument": "AAA-USDT-SWAP", "timestamp_ms": 80_000,
                 "liquidated_side": "LONG", "bankruptcy_price": 10,
                 "contracts": 2, "notional_usd_estimate": 20}
        rows = [sample(61_000, 100), sample(75_000, 101), sample(90_000, 102), sample(110_000, 103)]
        for row in rows:
            row["liquidations"] = {"events": [event]}
        result = aggregate_closed_minute(
            [{"liquidation_channel_healthy": True, "instruments": rows}], 120_000)
        row = result["rows"][0]
        self.assertEqual(row["liquidation_event_count"], 1)
        self.assertEqual(row["long_liquidation_notional_usd_estimate"], 20)


if __name__ == "__main__":
    unittest.main()
