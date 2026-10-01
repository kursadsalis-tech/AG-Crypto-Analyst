"""Product floor is actual new exposure, never permission to exceed hard caps."""
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from decision_engine import paper_candidate_orchestrator as orchestration
from paper_trading import crypto_profit_agent as agent, risk_tiering
from paper_trading.execution_contract import ExecutionRules, MarketSnapshot, OrderIntent
from paper_trading.execution_model import execute_paper
from tests import test_behavior_paper_integration as fixtures
from tests import test_paper_candidate_orchestrator as sizing_fixtures


class MinimumSizingTests(unittest.TestCase):
    def size(self, *, equity=10000, exposure=6000, cash=10000, **changes):
        candidate = {**sizing_fixtures.candidate(), **changes, "technical_plan": sizing_fixtures.plan()}
        tier = risk_tiering.tier_for(candidate)
        prepared = orchestration.OrchestratedCandidate(candidate, tier, .001, 2, 2)
        return orchestration.size_candidate(prepared, equity=equity, exposure_cap=exposure, available_cash=cash)

    def test_normal_soft_target_raises_to_floor(self):
        self.assertEqual(self.size().notional, 100)

    def test_quarter_recovery_has_100_floor_with_150_hard_cap(self):
        for multiplier in (.25, 10):
            result = self.size(experiment_tier="RECOVERY_EXPLORATION", execution_size_multiplier=multiplier)
            self.assertGreaterEqual(result.notional, 100)
            self.assertLessEqual(result.notional, 150)

    def test_floor_is_not_ceiling_and_recovery_scales_on_larger_account(self):
        self.assertGreater(self.size(equity=100000).notional, 100)
        self.assertGreater(self.size(equity=100000, experiment_tier="RECOVERY_EXPLORATION").notional, 100)

    def test_floor_never_overrides_exposure_cash_asset_or_stop_risk_caps(self):
        for changes in ({"exposure":99}, {"cash":99}, {"risk_cap_pct":.25},
                        {"risk_per_trade_pct":.001}, {"equity":500, "experiment_tier":"RECOVERY_EXPLORATION"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.size(**changes).notional, 0)

    def test_invalid_sizing_evidence_cannot_be_rescued_by_floor(self):
        for value in (float("nan"), float("inf"), -1, None):
            self.assertEqual(self.size(risk_cap_pct=value).notional, 0)


class ActualFillMinimumTests(unittest.TestCase):
    setUp = fixtures.BehaviorPaperIntegrationTests.setUp
    technical_plan = fixtures.BehaviorPaperIntegrationTests.technical_plan
    candidate = fixtures.BehaviorPaperIntegrationTests.candidate
    live_state = fixtures.BehaviorPaperIntegrationTests.live_state

    def select(self, multiplier=1, direction="LONG"):
        candidate = self.candidate()
        candidate.update(execution_size_multiplier=multiplier, direction=direction)
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status":"ACTIONABLE", "candidates":[candidate], "review":{}}):
            self.assertEqual(agent.select_new_positions(self.con, fixtures.NOW, self.metadata), 1)
        return candidate

    def fill(self, volume=1_000_000, market="SPOT"):
        state = self.live_state("BTCUSDT", fixtures.NOW, market_type=market)
        state.ask_quote_volume = volume
        state.bid_quote_volume = volume
        return agent.fill_pending_from_live_state(self.con, fixtures.NOW, state, self.metadata)

    def test_selected_floor_produces_actual_open_at_least_100_with_frozen_rules(self):
        self.select()
        self.assertEqual(self.fill(), (1,0))
        status, notional = self.con.execute("SELECT status,notional FROM paper_agent_positions").fetchone()
        self.assertEqual(status, "OPEN")
        self.assertGreaterEqual(notional,100)
        evidence = json.loads(self.con.execute("SELECT evidence_json FROM paper_position_events WHERE event_type='ENTRY_FILLED'").fetchone()[0])
        self.assertEqual(evidence["execution_rules"]["symbol"], "BTCUSDT")

    def test_partial_below_100_is_cancelled_without_cash_or_open_mutation(self):
        self.select(multiplier=4)
        before = self.con.execute("SELECT cash,total_fees FROM paper_agent_portfolio").fetchone()
        self.assertEqual(self.fill(volume=6300), (0,1))
        self.assertEqual(self.con.execute("SELECT status,cancel_reason FROM paper_agent_positions").fetchone(),
                         ("CANCELLED", "ACTUAL_OPEN_NOTIONAL_BELOW_100_USDT"))
        self.assertEqual(self.con.execute("SELECT cash,total_fees FROM paper_agent_portfolio").fetchone(),before)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM paper_position_events WHERE event_type='ENTRY_REJECTED'").fetchone()[0],1)

    def test_partial_over_100_remains_permitted(self):
        self.select(multiplier=4)
        self.assertEqual(self.fill(volume=15000), (1,0))
        notional, partial = self.con.execute("SELECT notional,partial_fill FROM paper_agent_positions").fetchone()
        self.assertGreaterEqual(notional,100)
        self.assertLess(notional,200)
        self.assertEqual(partial,1)

    def test_replan_that_cannot_carry_floor_is_cancelled_with_frozen_thesis_intact(self):
        candidate = self.select()
        before = self.con.execute("SELECT thesis_json FROM paper_agent_decision_theses").fetchone()[0]
        at = fixtures.NOW + timedelta(seconds=30)
        revised = self.technical_plan(candidate,"SPOT")
        revised["generated_at"] = at.isoformat()
        revised["as_of"] = (at-timedelta(seconds=1)).isoformat()
        revised["risk"].update(stop_distance_pct=4.0,invalidation_price=96,invalidation_offset_pct=-4)
        with patch.object(agent.technical_trade_plan,"replan_reason",return_value="STRUCTURE_REFRESH_DUE"), patch.object(
            agent.technical_trade_plan,"rebuild_for_pending",return_value=revised):
            result=agent.fill_pending_from_live_state(self.con,at,self.live_state("BTCUSDT",at),self.metadata)
        self.assertEqual(result,(0,1))
        self.assertEqual(self.con.execute("SELECT cancel_reason FROM paper_agent_positions").fetchone()[0],
                         "TECHNICAL_REPLAN_NOTIONAL_BELOW_MINIMUM")
        self.assertEqual(self.con.execute("SELECT thesis_json FROM paper_agent_decision_theses").fetchone()[0],before)

    def test_short_actual_fill_respects_same_floor(self):
        self.select(direction="SHORT")
        self.assertEqual(self.fill(market="USD_M_PERPETUAL"),(1,0))
        self.assertGreaterEqual(self.con.execute("SELECT notional FROM paper_agent_positions").fetchone()[0],100)

    def test_reduce_only_dust_ignores_product_and_exchange_minimum(self):
        stamp=int(fixtures.NOW.timestamp()*1000)
        market=MarketSnapshot(100,100000,observed_at=fixtures.NOW.isoformat(),best_bid=100,best_ask=100,
                              quote_timestamp_ms=stamp,liquidity_timestamp_ms=stamp,source="BINANCE_WS",market_type="SPOT")
        result=execute_paper(OrderIntent("BTCUSDT","SELL",7,reduce_only=True),market,ExecutionRules(.000001,.000001,100))
        self.assertIsNotNone(result.fill)
        self.assertLess(result.fill.price*result.fill.quantity,100)
