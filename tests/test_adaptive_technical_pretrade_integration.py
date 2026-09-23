import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from paper_trading import crypto_profit_agent as agent
from paper_trading.realtime_market import LiveState


NOW = datetime(2026, 9, 23, 1, 45, tzinfo=timezone.utc)


class AdaptiveTechnicalPretradeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        agent.ensure_tables(self.con)

    def tearDown(self):
        self.con.close()

    @staticmethod
    def candidate(decision_id=501):
        minute = int((NOW - timedelta(minutes=1)).timestamp() * 1000)
        return {
            "decision_id": decision_id,
            "decision_source": "MARKET_BEHAVIOR_CORE",
            "symbol": "BTCUSDT",
            "instrument": "BTC-USDT-SWAP",
            "horizon": 4,
            "direction": "LONG",
            "confidence": .60,
            "expected_net_edge_pct": .30,
            "execution_size_multiplier": 1.0,
            "experiment_tier": "EXPLORATORY_LEARNING",
            "current_price": 100.0,
            "target_price": 100.3,
            "risk_cap_pct": 10.0,
            "stop_reference_pct": 2.0,
            "risk_per_trade_pct": .75,
            "evidence": {
                "evidence_minute_end_ms": minute,
                "capabilities_used": [
                    "ORDER_BOOK", "TAKER_FLOW", "SPREAD_COST",
                    "FUNDING", "LIQUIDATIONS", "RISK_GATE",
                ],
                "primary_signal_source": "MARKET_BEHAVIOR",
                "directional_signals": [
                    {"source": "MARKET_BEHAVIOR", "direction": "LONG"},
                ],
                "event": {
                    "kind": "MARKET_STRUCTURE",
                    "id": f"market:{decision_id}",
                },
                "unified_forecast_used": False,
            },
            "evidence_sha256": (f"{decision_id:x}"[-1]) * 64,
        }

    @staticmethod
    def technical_plan(
        *, generated_at=NOW, low=99.0, high=101.0, anchor=100.0,
        stop_pct=2.0, stop_price=98.0, tp1=102.0, tp2=104.0,
        quality=.90, replan_move_pct=10.0,
    ):
        return {
            "version": "TEST_TECHNICAL_PLAN_V2",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "analysis_market_type": "USD_M_PERPETUAL",
            "execution_market_type": "SPOT",
            "source": "BINANCE_CANONICAL_LOCAL_CACHE",
            "network_io": False,
            "generated_at": generated_at.isoformat(),
            "as_of": (generated_at - timedelta(seconds=1)).isoformat(),
            "freshness": {
                "ttl_seconds": 180,
                "expires_at": (generated_at + timedelta(seconds=180)).isoformat(),
                "maximum_bar_age_seconds": 180,
                "replan_price_move_pct": replan_move_pct,
            },
            "trigger": {
                "event_kind": "MARKET_STRUCTURE",
                "horizon_hours": 4,
                "primary_signal_source": "MARKET_BEHAVIOR",
                "evidence_minute_end_ms": int(
                    (NOW - timedelta(minutes=1)).timestamp() * 1000
                ),
            },
            "profile": "STRUCTURE_BREAKOUT",
            "timeframes_requested": ["5m", "15m", "1h", "4h"],
            "timeframes_used": ["5m", "15m", "1h", "4h"],
            "timeframes_unavailable": [],
            "technical_score": .35,
            "relation_to_trigger_direction": "SUPPORTS_DIRECTION",
            "quality_multiplier": quality,
            "reference_price": 100.0,
            "entry": {
                "low": low, "high": high, "anchor": anchor,
                "chase_allowed": False,
                "timing": "WAIT_FOR_FRESH_EXECUTABLE_QUOTE_INSIDE_ZONE",
                "basis": "TEST",
            },
            "risk": {
                "invalidation_price": stop_price,
                "stop_distance_pct": stop_pct,
                "risk_compatible": stop_pct <= 5.0,
                "maximum_policy_stop_pct": 5.0,
                "basis": "TEST_STRUCTURE_ATR",
            },
            "targets": {
                "tp1_price": tp1, "tp2_price": tp2,
                "tp1_reward_pct": 2.0, "tp2_reward_pct": 4.0,
                "basis": "TEST_STRUCTURE",
            },
            "frames": {},
            "higher_context": {},
            "authority": {
                "direction_override": False,
                "ranking_input": True,
                "sizing_input": True,
                "entry_input": True,
                "stop_input": True,
                "take_profit_input": True,
                "single_indicator_hard_veto": False,
            },
            "real_order_allowed": False,
        }

    @staticmethod
    def live(price, at=NOW):
        ms = int(at.timestamp() * 1000)
        return LiveState(
            symbol="BTCUSDT",
            market_type="SPOT",
            last_price=price,
            best_bid=price - .01,
            best_ask=price,
            book_imbalance=.2,
            taker_imbalance=.2,
            visible_quote_volume=1_000_000,
            bid_quote_volume=1_000_000,
            ask_quote_volume=1_000_000,
            market_timestamp_ms=ms,
            quote_timestamp_ms=ms,
            liquidity_timestamp_ms=ms,
            quote_source="TEST_BINANCE_BOOK",
        )

    def select_with_plan(self, candidate=None, plan_value=None):
        candidate = candidate or self.candidate()
        plan_value = plan_value or self.technical_plan()

        def build_before_position_write(*args, **kwargs):
            count = self.con.execute(
                "SELECT COUNT(*) FROM paper_agent_positions"
            ).fetchone()[0]
            self.assertEqual(count, 0)
            return plan_value

        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }), patch.object(
            agent.technical_trade_plan, "build_for_candidate",
            side_effect=build_before_position_write,
        ):
            selected = agent.select_new_positions(
                self.con, NOW, technical_planning=True
            )
        return selected

    def test_plan_is_preselection_input_and_frozen_as_active_capability(self):
        selected = self.select_with_plan()
        self.assertEqual(selected, 1)
        row = self.con.execute("""
            SELECT notional,target_price,technical_plan_json,
                   technical_plan_version,market_type
            FROM paper_agent_positions
        """).fetchone()
        self.assertAlmostEqual(row[0], 45.0)
        self.assertAlmostEqual(row[1], 104.0)
        self.assertIsNotNone(row[2])
        self.assertEqual(row[3], "TEST_TECHNICAL_PLAN_V2")
        self.assertEqual(row[4], "SPOT")

        thesis = json.loads(self.con.execute(
            "SELECT thesis_json FROM paper_agent_decision_theses"
        ).fetchone()[0])
        self.assertEqual(
            thesis["technical_trade_plan"]["entry"]["anchor"], 100.0
        )
        self.assertEqual(thesis["execution_risk"]["planned_stop_distance_pct"], 2.0)
        self.assertEqual(thesis["execution_risk"]["planned_tp1_price"], 102.0)
        self.assertEqual(thesis["execution_risk"]["planned_tp2_price"], 104.0)
        self.assertIn(
            "MULTITIMEFRAME_TECHNICAL_EXECUTION_PLAN",
            thesis["capabilities_under_test"],
        )
        self.assertNotIn(
            "TECHNICAL_CONTEXT_SHADOW",
            thesis["capabilities_observed_but_not_decision_inputs"],
        )

    def test_missing_active_plan_rejects_before_position_write(self):
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [self.candidate()], "review": {},
        }), patch.object(
            agent.technical_trade_plan, "build_for_candidate",
            side_effect=ValueError("cache not warm"),
        ):
            selected = agent.select_new_positions(
                self.con, NOW, technical_planning=True
            )
        self.assertEqual(selected, 0)
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM paper_agent_positions").fetchone()[0],
            0,
        )
        reasons = json.loads(self.con.execute(
            "SELECT reason_counts_json FROM paper_behavior_selection_runs"
        ).fetchone()[0])
        self.assertEqual(reasons, {"TECHNICAL_TRADE_PLAN_UNAVAILABLE": 1})

    def test_pending_entry_does_not_chase_price_outside_fresh_zone(self):
        self.assertEqual(self.select_with_plan(), 1)
        filled, cancelled = agent.fill_pending_from_live_state(
            self.con, NOW + timedelta(seconds=1),
            self.live(105.0, NOW + timedelta(seconds=1)),
        )
        self.assertEqual((filled, cancelled), (0, 0))
        self.assertEqual(
            self.con.execute("SELECT status FROM paper_agent_positions").fetchone()[0],
            "PENDING_ENTRY",
        )
        self.assertEqual(
            self.con.execute(
                "SELECT COUNT(*) FROM paper_execution_market_observations"
            ).fetchone()[0],
            0,
        )

    def test_stale_plan_replans_and_can_reduce_notional_before_fill(self):
        candidate = self.candidate(502)
        candidate["risk_per_trade_pct"] = .01
        initial = self.technical_plan(
            stop_pct=.5, stop_price=99.5, quality=1.0, replan_move_pct=.1
        )
        self.assertEqual(self.select_with_plan(candidate, initial), 1)
        before = self.con.execute(
            "SELECT notional FROM paper_agent_positions"
        ).fetchone()[0]
        self.assertAlmostEqual(before, 50.0)

        replanned = self.technical_plan(
            generated_at=NOW + timedelta(seconds=5),
            low=99.5, high=100.5, anchor=100.0,
            stop_pct=5.0, stop_price=95.0,
            quality=1.0, replan_move_pct=.5,
        )
        with patch.object(
            agent.technical_trade_plan, "replan_reason",
            return_value="PLAN_TTL_EXPIRED",
        ), patch.object(
            agent.technical_trade_plan, "rebuild_for_position",
            return_value=replanned,
        ):
            filled, cancelled = agent.fill_pending_from_live_state(
                self.con, NOW + timedelta(seconds=5),
                self.live(100.0, NOW + timedelta(seconds=5)),
            )
        self.assertEqual((filled, cancelled), (1, 0))
        status, notional, plan_json = self.con.execute("""
            SELECT status,notional,technical_plan_json
            FROM paper_agent_positions
        """).fetchone()
        self.assertEqual(status, "OPEN")
        self.assertLess(notional, before)
        current_plan = json.loads(plan_json)
        self.assertEqual(current_plan["risk"]["stop_distance_pct"], 5.0)


if __name__ == "__main__":
    unittest.main()
