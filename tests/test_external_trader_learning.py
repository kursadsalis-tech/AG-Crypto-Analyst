import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from paper_trading import external_trader_learning as external


class ExternalTraderLearningTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.base = {
            "exchange": "BYBIT", "source_url": "https://www.bybit.com/copyTrading/x",
            "source_trader_id": "trader-1", "sampling_frame": "FULL_LEADERBOARD",
            "leaderboard_scope": "USDT_PERPETUAL_90D", "leaderboard_rank": 1,
            "leaderboard_size": 100, "metrics": {"roi_90d": 12.0},
        }

    def tearDown(self):
        self.con.close()

    def test_winner_only_social_example_is_quarantined(self):
        payload = dict(self.base, sampling_frame="ANECDOTAL_ONLY")
        result = external.ingest_snapshot(self.con, payload)
        self.assertFalse(result["research_eligible"])
        self.assertEqual(result["exclusion_reason"], "ANECDOTAL_WINNER_EXAMPLE")

    def test_incomplete_leaderboard_sample_is_quarantined(self):
        payload = dict(self.base)
        payload.pop("leaderboard_size")
        result = external.ingest_snapshot(self.con, payload)
        self.assertFalse(result["research_eligible"])

    def test_closed_trade_first_seen_later_is_retrospective_only(self):
        entry = datetime(2026, 1, 1, tzinfo=timezone.utc)
        trade = dict(self.base, source_trade_id="t1", symbol="BTCUSDT",
                     direction="LONG", status="CLOSED", entry_time=entry.isoformat(),
                     exit_time=(entry + timedelta(hours=4)).isoformat(),
                     entry_price=100.0, exit_price=102.0, notional=100.0,
                     reported_pnl=2.0, fees=0.2, funding=0.1, leverage=1.0)
        result = external.ingest_trade(
            self.con, trade, first_observed_at=entry + timedelta(hours=5)
        )
        self.assertTrue(result["retrospective_only"])
        self.assertTrue(result["research_eligible"])

    def test_extreme_leverage_and_missing_costs_are_excluded(self):
        trade = dict(self.base, source_trade_id="t2", symbol="ETHUSDT",
                     direction="SHORT", status="OPEN",
                     entry_time="2026-01-01T00:00:00+00:00", leverage=20.0)
        result = external.ingest_trade(self.con, trade)
        self.assertFalse(result["research_eligible"])
        self.assertEqual(result["exclusion_reason"], "EXTREME_LEVERAGE_EXCLUDED")

    def test_review_never_allows_policy_mutation_or_promotion(self):
        result = external.run_review(self.con)
        self.assertEqual(result["status"], "COLLECTING_UNBIASED_TRADER_COHORT")
        self.assertFalse(result["promotion_allowed"])
        row = self.con.execute("""
            SELECT policy_mutation_allowed,promotion_allowed
            FROM external_trader_learning_reviews
        """).fetchone()
        self.assertEqual(row, (0, 0))

    def test_open_observation_timestamp_survives_later_closed_version(self):
        entry = datetime(2026, 1, 1, tzinfo=timezone.utc)
        opened = dict(self.base, source_trade_id="stable", symbol="BTCUSDT",
                      direction="LONG", status="OPEN", entry_time=entry.isoformat(),
                      entry_price=100.0, notional=100.0, leverage=1.0,
                      fees=0.1, funding=0.0)
        external.ingest_trade(self.con, opened, first_observed_at=entry + timedelta(minutes=1))
        closed = dict(opened, status="CLOSED", exit_time=(entry + timedelta(hours=4)).isoformat(),
                      exit_price=105.0, reported_pnl=4.8)
        result = external.ingest_trade(
            self.con, closed, first_observed_at=entry + timedelta(hours=5)
        )
        self.assertFalse(result["retrospective_only"])


if __name__ == "__main__":
    unittest.main()
