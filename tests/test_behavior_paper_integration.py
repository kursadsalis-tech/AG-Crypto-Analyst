import json
import sqlite3
import unittest
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from paper_trading import crypto_profit_agent as agent
from paper_trading.execution_contract import MarketSnapshot, ExecutionRules


def quote(at, price=100, market_type="USD_M_PERPETUAL"):
    ms = int(at.timestamp() * 1000)
    return MarketSnapshot(price, 1_000_000, observed_at=at.isoformat(),
                          best_bid=price, best_ask=price, quote_timestamp_ms=ms,
                          liquidity_timestamp_ms=ms, source="TEST_BOOK", market_type=market_type)


NOW = datetime(2026, 9, 4, 12, 2, tzinfo=timezone.utc)


class BehaviorPaperIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        agent.ensure_tables(self.con)
        self.metadata = {"SPOT": object(), "USD_M_PERPETUAL": object()}
        rules_patch = patch.object(agent, "rules_for", return_value=ExecutionRules(.000001, .000001, 5))
        rules_patch.start()
        self.addCleanup(rules_patch.stop)
        self.addCleanup(self.con.close)

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
        self.assertEqual(row[1], "PAPER_BEHAVIOR_POLICY_V4_EXECUTION_CALIBRATED")
        self.assertEqual(row[2:], ("BEHAVIOR_CORE_V4", "MARKET_BEHAVIOR_CORE", 7))
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

    def test_own_execution_feedback_changes_next_experiment_size(self):
        candidate = self.candidate()
        candidate["execution_size_multiplier"] = 0.8
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }):
            selected = agent.select_new_positions(self.con, NOW)
        self.assertEqual(selected, 1)
        notional, reason = self.con.execute(
            "SELECT notional,selection_reason FROM paper_agent_positions"
        ).fetchone()
        self.assertAlmostEqual(notional, 40.0)
        self.assertIn("execution_size_multiplier=0.800", reason)

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
                          return_value=quote(planned)):
            self.assertEqual(agent.fill_pending(self.con, planned, self.metadata), (1, 0))
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
                          return_value=quote(planned + timedelta(minutes=2), .5, "SPOT")) as capture:
            filled, cancelled = agent.fill_pending(
                self.con, planned + timedelta(minutes=2), self.metadata
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
                self.con, planned + timedelta(minutes=11), self.metadata
            )
        self.assertEqual((filled, cancelled), (0, 1))
        self.assertEqual(
            self.con.execute("""SELECT status,cancel_reason
                FROM paper_agent_positions""").fetchone(),
            ("CANCELLED", "EXACT_SPOT_ENTRY_CAPTURE_UNAVAILABLE"),
        )

    def test_missing_okx_spot_management_route_cancels_before_open(self):
        candidate = self.candidate()
        candidate.update({"decision_id": 11, "symbol": "LAUSDT",
                          "evidence_sha256": "e" * 64})
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }):
            agent.select_new_positions(self.con, NOW)
        planned = agent.immediate_paper_entry_time(NOW)
        with patch.object(
            agent.execution_market_data, "capture_reference",
            side_effect=agent.execution_market_data.SpotManagementVenueUnavailable(
                "LAUSDT: OKX Spot management instrument unavailable"
            ),
        ):
            filled, cancelled = agent.fill_pending(self.con, planned, self.metadata)
        self.assertEqual((filled, cancelled), (0, 1))
        self.assertEqual(
            self.con.execute("""SELECT status,cancel_reason
                FROM paper_agent_positions""").fetchone(),
            ("CANCELLED", "OKX_SPOT_MANAGEMENT_UNAVAILABLE"),
        )

    def test_minute_cycle_skips_heavy_learning_without_new_close(self):
        with patch.object(agent, "connect", return_value=sqlite3.connect(":memory:")), \
                patch.object(agent, "load_execution_metadata", return_value=None), \
                patch.object(agent, "fill_pending", return_value=(0, 0)), \
                patch.object(agent, "settle_open", return_value=0), \
                patch.object(agent, "select_new_positions", return_value=0), \
                patch.object(agent.experience_memory, "review_closed_positions",
                             return_value=0), \
                patch.object(agent.trade_learning, "run") as learning:
            result = agent.run_behavior_selection(NOW)
        learning.assert_not_called()
        self.assertEqual(result["trade_learning"]["status"], "NO_NEW_CLOSED_TRADE")

    def test_infeasible_exchange_rules_reject_before_position_is_written(self):
        rejected = SimpleNamespace(status="REJECTED", reason="EXCHANGE_FILTER_REJECTION")
        rules = SimpleNamespace(quantity_step=1000.0, price_tick=0.1, min_notional=5.0)
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [self.candidate()], "review": {},
        }), patch.object(agent, "rules_for", return_value=rules), patch.object(
            agent, "estimate_order_rules", return_value=rejected,
        ):
            selected = agent.select_new_positions(
                self.con, NOW, exchange_metadata={"SPOT": object()},
            )
        self.assertEqual(selected, 0)
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM paper_agent_positions").fetchone()[0], 0,
        )
        reasons = json.loads(self.con.execute(
            "SELECT reason_counts_json FROM paper_behavior_selection_runs"
        ).fetchone()[0])
        self.assertEqual(reasons, {"PRESELECTION_EXCHANGE_FILTER_REJECTION": 1})


if __name__ == "__main__":
    unittest.main()
