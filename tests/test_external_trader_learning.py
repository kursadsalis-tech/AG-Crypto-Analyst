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

    def test_status_identity_index_exists_for_bounded_runtime_queries(self):
        external.ensure_tables(self.con)
        indexes = {
            row[0] for row in self.con.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        self.assertIn("ix_external_trader_status_identity", indexes)

    def test_review_uses_eligible_indexes_without_changing_evidence_counts(self):
        entry = datetime(2026, 1, 1, tzinfo=timezone.utc)
        external.ingest_snapshot(self.con, self.base, observed_at=entry)
        external.ingest_snapshot(self.con, self.base, observed_at=entry + timedelta(days=1))
        second = dict(self.base, source_trader_id="trader-2")
        external.ingest_snapshot(self.con, second, observed_at=entry + timedelta(days=3))
        excluded = dict(self.base, source_trader_id="excluded", sampling_frame="ANECDOTAL_ONLY")
        external.ingest_snapshot(self.con, excluded, observed_at=entry + timedelta(days=20))
        trade = dict(self.base, source_trade_id="t1", symbol="BTCUSDT",
                     direction="LONG", status="CLOSED", entry_time=entry.isoformat(),
                     exit_time=(entry + timedelta(hours=4)).isoformat(),
                     entry_price=100.0, exit_price=102.0, notional=100.0,
                     reported_pnl=2.0, fees=0.2, funding=0.1, leverage=1.0)
        for payload in (
            trade,
            dict(trade, reported_pnl=3.0),  # A second observation is the same trade.
            dict(trade, source_trader_id="trader-2", source_trade_id="t2"),
            dict(trade, source_trade_id="excluded", sampling_frame="ANECDOTAL_ONLY"),
            dict(trade, source_trade_id="open-only", status="OPEN", exit_time=None),
        ):
            external.ingest_trade(self.con, payload, first_observed_at=entry + timedelta(hours=5))

        queries = (
            ("SELECT count(DISTINCT exchange || ':' || source_trader_id) "
             "FROM external_trader_source_snapshots WHERE research_eligible=1",
             "ix_external_trader_eligible_snapshot_identity_time"),
            ("SELECT min(observed_at),max(observed_at) "
             "FROM external_trader_source_snapshots WHERE research_eligible=1",
             "ix_external_trader_eligible_snapshot_identity_time"),
            ("SELECT count(DISTINCT exchange || ':' || source_trader_id || ':' || source_trade_id) "
             "FROM external_trader_trade_observations WHERE research_eligible=1 AND status='CLOSED'",
             "ix_external_trader_eligible_closed_identity"),
        )
        for query, index in queries:
            with self.subTest(index=index, query=query):
                plan = self.con.execute("EXPLAIN QUERY PLAN " + query).fetchall()
                self.assertIn(index, " ".join(str(row[3]) for row in plan))

        result = external.run_review(self.con, entry + timedelta(days=30))
        self.assertEqual(result["traders"], 2)
        self.assertEqual(result["closed_trades"], 2)
        self.assertEqual(result["observation_days"], 3)
        self.assertFalse(result["promotion_allowed"])

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
