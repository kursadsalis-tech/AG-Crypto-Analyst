import unittest

from data.rolling_behavior_window import aggregate_closed_minute


def sample(timestamp, oi, imbalance=0.2, oi_available=True, bid=99, ask=101,
           buy_notional=10, sell_notional=5):
    return {
        "instrument": "AAA-USDT-SWAP", "collected_timestamp_ms": timestamp,
        "required_sources_available": True,
        "order_book": {"best_bid": bid, "best_ask": ask, "spread_bps": 1,
                       "depths": {"20": {"imbalance": imbalance}}},
        "recent_trades": {
            "taker_imbalance": -0.1,
            "buy_notional_usd_estimate": buy_notional,
            "sell_notional_usd_estimate": sell_notional,
        },
        "open_interest": {"usd": oi, "available": oi_available},
        "funding": {"rate": 0.0001},
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

    def test_closed_minute_has_compact_ohlc_and_aggressive_flow(self):
        end = 120_000
        bundle = {"liquidation_channel_healthy": True, "instruments": [
            sample(61_000, 100, bid=99, ask=101, buy_notional=10, sell_notional=1),
            sample(75_000, 101, bid=101, ask=103, buy_notional=20, sell_notional=2),
            sample(90_000, 102, bid=98, ask=100, buy_notional=30, sell_notional=3),
            sample(110_000, 103, bid=100, ask=102, buy_notional=40, sell_notional=4),
        ]}
        row = aggregate_closed_minute([bundle], end)["rows"][0]
        self.assertEqual(row["mid_price_first"], 100)
        self.assertEqual(row["mid_price_high"], 102)
        self.assertEqual(row["mid_price_low"], 99)
        self.assertEqual(row["mid_price_last"], 101)
        self.assertEqual(row["aggressive_buy_notional_usd"], 100)
        self.assertEqual(row["aggressive_sell_notional_usd"], 10)

    def test_unavailable_open_interest_is_optional_and_explicit(self):
        end = 120_000
        bundle = {"liquidation_channel_healthy": True, "instruments": [
            sample(61_000, 0, oi_available=False),
            sample(75_000, 0, oi_available=False),
            sample(90_000, 0, oi_available=False),
            sample(110_000, 0, oi_available=False),
        ]}
        row = aggregate_closed_minute([bundle], end)["rows"][0]
        self.assertTrue(row["eligible_for_learning"])
        self.assertFalse(row["quality_gates"]["open_interest_available"])
        self.assertEqual(row["open_interest_usd_change"], 0)

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
