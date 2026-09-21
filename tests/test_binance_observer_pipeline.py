"""Real Binance-native observer evidence must survive the existing minute contract.

No network, DB writes, new model, or real order capability.
"""
import unittest

from data.binance_live_evidence import summarize_instrument
from data.live_behavior_evidence_bundle import build_bundle
from data.rolling_behavior_window import aggregate_closed_minute


END_MS = 1_800_000_060_000
SYMBOL = "1000PEPEUSDT"
ROW = {"instrument": SYMBOL, "exchange": "BINANCE_USD_M"}


def wrap(payload, stamp):
    return {"payload": payload, "received_ms": stamp,
            "round_trip_ms": 80}


def book_levels(first, step, reverse):
    return [[str(first - i*step if reverse else first + i*step), "100"]
            for i in range(50)]


def sample(stamp):
    return summarize_instrument(
        ROW,
        depth=wrap({
            "lastUpdateId": 123, "E": stamp-150, "T": stamp-160,
            "bids": book_levels(1., 0.00001, True),
            "asks": book_levels(1.0001, 0.00001, False),
        }, stamp),
        trades=wrap([
            {"time": stamp-250, "price": "1.0", "qty": "4",
             "isBuyerMaker": False},
            {"time": stamp-200, "price": "1.0", "qty": "2",
             "isBuyerMaker": True},
        ], stamp),
        open_interest=wrap({
            "symbol": SYMBOL, "openInterest": "1000", "time": stamp-200,
        }, stamp),
        funding=wrap({
            "symbol": SYMBOL, "lastFundingRate": "0.0001",
            "nextFundingTime": stamp+3_600_000, "time": stamp-200,
        }, stamp),
    )


class BinanceObserverPipelineTests(unittest.TestCase):
    def test_native_closed_minute_populates_existing_decision_schema(self):
        snapshots = [sample(END_MS-50_000+i*10_000) for i in range(4)]
        self.assertTrue(all(s["required_sources_available"] for s in snapshots))
        bundles = [build_bundle({
            "instruments": [state],
        }, {"state": "AVAILABLE_NO_EVENT_IN_WINDOW", "events": []})
            for state in snapshots]
        report = aggregate_closed_minute(bundles, END_MS)
        self.assertEqual(report["row_count"], 1)
        self.assertEqual(report["eligible_row_count"], 1)
        row = report["rows"][0]
        self.assertEqual(row["instrument"], SYMBOL)
        self.assertTrue(row["eligible_for_learning"])
        self.assertLess(row["book_imbalance_20_mean"], 0)
        self.assertGreater(row["taker_imbalance_snapshot_mean"], 0)
        self.assertEqual(row["sample_count"], 4)
        self.assertTrue(all(row["quality_gates"].values()))

    def test_missing_one_source_never_enters_eligible_universe(self):
        snapshots = [sample(END_MS-50_000+i*10_000) for i in range(4)]
        snapshots[2]["required_sources_available"] = False
        snapshots[2]["source_states"]["open_interest"] = "MISSING"
        bundles = [build_bundle({"instruments": [state]}, {
            "state": "AVAILABLE_NO_EVENT_IN_WINDOW", "events": [],
        }) for state in snapshots]
        report = aggregate_closed_minute(bundles, END_MS)
        self.assertEqual(report["eligible_row_count"], 0)
        self.assertFalse(report["rows"][0]["quality_gates"]["required_sources"])

    def test_unconfirmed_liquidation_channel_blocks_eligible_behavior(self):
        snapshots = [sample(END_MS-50_000+i*10_000) for i in range(4)]
        bundles = [build_bundle({"instruments": [state]}, {
            "state": "SUBSCRIPTION_NOT_CONFIRMED", "events": [],
        }) for state in snapshots]
        self.assertEqual(aggregate_closed_minute(bundles, END_MS)["eligible_row_count"], 0)


if __name__ == "__main__":
    unittest.main()
