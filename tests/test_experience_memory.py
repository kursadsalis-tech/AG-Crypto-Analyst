import json
import sqlite3
import unittest
from datetime import datetime, timezone

from database.runtime_store import REMOTE_RUNTIME_TABLES
from paper_trading import experience_memory as memory


NOW = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)


class ExperienceMemoryTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript("""
            CREATE TABLE paper_agent_positions(
                position_id INTEGER PRIMARY KEY,status TEXT,net_pnl REAL,notional REAL,
                gross_pnl REAL,entry_fee REAL,exit_fee REAL,entry_spread_cost REAL,
                entry_slippage_cost REAL,exit_spread_cost REAL,exit_slippage_cost REAL,
                funding_cost REAL,liquidated INTEGER,close_reason TEXT,exit_time TEXT,
                direction TEXT,horizon_hours INTEGER,selected_at TEXT,
                evidence_eligible INTEGER
            );
        """)
        memory.ensure_tables(self.con)

    def row(self):
        return {
            "as_of": NOW.isoformat(), "symbol": "BTCUSDT", "horizon": 4,
            "direction": "LONG", "p_up": .60, "p_down": .25, "p_neutral": .15,
            "confidence": .60, "gap": .35, "current_price": 100,
            "target_price": 103, "predicted_move_pct": 3,
            "risk_eligible": 1, "risk_cap_pct": 2,
            "model_version": "old-model", "artifact_hash": "abc",
        }

    def add_pending(self, position_id=1):
        self.con.execute("""INSERT INTO paper_agent_positions(
            position_id,status,notional,direction,horizon_hours,selected_at
        ) VALUES(?, 'PENDING_ENTRY',100,'LONG',4,?)""", (position_id, NOW.isoformat()))

    def capture(self, position_id=1):
        return memory.capture_decision_thesis(
            self.con, position_id=position_id, row=self.row(), agent_version="agent",
            policy_version="policy", validation_cohort="cohort", strategy_id="base",
            strategy_weight=1.0, selected_at=NOW, planned_entry_time=NOW,
        )

    def test_thesis_is_outcome_blind_and_names_unused_capabilities(self):
        self.add_pending()
        self.capture()
        raw, known = self.con.execute(
            "SELECT thesis_json,outcome_known_at_capture FROM paper_agent_decision_theses"
        ).fetchone()
        thesis = json.loads(raw)
        self.assertEqual(known, 0)
        self.assertFalse(thesis["claims"]["outcome_used"])
        self.assertIn("MARKET_BEHAVIOR_OBSERVER", thesis["capabilities_observed_but_not_decision_inputs"])

    def test_behavior_thesis_freezes_selection_economics(self):
        self.add_pending()
        row = self.row()
        row.update({
            "decision_source": "MARKET_BEHAVIOR_CORE",
            "evidence": {
                "evidence_minute_end_ms": int(NOW.timestamp() * 1000),
                "capabilities_used": ["ORDER_BOOK", "RISK_GATE"],
            },
            "technical_context": {
                "version": "SUPPORT",
                "summary": {"alignment": "ALIGNED_BULLISH"},
            },
            "technical_plan": {
                "version": "TECHNICAL_TEST",
                "direction": "LONG",
                "risk": {
                    "invalidation_price": 98.0,
                    "stop_distance_pct": 2.0,
                    "risk_compatible": True,
                },
                "entry": {"low": 99.0, "high": 101.0, "anchor": 100.0},
                "targets": {"tp1_price": 102.0, "tp2_price": 104.0},
                "authority": {
                    "ranking_input": True, "sizing_input": True,
                    "entry_input": True, "stop_input": True,
                    "take_profit_input": True,
                },
            },
            "expected_net_edge_pct": 1.25,
            "expected_profit_per_risk": 2.5,
            "expected_profit_usdt": 12.5,
        })
        memory.capture_decision_thesis(
            self.con, position_id=1, row=row, agent_version="agent",
            policy_version="policy", validation_cohort="cohort", strategy_id="base",
            strategy_weight=1.0, selected_at=NOW, planned_entry_time=NOW,
        )
        thesis = json.loads(self.con.execute(
            "SELECT thesis_json FROM paper_agent_decision_theses WHERE position_id=1"
        ).fetchone()[0])
        self.assertEqual(thesis["selection_economics"]["expected_net_edge_pct"], 1.25)
        self.assertEqual(thesis["selection_economics"]["expected_profit_per_risk"], 2.5)
        self.assertEqual(thesis["selection_economics"]["expected_profit_usdt"], 12.5)
        self.assertEqual(
            thesis["technical_trade_plan"]["risk"]["invalidation_price"], 98.0
        )
        self.assertIn(
            "MULTITIMEFRAME_TECHNICAL_EXECUTION_PLAN",
            thesis["capabilities_under_test"],
        )

    def test_closed_trade_cannot_receive_retrofitted_thesis(self):
        self.add_pending()
        self.con.execute("UPDATE paper_agent_positions SET status='CLOSED' WHERE position_id=1")
        with self.assertRaisesRegex(ValueError, "OUTCOME_UNKNOWN"):
            self.capture()

    def test_review_is_idempotent_and_single_trade_never_proves_skill(self):
        self.add_pending()
        digest = self.capture()
        self.con.execute("""UPDATE paper_agent_positions SET status='CLOSED',net_pnl=4,
            gross_pnl=5,entry_fee=.5,exit_fee=.5,entry_spread_cost=.1,
            entry_slippage_cost=.1,exit_spread_cost=.1,exit_slippage_cost=.1,
            funding_cost=.2,liquidated=0,close_reason='DUE',exit_time=? WHERE position_id=1
        """, (NOW.isoformat(),))
        self.assertEqual(memory.review_closed_positions(self.con, NOW), 1)
        self.assertEqual(memory.review_closed_positions(self.con, NOW), 0)
        row = self.con.execute("SELECT thesis_sha256,skill_proven,causal_claim,review_json FROM paper_agent_experience_reviews").fetchone()
        self.assertEqual(row[:3], (digest, 0, 0))
        self.assertEqual(json.loads(row[3])["attribution"]["skill"], "UNRESOLVED_SINGLE_EPISODE")

    def test_legacy_profit_is_retained_but_not_skill(self):
        self.con.execute("""INSERT INTO paper_agent_positions(
            position_id,status,net_pnl,notional,direction,horizon_hours,selected_at,
            evidence_eligible
        ) VALUES(9,'CLOSED',10,100,'SHORT',24,?,1)""", (NOW.isoformat(),))
        summary = memory.record_legacy_summary(self.con, NOW)
        self.assertEqual(summary["net_pnl_usdt"], 10)
        row = self.con.execute("SELECT skill_proven,promotion_allowed FROM paper_agent_experience_summaries").fetchone()
        self.assertEqual(row, (0, 0))

    def test_quarantined_lookahead_trade_is_not_learning_experience(self):
        self.con.execute("""INSERT INTO paper_agent_positions(
            position_id,status,net_pnl,notional,direction,horizon_hours,selected_at,
            evidence_eligible
        ) VALUES(10,'CLOSED',999,100,'LONG',24,?,0)""", (NOW.isoformat(),))
        self.assertIsNone(memory.record_legacy_summary(self.con, NOW))

    def test_memory_tables_are_local_only(self):
        for table in ("paper_agent_decision_theses", "paper_agent_experience_reviews",
                      "paper_agent_experience_summaries"):
            self.assertNotIn(table, REMOTE_RUNTIME_TABLES)


if __name__ == "__main__":
    unittest.main()
