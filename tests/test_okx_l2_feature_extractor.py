import unittest

from data import okx_l2_feature_extractor as extractor


INSTRUMENT = "LQTY-USDT-SWAP"


def event(timestamp, action, bids, asks):
    return {
        "instId": INSTRUMENT, "ts": str(timestamp), "action": action,
        "bids": bids, "asks": asks,
    }


class OkxL2FeatureExtractorTests(unittest.TestCase):
    def test_future_event_cannot_change_closed_minute(self):
        initial = event(
            1_000, "snapshot",
            [["99", "10", "1"]], [["101", "10", "1"]],
        )
        before = event(30_000, "update", [["99", "20", "2"]], [])
        future_a = event(60_001, "update", [], [["101", "1", "1"]])
        future_b = event(60_001, "update", [], [["101", "1000", "10"]])
        rows_a = extractor.extract([initial, before, future_a], INSTRUMENT)
        rows_b = extractor.extract([initial, before, future_b], INSTRUMENT)
        self.assertEqual(rows_a[0], rows_b[0])
        self.assertNotEqual(rows_a[1]["imbalance_5"], rows_b[1]["imbalance_5"])

    def test_add_and_remove_notional_are_measured_from_visible_changes(self):
        rows = extractor.extract([
            event(1_000, "snapshot", [["99", "10", "1"]], [["101", "10", "1"]]),
            event(20_000, "update", [["99", "15", "2"]], []),
            event(30_000, "update", [], [["101", "0", "0"]]),
            event(40_000, "update", [], [["102", "10", "1"]]),
        ], INSTRUMENT)
        row = rows[0]
        self.assertAlmostEqual(
            row["added_notional_1m"], 99 * 5 + 102 * 10
        )
        self.assertAlmostEqual(row["removed_notional_1m"], 101 * 10)
        self.assertEqual(row["snapshots_1m"], 1)
        self.assertGreater(row["spread_bps"], 0)

    def test_requires_initial_snapshot(self):
        with self.assertRaisesRegex(ValueError, "first event must be snapshot"):
            extractor.extract([
                event(1_000, "update", [["99", "1", "1"]], [["101", "1", "1"]])
            ], INSTRUMENT)


if __name__ == "__main__":
    unittest.main()
