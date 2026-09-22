import sqlite3
import unittest
from unittest.mock import patch
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

    def test_preinstalled_schema_skips_repeated_learning_ddl(self):
        trade_learning.ensure_tables(self.con)
        trade_learning.external_trader_learning.ensure_tables(self.con)
        trade_learning.external_trader_rationale.ensure_tables(self.con)
        trade_learning.continuous_improvement.ensure_tables(self.con)
        with patch.object(
            trade_learning, "ensure_tables",
            side_effect=AssertionError("trade learning DDL repeated"),
        ), patch.object(
            trade_learning.external_trader_learning, "ensure_tables",
            side_effect=AssertionError("external learning DDL repeated"),
        ), patch.object(
            trade_learning.external_trader_rationale, "ensure_tables",
            side_effect=AssertionError("rationale DDL repeated"),
        ), patch.object(
            trade_learning.continuous_improvement, "ensure_tables",
            side_effect=AssertionError("improvement DDL repeated"),
        ):
            result = trade_learning.run(
                self.con, "CLEAN", ensure_schema=False
            )
        self.assertEqual(result["status"], "COLLECTING_EVIDENCE")

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
