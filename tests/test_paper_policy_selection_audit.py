"""Focused core -> orchestration -> PAPER selection policy regressions."""
import json
import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from analysis import realtime_technical_context, technical_trade_plan
from paper_trading import crypto_profit_agent as agent


NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def candidate(direction="LONG", decision_id=1):
    return {
        "decision_id": decision_id, "decision_source": "MARKET_BEHAVIOR_CORE",
        "symbol": "BTCUSDT" if direction == "LONG" else "ETHUSDT",
        "horizon": 4, "direction": direction, "confidence": 0.6,
        "expected_net_edge_pct": 0.4, "execution_size_multiplier": 1.0,
        "experiment_tier": "EXPLORATORY_LEARNING", "current_price": 100.0,
        "target_price": 100.4 if direction == "LONG" else 99.6,
        "risk_cap_pct": 10.0, "risk_per_trade_pct": 1.0,
        "evidence_sha256": str(decision_id) * 64,
        "evidence": {
            "evidence_minute_end_ms": int(NOW.timestamp() * 1000),
            "primary_signal_source": "MARKET_BEHAVIOR",
            "directional_signals": [{"source": "MARKET_BEHAVIOR", "direction": direction}],
            "event": {"kind": "MARKET_STRUCTURE", "reported_direction": "OBSERVED"},
            "learned_pattern": {"flow_pattern": "FLOW_POS", "direction": direction},
            "execution_calibration": {"calibrated_net_edge_pct": 0.4},
            "capabilities_used": ["ORDER_BOOK", "TAKER_FLOW", "RISK_GATE"],
        },
    }


def plan(con, row, *, now, market_type):
    return {
        "version": "TEST", "symbol": row["symbol"], "direction": row["direction"],
        "execution_market_type": market_type, "quality_multiplier": 1.0,
        "technical_score": 0.25, "timeframes_used": ["1m", "5m", "15m"],
        "risk": {"risk_compatible": True, "stop_distance_pct": 2.0,
                 "invalidation_price": 98.0 if row["direction"] == "LONG" else 102.0},
        "entry": {"anchor": 100.0, "low": 99.8, "high": 100.2},
        "targets": {"tp1_price": 102.0, "tp2_price": 104.0},
    }


class PaperPolicySelectionAuditTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.addCleanup(self.con.close)
        agent.ensure_tables(self.con)
        self.snapshot = {"valuation_complete": True, "equity": 10_000.0,
                         "cash": 10_000.0, "gross_exposure": 0.0}

    def select(self, behavior, *, planner=plan):
        telemetry = {}
        with patch.object(agent.behavior_decision_core, "evaluate", return_value=behavior), patch.object(
            agent, "portfolio_snapshot", return_value=dict(self.snapshot)
        ), patch.object(technical_trade_plan, "build_for_candidate", side_effect=planner), patch.object(
            realtime_technical_context, "load_latest", return_value=None
        ):
            selected = agent.select_new_positions(self.con, NOW, telemetry=telemetry)
        return selected, telemetry

    def audit(self):
        row = self.con.execute("""SELECT reason_counts_json,direction_counts_json,
            selected_count,real_order_allowed FROM paper_behavior_selection_runs
            ORDER BY run_id DESC LIMIT 1""").fetchone()
        return json.loads(row[0]), json.loads(row[1]), row[2], row[3]

    def test_zero_core_result_preserves_causes_and_direction_distribution(self):
        causes = {"PATTERN_SUSPENDED": 6, "NEGATIVE_EDGE_VETO": 3,
                  "EVIDENCE_EVENT_MISSING": 2, "NO_LEARNED_PATTERN": 1}
        stages = {"patterns": {"LONG": 20, "SHORT": 1},
                  "suspended": {"LONG": 5, "SHORT": 1}}
        result, telemetry = self.select({"status": "NO_ACTIONABLE_PATTERN", "candidates": [],
                                        "reason_counts": causes, "direction_counts": stages})
        reasons, directions, selected, real = self.audit()
        self.assertEqual((result, selected, real), (0, 0, 0))
        self.assertEqual(reasons, causes)
        self.assertEqual(directions["suspended"], stages["suspended"])
        self.assertEqual(directions["selected"], {"LONG": 0, "SHORT": 0})
        self.assertEqual(telemetry["direction_counts"], directions)

    def test_pre_orchestration_exit_keeps_core_reasons(self):
        self.snapshot["valuation_complete"] = False
        self.select({"status": "NO_ACTIONABLE_PATTERN", "candidates": [],
                     "reason_counts": {"PATTERN_SUSPENDED": 6}})
        reasons, _, _, real = self.audit()
        self.assertEqual(reasons, {"PATTERN_SUSPENDED": 6,
                                  "PORTFOLIO_VALUATION_STALE_OR_MISSING": 1})
        self.assertEqual(real, 0)

    def test_coverage_and_technical_rejection_remain_distinct(self):
        self.select({"status": "ACTIONABLE", "candidates": [candidate()],
                     "reason_counts": {"RISK_COVERAGE_MISSING": 1}},
                    planner=ValueError("missing closed bars"))
        self.assertEqual(self.audit()[0], {"RISK_COVERAGE_MISSING": 1,
                                          "TECHNICAL_PLAN_UNAVAILABLE": 1})

    def test_long_short_selection_and_immutable_evidence_remain_paper_only(self):
        candidates = [candidate("LONG", 1), candidate("SHORT", 2)]
        selected, _ = self.select({"status": "ACTIONABLE", "candidates": candidates})
        self.assertEqual(selected, 2)
        positions = self.con.execute("""SELECT direction,status,market_type
            FROM paper_agent_positions ORDER BY position_id""").fetchall()
        self.assertEqual(set(positions), {("LONG", "PENDING_ENTRY", "SPOT"),
                                          ("SHORT", "PENDING_ENTRY", "USD_M_PERPETUAL")})
        reasons, directions, selected, real = self.audit()
        self.assertEqual((reasons, selected, real), ({}, 2, 0))
        self.assertEqual(directions["selected"], {"LONG": 1, "SHORT": 1})
        self.assertEqual(directions["recent_positions_20"], {"LONG": 1, "SHORT": 1})
        frozen = self.con.execute("SELECT thesis_json,outcome_known_at_capture FROM paper_agent_decision_theses").fetchall()
        self.assertEqual(len(frozen), 2)
        for thesis_json, outcome_known in frozen:
            thesis = json.loads(thesis_json)
            original = next(row for row in candidates if row["direction"] == thesis["direction"])
            self.assertEqual(thesis["evidence_used"], original["evidence"])
            self.assertEqual(outcome_known, 0)

    def test_negative_calibration_cannot_enter_normal_selection(self):
        row = candidate()
        row["evidence"]["execution_calibration"]["calibrated_net_edge_pct"] = -0.0372
        selected, _ = self.select({"status": "ACTIONABLE", "candidates": [row]})
        self.assertEqual(selected, 0)
        self.assertEqual(self.audit()[0], {"NEGATIVE_EDGE_VETO": 1})
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM paper_agent_positions").fetchone()[0], 0)

    def test_recovery_size_is_bounded_even_if_adapter_supplies_large_multiplier(self):
        row = candidate()
        row["experiment_tier"] = "RECOVERY_EXPLORATION"
        row["execution_size_multiplier"] = 5.0
        selected, _ = self.select({"status": "ACTIONABLE", "candidates": [row]})
        self.assertEqual(selected, 1)
        notional, leverage = self.con.execute(
            "SELECT notional,leverage FROM paper_agent_positions").fetchone()
        # Quarter recovery is a soft target; the user floor is still bounded
        # by the explicit 1.5% recovery gross cap and ordinary hard limits.
        self.assertGreaterEqual(notional, 100.0)
        self.assertAlmostEqual(notional, 100.1)
        self.assertLessEqual(notional, self.snapshot["equity"] * .015)
        self.assertEqual(leverage, 1.0)

    def test_portfolio_exposure_limit_has_its_own_reason(self):
        self.snapshot["gross_exposure"] = 5_995.0
        self.select({"status": "ACTIONABLE", "candidates": [candidate()]})
        self.assertEqual(self.audit()[0], {"PORTFOLIO_EXPOSURE_LIMIT": 1})

    def test_additive_audit_migration_preserves_existing_history(self):
        self.con.execute("DROP TABLE paper_behavior_selection_runs")
        self.con.execute("""CREATE TABLE paper_behavior_selection_runs (
            run_id INTEGER PRIMARY KEY, created_at TEXT, core_status TEXT,
            candidate_count INTEGER, selected_count INTEGER, active_position_count INTEGER,
            capacity INTEGER, available_exposure REAL, reason_counts_json TEXT,
            real_order_allowed INTEGER DEFAULT 0)""")
        self.con.execute("""INSERT INTO paper_behavior_selection_runs VALUES
            (7,'2026-09-28','NO_ACTIONABLE_PATTERN',0,0,0,6,6069,'{}',0)""")
        agent.ensure_tables(self.con)
        self.assertEqual(self.con.execute("""SELECT run_id,created_at,reason_counts_json,
            direction_counts_json FROM paper_behavior_selection_runs""").fetchone(),
            (7, "2026-09-28", "{}", "{}"))


if __name__ == "__main__":
    unittest.main()
