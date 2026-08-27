import sqlite3
import unittest

from analysis import forecast_drift_challenger as challenger


class ForecastDriftChallengerTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute("""
            CREATE TABLE forecast_engine_predictions(
                forecast_id INTEGER PRIMARY KEY, as_of TEXT, symbol TEXT,
                horizon_hours INTEGER, status TEXT, settled_at TEXT,
                actual_class TEXT
            )
        """)
        challenger.ensure_table(self.con)

    def tearDown(self):
        self.con.close()

    def add_batches(self, count, actual="UP"):
        for batch in range(count):
            for symbol in ("BTCUSDT", "ETHUSDT"):
                self.con.execute(
                    "INSERT INTO forecast_engine_predictions VALUES(?,?,?,?,?,?,?)",
                    (batch * 2 + (symbol == "ETHUSDT"), f"2026-01-{batch+1:02d}T00:00:00+00:00",
                     symbol, 4, "SETTLED", f"2026-01-{batch+1:02d}T05:00:00+00:00", actual),
                )

    def test_warms_up_without_mutating_probabilities(self):
        self.add_batches(9)
        incumbent = {"UP": .2, "DOWN": .7, "NEUTRAL": .1}
        probs, status, count, evidence = challenger.probabilities(
            self.con, 4, "2026-02-01T00:00:00+00:00", incumbent,
            {"UP": .4, "DOWN": .5, "NEUTRAL": .1},
        )
        self.assertEqual(status, "WARMING_UP")
        self.assertEqual(count, 9)
        self.assertEqual(probs, incumbent)
        self.assertFalse(evidence["action_authority"])

    def test_equal_batch_prior_corrects_collapse_and_settles(self):
        self.add_batches(10, "UP")
        challenger.record(
            self.con, 999, "2026-02-01T00:00:00+00:00", 4,
            {"UP": .4, "DOWN": .5, "NEUTRAL": .1},
            {"UP": .4, "DOWN": .5, "NEUTRAL": .1},
        )
        row = self.con.execute("""
            SELECT status,p_up,p_down,predicted_class,production_eligible,action_authority
            FROM forecast_drift_challenger_predictions WHERE forecast_id=999
        """).fetchone()
        self.assertEqual(row[0], "FORWARD_SHADOW")
        self.assertGreater(row[1], row[2])
        self.assertEqual(row[3], "UP")
        self.assertEqual(row[4:], (0, 0))
        self.assertTrue(challenger.settle(self.con, 999, "UP", "2026-02-02T00:00:00+00:00"))
        settled = self.con.execute("""
            SELECT status,direction_hit,brier_score
            FROM forecast_drift_challenger_predictions WHERE forecast_id=999
        """).fetchone()
        self.assertEqual(settled[0:2], ("SETTLED", 1))
        self.assertGreaterEqual(settled[2], 0.0)

    def test_future_settlements_are_excluded(self):
        self.add_batches(10)
        probs, status, count, _ = challenger.probabilities(
            self.con, 4, "2026-01-05T00:00:00+00:00",
            {"UP": .2, "DOWN": .7, "NEUTRAL": .1},
            {"UP": .4, "DOWN": .5, "NEUTRAL": .1},
        )
        self.assertEqual(status, "WARMING_UP")
        self.assertEqual(count, 4)


if __name__ == "__main__":
    unittest.main()
