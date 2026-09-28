import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from paper_trading import controlled_challenger as challenger


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def closed_row(index, *, pnl, direction="LONG"):
    week = index // 10
    selected = NOW + timedelta(weeks=week, days=1, hours=index % 10)
    return {
        "position_id": index + 1,
        "symbol": "BTCUSDT",
        "horizon": 4,
        "direction": direction,
        "selected_at": selected.isoformat(),
        "exit_time": (selected + timedelta(hours=4)).isoformat(),
        "notional": 100.0,
        "net_pnl": float(pnl),
        "regime": "RISK_OFF",
        "policy_version": "POLICY_V1",
        "decision_source": "MARKET_BEHAVIOR_CORE",
        "leverage": 1.0,
        "margin": 100.0,
        "risk_tier": "PAPER_RISK_TIER_1",
        "liquidated": False,
    }


class ControlledChallengerTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute("""
            CREATE TABLE paper_agent_trade_learning_hypotheses(
                hypothesis_id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL,
                learning_version TEXT NOT NULL,
                validation_cohort TEXT NOT NULL,
                pattern_key TEXT NOT NULL,
                candidate_type TEXT NOT NULL,
                trade_count INTEGER NOT NULL,
                independent_weeks INTEGER NOT NULL,
                evidence_json TEXT NOT NULL,
                status TEXT NOT NULL,
                promotion_allowed INTEGER NOT NULL DEFAULT 0
            )
        """)

    def tearDown(self):
        self.con.close()

    def add_hypothesis(
        self, *, hypothesis_id=1, pattern="DIRECTION:LONG",
        candidate_type="DIRECTION_SIZING_CHALLENGER",
    ):
        self.con.execute("""
            INSERT INTO paper_agent_trade_learning_hypotheses
            VALUES(?,?,?,?,?,?,?,?,?,'EVIDENCE_READY',0)
        """, (
            hypothesis_id, NOW.isoformat(), "LEARNING_V3", "CLEAN",
            pattern, candidate_type, 30, 3, "{}",
        ))

    def test_forward_only_suppression_challenger_becomes_reviewable(self):
        self.add_hypothesis()

        initial = challenger.run(self.con, "CLEAN", [], NOW)
        self.assertEqual(initial["created"], 1)
        self.assertEqual(initial["challenger_count"], 1)
        self.assertEqual(initial["reviewable_count"], 0)

        before_start = closed_row(999, pnl=-1000.0)
        before_start["selected_at"] = (NOW - timedelta(days=1)).isoformat()
        before_start["exit_time"] = (NOW - timedelta(hours=1)).isoformat()
        forward = [closed_row(i, pnl=-1.0) for i in range(30)]

        result = challenger.run(
            self.con, "CLEAN", [before_start, *forward],
            NOW + timedelta(weeks=4),
        )

        self.assertEqual(result["reviewable_count"], 1)
        row = self.con.execute("""
            SELECT status,forward_trade_count,independent_weeks,
                   incumbent_net_pnl,challenger_net_pnl,
                   improvement_net_pnl,promotion_allowed,
                   policy_mutation_allowed,real_order_allowed
            FROM paper_agent_controlled_challengers
        """).fetchone()
        self.assertEqual(row[0], "FORWARD_BETTER_REVIEWABLE")
        self.assertEqual(row[1], 30)
        self.assertGreaterEqual(row[2], 3)
        self.assertAlmostEqual(row[3], -30.0)
        self.assertAlmostEqual(row[4], 0.0)
        self.assertAlmostEqual(row[5], 30.0)
        self.assertEqual(row[6:], (0, 0, 0))

    def test_profitable_incumbent_rejects_suppression_challenger(self):
        self.add_hypothesis()
        challenger.run(self.con, "CLEAN", [], NOW)

        result = challenger.run(
            self.con, "CLEAN",
            [closed_row(i, pnl=1.0) for i in range(30)],
            NOW + timedelta(weeks=4),
        )

        self.assertEqual(result["reviewable_count"], 0)
        status, improvement = self.con.execute("""
            SELECT status,improvement_net_pnl
            FROM paper_agent_controlled_challengers
        """).fetchone()
        self.assertEqual(status, "FORWARD_NOT_BETTER")
        self.assertAlmostEqual(improvement, -30.0)

    def test_policy_source_review_hypothesis_never_becomes_challenger(self):
        self.add_hypothesis(
            pattern="POLICY:POLICY_V1",
            candidate_type="POLICY_SOURCE_REVIEW_ONLY",
        )
        result = challenger.run(self.con, "CLEAN", [], NOW)

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["challenger_count"], 0)

    def test_insufficient_forward_sample_stays_collecting(self):
        self.add_hypothesis()
        challenger.run(self.con, "CLEAN", [], NOW)

        result = challenger.run(
            self.con, "CLEAN",
            [closed_row(i, pnl=-5.0) for i in range(5)],
            NOW + timedelta(weeks=4),
        )

        self.assertEqual(
            result["statuses"], {"FORWARD_COLLECTING": 1}
        )
        self.assertEqual(result["reviewable_count"], 0)
        flags = self.con.execute("""
            SELECT promotion_allowed,policy_mutation_allowed,real_order_allowed
            FROM paper_agent_controlled_challengers
        """).fetchone()
        self.assertEqual(flags, (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
