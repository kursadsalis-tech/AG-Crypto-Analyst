import unittest
from datetime import datetime, timedelta, timezone

from analysis import derivatives_multi_timeframe_direction_lab as lab


class DerivativesMultiTimeframeDirectionLabTests(unittest.TestCase):
    def test_derivatives_are_added_only_to_challenger(self):
        row = {
            "v1_features": [1.0, 2.0],
            "features": [1.0, 2.0, 3.0, 4.0],
            "technical_features": [5.0],
            "multi_timeframe_features": [6.0],
        }
        self.assertEqual(lab._features(row, False), [1.0, 2.0, 5.0, 6.0])
        self.assertEqual(lab._features(row, True), [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])

    def test_overlapping_four_hour_labels_are_excluded_per_symbol(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = [{
            "symbol": "BTCUSDT", "as_of": start + timedelta(hours=hour),
            "due_time": start + timedelta(hours=hour + 4),
        } for hour in range(8)]
        selected = lab._non_overlapping(rows)
        self.assertEqual([row["as_of"] for row in selected], [
            start, start + timedelta(hours=4)
        ])


if __name__ == "__main__":
    unittest.main()
