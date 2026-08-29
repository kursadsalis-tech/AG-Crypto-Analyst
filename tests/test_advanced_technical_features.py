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

    def test_sparse_zero_volume_candle_is_valid(self):
        candles = self.candles()
        candles[150].update({
            "open": candles[149]["close"],
            "high": candles[149]["close"],
            "low": candles[149]["close"],
            "close": candles[149]["close"],
            "quote_volume": 0.0,
        })
        values = technical.vector(candles, 168)
        self.assertTrue(all(math.isfinite(value) for value in values))

    def test_zero_volume_lookback_fails_closed(self):
        candles = self.candles()
        for candle in candles[145:169]:
            candle["quote_volume"] = 0.0
        with self.assertRaisesRegex(
            ValueError, "insufficient advanced technical volume"
        ):
            technical.vector(candles, 168)


if __name__ == "__main__":
    unittest.main()
