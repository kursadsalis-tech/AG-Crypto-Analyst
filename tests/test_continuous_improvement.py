import sqlite3
import unittest
from datetime import datetime, timezone

from paper_trading import continuous_improvement as learning
from paper_trading import external_trader_learning as external


NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)


class ContinuousImprovementTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        external.ensure_tables(self.con)

    def tearDown(self):
        self.con.close()

    def trade(self, trader, trade, direction="LONG", status="CLOSED",
              first_seen="2026-08-25T00:00:00+00:00",
              exit_time="2026-08-24T00:00:00+00:00", pnl=1.0,
              symbol="BTCUSDT"):
        external.ingest_trade(self.con, {
            "exchange": "OKX", "source_url": "https://www.okx.com/copy-trading",
            "source_trader_id": trader, "source_trade_id": trade,
            "sampling_frame": "FULL_LEADERBOARD", "leaderboard_rank": 1,
            "leaderboard_size": 100, "symbol": symbol, "direction": direction,
            "status": status, "entry_time": "2026-08-20T00:00:00+00:00",
            "exit_time": exit_time if status == "CLOSED" else None,
            "entry_price": 100.0, "exit_price": 101.0 if status == "CLOSED" else None,
            "notional": 100.0, "leverage": 2.0, "reported_pnl": pnl,
            "fees": 0.1, "funding": 0.0,
        }, first_observed_at=first_seen)

    def test_one_prolific_trader_cannot_create_candidate(self):
        for index in range(100):
            self.trade("one", str(index))
        result = learning.run(self.con, NOW)
        self.assertEqual(result["candidates_seen"], [])

    def test_retrospective_data_only_opens_forward_candidate(self):
        for trader in range(10):
            for trade in range(5):
                self.trade(str(trader), f"{trader}-{trade}")
        result = learning.run(self.con, NOW)
        self.assertEqual(result["candidates_seen"], ["EXTERNAL_DIRECTION:LONG"])
        row = self.con.execute("""
            SELECT status,policy_mutation_allowed,promotion_allowed
            FROM paper_agent_research_candidates
        """).fetchone()
        self.assertEqual(row, ("FORWARD_COLLECTING", 0, 0))

    def test_consensus_is_one_vote_per_trader_and_never_executes(self):
        self.trade("a", "1", status="OPEN", symbol="ETHUSDT")
        self.trade("a", "2", direction="LONG", status="OPEN", symbol="ETHUSDT")
        self.trade("b", "3", direction="SHORT", status="OPEN", symbol="ETHUSDT")
        learning.run(self.con, NOW)
        row = self.con.execute("""
            SELECT long_traders,short_traders,total_traders,execution_allowed
            FROM external_trader_consensus_snapshots WHERE symbol='ETHUSDT'
        """).fetchone()
        self.assertEqual(row, (1, 1, 2, 0))

    def test_closed_trade_first_seen_after_exit_is_not_forward(self):
        learning.ensure_tables(self.con)
        self.con.execute("""
          INSERT INTO paper_agent_research_candidates(
            created_at,learning_version,source_kind,pattern_key,rule_json,
            discovery_evidence_json,forward_start_at,status
          ) VALUES(?,?,?,?,?,?,?,'FORWARD_COLLECTING')
        """, (NOW.isoformat(), learning.VERSION, "EXTERNAL_RETROSPECTIVE", "X",
              '{"direction":"LONG"}', '{}', NOW.isoformat()))
        self.trade("a", "late", first_seen="2026-08-27T00:00:00+00:00",
                   exit_time="2026-08-26T12:00:00+00:00")
        result = learning.run(self.con, datetime(2026, 8, 28, tzinfo=timezone.utc))
        self.assertEqual(result["evaluations"][0]["trades"], 0)


if __name__ == "__main__":
    unittest.main()
