import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from paper_trading import crypto_profit_agent as agent


NOW = datetime(2026, 9, 4, 12, 2, tzinfo=timezone.utc)


class BehaviorPaperIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        agent.ensure_tables(self.con)

    def candidate(self):
        minute = int((NOW - timedelta(minutes=1)).timestamp() * 1000)
        evidence = {
            "evidence_minute_end_ms": minute,
            "capabilities_used": ["ORDER_BOOK", "TAKER_FLOW", "RISK_GATE"],
            "unified_forecast_used": False,
        }
        return {
            "decision_id": 7, "decision_source": "MARKET_BEHAVIOR_CORE",
            "symbol": "BTCUSDT", "instrument": "BTC-USDT-SWAP", "horizon": 4,
            "direction": "LONG", "confidence": .60, "expected_net_edge_pct": .30,
            "experiment_tier": "EXPLORATORY_LEARNING",
            "current_price": 100.0, "target_price": 100.3, "risk_cap_pct": 10.0,
            "evidence": evidence, "evidence_sha256": "a" * 64,
        }

    def test_only_behavior_candidate_creates_position_and_frozen_thesis(self):
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [self.candidate()], "review": {},
        }), patch.object(agent, "latest_candidates", side_effect=AssertionError(
            "legacy Unified Forecast must not be an execution input"
        )):
            selected = agent.select_new_positions(self.con, NOW)
        self.assertEqual(selected, 1)
        row = self.con.execute("""SELECT forecast_id,policy_version,strategy_id,
            decision_source,decision_reference_id FROM paper_agent_positions""").fetchone()
        self.assertEqual(row[0], -7)
        self.assertEqual(row[1], "PAPER_BEHAVIOR_POLICY_V3_RISK_TIERED_LEARNING")
        self.assertEqual(row[2:], ("BEHAVIOR_CORE_V3", "MARKET_BEHAVIOR_CORE", 7))
        notional = self.con.execute("SELECT notional FROM paper_agent_positions").fetchone()[0]
        self.assertAlmostEqual(notional, 50.0)
        leverage, margin, tier, market = self.con.execute(
            """SELECT leverage,margin_allocated,risk_tier,market_type
            FROM paper_agent_positions"""
        ).fetchone()
        self.assertEqual(
            (leverage, margin, tier, market),
            (1.0, 50.0, "PAPER_RISK_TIER_1", "SPOT"),
        )
        thesis = self.con.execute(
            "SELECT decision_source,outcome_known_at_capture FROM paper_agent_decision_theses"
        ).fetchone()
        self.assertEqual(thesis, ("MARKET_BEHAVIOR_CORE", 0))

    def test_tiny_risk_cap_can_open_small_paper_learning_experiment(self):
        candidate = self.candidate()
        candidate["risk_cap_pct"] = 0.25
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }):
            selected = agent.select_new_positions(self.con, NOW)
        self.assertEqual(selected, 1)
        notional = self.con.execute(
            "SELECT notional FROM paper_agent_positions"
        ).fetchone()[0]
        self.assertAlmostEqual(notional, 25.0)

    def test_five_independent_signals_allocate_margin_not_gross_exposure(self):
        candidate = self.candidate()
        candidate["experiment_tier"] = "PROMOTION_READY"
        candidate["evidence"]["directional_signals"] = [
            {"source": source, "direction": "LONG"}
            for source in ("TECHNICAL", "NEWS", "ONCHAIN", "DERIVATIVES")
        ]
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 1)
        leverage, margin, gross = self.con.execute(
            "SELECT leverage,margin_allocated,notional FROM paper_agent_positions"
        ).fetchone()
        self.assertEqual(leverage, 5)
        self.assertAlmostEqual(margin, 200)
        self.assertAlmostEqual(gross, 1000)
        planned = agent.immediate_paper_entry_time(NOW)
        with patch.object(agent.execution_market_data, "capture_reference",
                          return_value=(planned, 100.0, 1_000_000.0)):
            self.assertEqual(agent.fill_pending(self.con, planned), (1, 0))
        cash = self.con.execute(
            "SELECT cash FROM paper_agent_portfolio WHERE portfolio_id=1"
        ).fetchone()[0]
        position = self.con.execute("""SELECT leverage,margin_allocated,notional,
            liquidation_price FROM paper_agent_positions""").fetchone()
        self.assertEqual(position[0], 5)
        self.assertAlmostEqual(position[1], position[2] / 5)
        self.assertGreater(position[3], 79)
        self.assertLess(position[3], 81)
        self.assertAlmostEqual(cash, 10_000 - position[1] - position[2] * .0005)

    def test_unexecutable_tiny_candidate_does_not_hide_later_candidate(self):
        too_small = self.candidate()
        too_small["risk_cap_pct"] = 0.05
        later = self.candidate()
        later.update({"decision_id": 8, "symbol": "ETHUSDT", "risk_cap_pct": 1.0,
                      "evidence_sha256": "b" * 64})
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [too_small, later], "review": {},
        }):
            selected = agent.select_new_positions(self.con, NOW)
        self.assertEqual(selected, 1)
        self.assertEqual(
            self.con.execute("SELECT symbol FROM paper_agent_positions").fetchone()[0],
            "ETHUSDT",
        )
        audit = self.con.execute("""SELECT candidate_count,selected_count,
            reason_counts_json,real_order_allowed FROM paper_behavior_selection_runs""").fetchone()
        self.assertEqual(audit[:2], (2, 1))
        self.assertEqual(json.loads(audit[2]), {"NOTIONAL_BELOW_EXECUTABLE_MINIMUM": 1})
        self.assertEqual(audit[3], 0)

    def test_selection_audit_is_local_only(self):
        from database.runtime_store import REMOTE_RUNTIME_TABLES
        self.assertNotIn("paper_behavior_selection_runs", REMOTE_RUNTIME_TABLES)

    def test_low_conviction_long_fills_through_spot_route(self):
        candidate = self.candidate()
        candidate.update({"decision_id": 9, "symbol": "CRVUSDT",
                          "evidence_sha256": "c" * 64})
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 1)
        planned = agent.immediate_paper_entry_time(NOW)
        with patch.object(agent.execution_market_data, "capture_reference",
                          return_value=(planned, 0.5, 1_000_000.0)) as capture:
            filled, cancelled = agent.fill_pending(
                self.con, planned + timedelta(minutes=2)
            )
        self.assertEqual((filled, cancelled), (1, 0))
        self.assertEqual(capture.call_args.kwargs["symbol"], "CRVUSDT")
        self.assertEqual(capture.call_args.kwargs["market_type"], "SPOT")
        self.assertEqual(
            self.con.execute("SELECT status FROM paper_agent_positions").fetchone()[0],
            "OPEN",
        )

    def test_failed_dynamic_entry_releases_capacity_after_ten_minutes(self):
        candidate = self.candidate()
        candidate.update({"decision_id": 10, "symbol": "UNIUSDT",
                          "evidence_sha256": "d" * 64})
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }):
            agent.select_new_positions(self.con, NOW)
        planned = agent.immediate_paper_entry_time(NOW)
        with patch.object(agent.execution_market_data, "capture_reference",
                          return_value=None):
            filled, cancelled = agent.fill_pending(
                self.con, planned + timedelta(minutes=11)
            )
        self.assertEqual((filled, cancelled), (0, 1))
        self.assertEqual(
            self.con.execute("""SELECT status,cancel_reason
                FROM paper_agent_positions""").fetchone(),
            ("CANCELLED", "EXACT_SPOT_ENTRY_CAPTURE_UNAVAILABLE"),
        )


if __name__ == "__main__":
    unittest.main()
