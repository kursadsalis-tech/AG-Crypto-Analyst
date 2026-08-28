import math
import unittest

from analysis import advanced_technical_features as technical


class AdvancedTechnicalFeatureTests(unittest.TestCase):
    @staticmethod
    def candles(count=171):
        rows = []
        for index in range(count):
            close = 100.0 + index * 0.1 + math.sin(index / 5.0)
            rows.append({
                "open": close - 0.05, "high": close + 0.4,
                "low": close - 0.4, "close": close,
                "quote_volume": 1000.0 + index * 3.0,
            })
        return rows

    def test_feature_pack_is_finite_and_future_safe(self):
        candles = self.candles()
        before = technical.vector(candles, 168)
        candles[169].update({
            "open": 1e9, "high": 1e9, "low": 1e9,
            "close": 1e9, "quote_volume": 1e12,
        })
        after = technical.vector(candles, 168)
        self.assertEqual(before, after)
        self.assertEqual(len(before), len(technical.FEATURE_NAMES))
        self.assertTrue(all(math.isfinite(value) for value in before))


if __name__ == "__main__":
    unittest.main()
