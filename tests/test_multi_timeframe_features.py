import math
import unittest
from datetime import datetime, timedelta, timezone

from analysis import multi_timeframe_features as mtf


class MultiTimeframeFeatureTests(unittest.TestCase):
    @staticmethod
    def candles(hours=24 * 150):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = []
        for index in range(hours):
            opened = start + timedelta(hours=index)
            close = 100.0 + index * 0.01 + math.sin(index / 17.0)
            rows.append({
                "open_time": opened,
                "close_time": opened + timedelta(hours=1) - timedelta(milliseconds=1),
                "open": close - 0.02,
                "high": close + 0.10,
                "low": close - 0.10,
                "close": close,
                "quote_volume": 1000.0 + index,
            })
        return rows

    def test_vector_is_finite_and_has_fixed_shape(self):
        candles = self.candles()
        values = mtf.vector(candles, len(candles) - 1)
        self.assertEqual(len(values), len(mtf.FEATURE_NAMES))
        self.assertTrue(all(math.isfinite(value) for value in values))

    def test_future_and_incomplete_higher_timeframe_do_not_leak(self):
        candles = self.candles()
        index = len(candles) - 25
        before = mtf.vector(candles, index)
        for candle in candles[index + 1:]:
            candle.update({
                "open": 1e9, "high": 1e9, "low": 1e9,
                "close": 1e9, "quote_volume": 1e12,
            })
        self.assertEqual(before, mtf.vector(candles, index))

    def test_incomplete_month_is_excluded(self):
        candles = self.candles()
        index = len(candles) - 1
        bars = mtf._aggregate_completed(candles, index, "monthly")
        self.assertTrue(bars)
        month_start, month_end = mtf._bucket_bounds(candles[index]["open_time"], "monthly")
        if candles[index]["close_time"] < month_end - timedelta(milliseconds=1):
            self.assertNotEqual(bars[-1]["open"], next(
                row["open"] for row in candles if row["open_time"] == month_start
            ))


if __name__ == "__main__":
    unittest.main()
