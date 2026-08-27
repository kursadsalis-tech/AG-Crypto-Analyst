import sqlite3
import unittest

from analysis import forecast_feature_challenger as challenger
from analysis import forecast_feature_challenger_lab as lab


class FeatureChallengerTests(unittest.TestCase):
    def test_flow_features_use_only_trailing_closed_rows(self):
        candles = []
        from datetime import datetime, timedelta, timezone
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index in range(30):
            candles.append({
                "open_time": start + timedelta(hours=index),
                "close_time": start + timedelta(hours=index + 1) - timedelta(milliseconds=1),
                "open": 100.0, "high": 101.0, "low": 99.0,
                "close": 100.0 + index, "quote_volume": 1000.0,
                "taker_buy_quote_volume": 600.0,
            })
        features = lab.feature_values(candles, 28, candles, 28)
        self.assertEqual(len(features), len(lab.FLOW_FEATURE_NAMES))
        self.assertAlmostEqual(features[0], 0.2)
        candles[29]["taker_buy_quote_volume"] = 0.0
        self.assertEqual(features, lab.feature_values(candles, 28, candles, 28))

    def test_settlement_tracks_net_utility_without_authority(self):
        con = sqlite3.connect(":memory:")
        challenger.ensure_table(con)
        con.execute("""
            INSERT INTO forecast_feature_challenger_predictions(
              forecast_id,created_at,model_version,model_hash,horizon_hours,status,
              p_up,p_down,p_neutral,predicted_return_pct,predicted_class,
              shadow_action,production_eligible,action_authority,feature_json
            ) VALUES(1,'x','m','h',4,'FORWARD_SHADOW',.8,.1,.1,1,'UP','AL',0,0,'{}')
        """)
        self.assertTrue(challenger.settle(con, 1, "UP", 1.0, "y"))
        row = con.execute("""
            SELECT direction_hit,action_hit,action_net_utility_pct,
                   production_eligible,action_authority FROM forecast_feature_challenger_predictions
        """).fetchone()
        self.assertEqual(row[:2], (1, 1))
        self.assertAlmostEqual(row[2], 0.9)
        self.assertEqual(row[3:], (0, 0))
        con.close()


if __name__ == "__main__":
    unittest.main()
