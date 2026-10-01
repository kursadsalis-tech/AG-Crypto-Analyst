import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from paper_trading import trade_learning


class TradeLearningTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript("""
            CREATE TABLE forecast_engine_predictions(
                forecast_id INTEGER PRIMARY KEY, regime TEXT
            );
            CREATE TABLE paper_agent_positions(
                position_id INTEGER PRIMARY KEY, forecast_id INTEGER,
                symbol TEXT, horizon_hours INTEGER, direction TEXT,
                exit_time TEXT, notional REAL, net_pnl REAL, status TEXT,
                evidence_eligible INTEGER, validation_cohort TEXT,
                policy_version TEXT,decision_source TEXT
            );
        """)

    def tearDown(self):
        self.con.close()

    def add(self, index, *, cohort="CLEAN", eligible=1, pnl=-1.0, weeks=1):
        stamp = datetime(2026, 1, 5, tzinfo=timezone.utc) + timedelta(
            weeks=index % weeks, hours=index
        )
        self.con.execute(
            "INSERT INTO forecast_engine_predictions VALUES(?,?)", (index, "RISK_OFF")
        )
        self.con.execute(
            "INSERT INTO paper_agent_positions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (index, index, "BTCUSDT", 4, "LONG", stamp.isoformat(), 100.0,
             pnl, "CLOSED", eligible, cohort, "POLICY", "SOURCE"),
        )

    def _add_technical_review(self, position_id, *, alignment, pnl,
                              net_return_pct, economics_final=True,
                              technical_authority=False):
        self.con.executescript("""
            CREATE TABLE IF NOT EXISTS paper_agent_decision_theses(
                position_id INTEGER PRIMARY KEY,
                validation_cohort TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_agent_experience_reviews(
                position_id INTEGER PRIMARY KEY,
                net_pnl REAL NOT NULL,
                review_json TEXT NOT NULL
            );
        """)
        authority = {
            "direction_input": technical_authority,
            "ranking_input": False,
            "confidence_input": False,
            "sizing_input": False,
            "stop_input": False,
        }
        payload = {
            "evidence_assessment": {
                "execution_complete": True,
                "economics_final": economics_final,
            },
            "economic_result": {
                "net_return_pct": net_return_pct,
            },
            "frozen_thesis": {
                "technical_context_observed": {
                    "observation_only": True,
                    "authority": authority,
                    "summary": {
                        "alignment": alignment,
                        "trend_1h": "BULLISH",
                        "trend_4h": "BULLISH",
                    },
                },
            },
        }
        self.con.execute(
            "INSERT INTO paper_agent_decision_theses VALUES(?,?)",
            (position_id, "CLEAN"),
        )
        self.con.execute(
            "INSERT INTO paper_agent_experience_reviews VALUES(?,?,?)",
            (position_id, pnl, json.dumps(payload)),
        )

    def test_technical_shadow_review_is_forward_association_only(self):
        values = [
            (1, "ALIGNED_BULLISH", 2.0, 2.0),
            (2, "ALIGNED_BULLISH", 1.0, 1.0),
            (3, "ALIGNED_BEARISH", -1.0, -1.0),
            (4, "MIXED", -0.5, -0.5),
        ]
        for index, alignment, pnl, net_return in values:
            self.add(index, pnl=pnl)
            self._add_technical_review(
                index, alignment=alignment, pnl=pnl,
                net_return_pct=net_return,
            )
        review = trade_learning.technical_shadow_review(self.con, "CLEAN")
        self.assertEqual(review["status"], "COLLECTING_FORWARD_EVIDENCE")
        self.assertEqual(review["overall"]["trades"], 4)
        self.assertEqual(
            review["groups"]["RELATION:SUPPORTS_DIRECTION"]["trades"], 2
        )
        self.assertEqual(
            review["groups"]["RELATION:OPPOSES_DIRECTION"]["trades"], 1
        )
        self.assertEqual(
            review["groups"]["RELATION:MIXED_OR_UNALIGNED"]["trades"], 1
        )
        self.assertGreater(
            review["comparison"]["supports_minus_opposes_mean_net_return_pct"],
            0.0,
        )
        self.assertFalse(review["authority"]["direction"])
        self.assertFalse(review["authority"]["promotion_allowed"])
        self.assertEqual(
            review["claim_level"], "FORWARD_ASSOCIATION_NOT_CAUSATION"
        )

    def test_technical_shadow_excludes_nonfinal_or_authoritative_context(self):
        self.add(10, pnl=1.0)
        self._add_technical_review(
            10, alignment="ALIGNED_BULLISH", pnl=1.0,
            net_return_pct=1.0, economics_final=False,
        )
        self.add(11, pnl=1.0)
        self._add_technical_review(
            11, alignment="ALIGNED_BULLISH", pnl=1.0,
            net_return_pct=1.0, technical_authority=True,
        )
        review = trade_learning.technical_shadow_review(self.con, "CLEAN")
        self.assertEqual(review["status"], "NO_TECHNICAL_SHADOW_EVIDENCE")
        self.assertEqual(review["overall"]["trades"], 0)

    def test_run_reports_subphase_progress_without_changing_authority(self):
        phases = []
        result = trade_learning.run(
            self.con,
            "CLEAN",
            refresh_external=False,
            progress=phases.append,
        )
        self.assertEqual(result["status"], "COLLECTING_EVIDENCE")
        self.assertEqual(
            phases,
            [
                "LEARNING_LOAD_CLOSED_START",
                "LEARNING_ROWS_LOADED",
                "LEARNING_TECHNICAL_SHADOW_START",
                "LEARNING_TECHNICAL_SHADOW_DONE",
                "LEARNING_REVIEW_COMMITTED",
                "LEARNING_CHALLENGER_START",
                "LEARNING_CHALLENGER_DONE",
            ],
        )
        self.assertFalse(result["promotion_allowed"])

    def test_zero_clean_closed_is_collecting(self):
        result = trade_learning.run(self.con, "CLEAN")
        self.assertEqual(result["status"], "COLLECTING_EVIDENCE")
        self.assertEqual(result["closed"], 0)
        self.assertFalse(result["promotion_allowed"])

    def test_legacy_and_ineligible_trades_are_excluded(self):
        self.add(1, cohort="LEGACY")
        self.add(2, eligible=0)
        result = trade_learning.run(self.con, "CLEAN")
        self.assertEqual(result["closed"], 0)

    def test_below_total_threshold_never_opens_hypothesis(self):
        for index in range(1, 31):
            self.add(index, weeks=3)
        result = trade_learning.run(self.con, "CLEAN")
        self.assertEqual(result["status"], "COLLECTING_EVIDENCE")
        count = self.con.execute(
            "SELECT COUNT(*) FROM paper_agent_trade_learning_hypotheses"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_legacy_negative_pnl_without_execution_ledger_cannot_open_challenger(self):
        for index in range(1, 101):
            self.add(index, weeks=3)
        result = trade_learning.run(self.con, "CLEAN")
        self.assertEqual(result["status"], "COLLECTING_EVIDENCE")
        self.assertEqual(result["closed"], 0)
        self.assertFalse(result["promotion_allowed"])
        self.assertEqual(
            self.con.execute(
                "SELECT COUNT(*) FROM paper_agent_trade_learning_hypotheses"
            ).fetchone()[0], 0,
        )


if __name__ == "__main__":
    unittest.main()
