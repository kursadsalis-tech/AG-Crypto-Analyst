import sqlite3
import unittest
from datetime import datetime, timezone

from paper_trading import strategy_allocator as allocator


NOW = datetime(2026, 8, 26, tzinfo=timezone.utc)


class StrategyAllocatorTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript("""
          CREATE TABLE paper_agent_research_candidates(
            pattern_key TEXT, rule_json TEXT, status TEXT, source_kind TEXT
          );
          CREATE TABLE paper_agent_positions(
            strategy_id TEXT,status TEXT,evidence_eligible INTEGER,
            net_pnl REAL,notional REAL
          );
        """)
        for direction in ("LONG", "SHORT"):
            self.con.execute("INSERT INTO paper_agent_research_candidates VALUES(?,?,?,?)",
                             (f"EXTERNAL_DIRECTION:{direction}",
                              f'{{"direction":"{direction}"}}',
                              "FORWARD_COLLECTING", "EXTERNAL_RETROSPECTIVE"))

    def tearDown(self):
        self.con.close()

    def test_new_strategies_share_virtual_budget_and_never_get_real_orders(self):
        weights = allocator.rebalance(self.con, NOW)
        self.assertEqual(set(weights), {
            "CHAMPION_BASELINE", "CHALLENGER_EXTERNAL_DIRECTION_LONG",
            "CHALLENGER_EXTERNAL_DIRECTION_SHORT",
        })
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertEqual(self.con.execute(
            "SELECT sum(real_order_allowed) FROM paper_agent_strategy_allocations"
        ).fetchone()[0], 0)

    def test_single_win_cannot_change_allocations(self):
        allocator.rebalance(self.con, NOW)
        self.con.execute("INSERT INTO paper_agent_positions VALUES(?,?,?,?,?)",
                         ("CHALLENGER_EXTERNAL_DIRECTION_LONG", "CLOSED", 1, 50, 100))
        weights = allocator.rebalance(self.con, NOW)
        self.assertAlmostEqual(weights["CHALLENGER_EXTERNAL_DIRECTION_LONG"], 1 / 3)

    def test_legacy_pnl_without_execution_ledger_cannot_earn_more_budget(self):
        allocator.rebalance(self.con, NOW)
        for _ in range(10):
            self.con.execute("INSERT INTO paper_agent_positions VALUES(?,?,?,?,?)",
                             ("CHALLENGER_EXTERNAL_DIRECTION_LONG", "CLOSED", 1, 2, 100))
        weights = allocator.rebalance(self.con, NOW)
        self.assertAlmostEqual(weights["CHALLENGER_EXTERNAL_DIRECTION_LONG"],
                               weights["CHAMPION_BASELINE"])
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_direction_filter_only_matches_compatible_challenger(self):
        allocator.rebalance(self.con, NOW)
        ids = {row[0] for row in allocator.matching_strategies(self.con, "LONG")}
        self.assertIn("CHAMPION_BASELINE", ids)
        self.assertIn("CHALLENGER_EXTERNAL_DIRECTION_LONG", ids)
        self.assertNotIn("CHALLENGER_EXTERNAL_DIRECTION_SHORT", ids)


if __name__ == "__main__":
    unittest.main()
