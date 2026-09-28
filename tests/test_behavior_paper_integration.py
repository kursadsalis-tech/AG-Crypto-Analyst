import json
import sqlite3
import unittest
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from paper_trading import crypto_profit_agent as agent
from paper_trading.execution_contract import MarketSnapshot, ExecutionRules
from paper_trading.realtime_market import LiveState


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
        # Portfolio valuation fixtures require the closed-candle market table.
        self.con.execute("""CREATE TABLE IF NOT EXISTS market_klines(
            symbol TEXT, interval TEXT, open_time INTEGER, open REAL,
            high REAL, low REAL, close REAL, volume REAL,
            close_time INTEGER, quote_volume REAL
        )""")
        self.metadata = {"SPOT": object(), "USD_M_PERPETUAL": object()}
        rules_patch = patch.object(agent, "rules_for", return_value=ExecutionRules(.000001, .000001, 5))
        rules_patch.start()
        self.addCleanup(rules_patch.stop)
        plan_patch = patch.object(
            agent.technical_trade_plan,
            "build_for_candidate",
            side_effect=lambda con, candidate, now, market_type: self.technical_plan(
                candidate, market_type
            ),
        )
        plan_patch.start()
        self.addCleanup(plan_patch.stop)
        replan_patch = patch.object(
            agent.technical_trade_plan, "replan_reason", return_value=None
        )
        replan_patch.start()
        self.addCleanup(replan_patch.stop)
        self.addCleanup(self.con.close)

    def technical_plan(self, candidate, market_type):
        price = float(candidate.get("current_price") or 100.0)
        stop = float(candidate.get("stop_reference_pct") or 2.0)
        direction = str(candidate["direction"])
        if direction == "LONG":
            invalidation = price * (1.0 - stop / 100.0)
            tp1, tp2 = price * 1.01, price * 1.02
            low, high = price * 0.999, price * 1.001
        else:
            invalidation = price * (1.0 + stop / 100.0)
            tp1, tp2 = price * 0.99, price * 0.98
            low, high = price * 0.999, price * 1.001
        return {
            "version": agent.technical_trade_plan.VERSION,
            "symbol": candidate["symbol"],
            "direction": direction,
            "execution_market_type": market_type,
            "technical_source_market": "BINANCE_USD_M_PERPETUAL",
            "source_reference_price": price,
            "generated_at": NOW.isoformat(),
            "as_of": (NOW - timedelta(minutes=1)).isoformat(),
            "data_cutoff_ms": int((NOW - timedelta(minutes=1)).timestamp() * 1000),
            "freshness": {
                "ttl_seconds": agent.technical_trade_plan.MAX_ENTRY_PLAN_AGE_SECONDS,
                "maximum_market_data_age_seconds": agent.technical_trade_plan.MAX_BAR_AGE_SECONDS,
                "structure_refresh_seconds": agent.technical_trade_plan.STRUCTURE_REFRESH_SECONDS,
                "replan_price_move_pct": 0.50,
            },
            "trigger": {
                "event_kind": str(
                    ((candidate.get("evidence") or {}).get("event") or {}).get("kind")
                    or "MARKET_STRUCTURE"
                ),
                "horizon_hours": int(candidate.get("horizon") or 4),
                "primary_signal_source": (
                    (candidate.get("evidence") or {}).get("primary_signal_source")
                ),
                "evidence_minute_end_ms": (
                    (candidate.get("evidence") or {}).get("evidence_minute_end_ms")
                ),
            },
            "timeframes_used": ["1m", "5m", "15m", "1h", "4h"],
            "technical_score": 0.20,
            "relation_to_trigger_direction": "MIXED_OR_UNALIGNED",
            "quality_multiplier": 1.0,
            "entry": {
                "anchor": price, "low": low, "high": high,
                "low_offset_pct": 100 * (low / price - 1),
                "high_offset_pct": 100 * (high / price - 1),
            },
            "risk": {
                "invalidation_price": invalidation,
                "invalidation_offset_pct": 100 * (invalidation / price - 1),
                "stop_distance_pct": stop,
                "risk_compatible": stop <= 5.0,
            },
            "targets": {
                "tp1_price": tp1, "tp2_price": tp2,
                "tp1_offset_pct": 100 * (tp1 / price - 1),
                "tp2_offset_pct": 100 * (tp2 / price - 1),
            },
            "authority": {
                "direction_override": False, "ranking_input": True,
                "sizing_input": True, "entry_input": True, "stop_input": True,
                "take_profit_input": True, "single_indicator_hard_veto": False,
            },
            "real_order_allowed": False,
        }

    def live_state(self, symbol, at, market_type="SPOT", price=100.0):
        ms = int(at.timestamp() * 1000)
        return LiveState(
            symbol=symbol, market_type=market_type, last_price=price,
            best_bid=price - 0.01, best_ask=price + 0.01,
            visible_quote_volume=1_000_000,
            bid_quote_volume=1_000_000, ask_quote_volume=1_000_000,
            quote_source="TEST_BINANCE_BOOK",
            market_timestamp_ms=ms, quote_timestamp_ms=ms,
            liquidity_timestamp_ms=ms,
        )

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
            "stop_reference_pct": 2.0, "risk_per_trade_pct": 0.75,
            "evidence": evidence, "evidence_sha256": "a" * 64,
        }

    def fingerprinted_candidate(self, decision_id, current_price=100.0):
        candidate = self.candidate()
        candidate["decision_id"] = decision_id
        candidate["current_price"] = current_price
        candidate["evidence_sha256"] = (f"{decision_id:x}"[-1] or "a") * 64
        candidate["evidence"].update({
            "primary_signal_source": "PRICE_ACTION",
            "hypothesis_scope": "PRICE_ACTION_ONLY",
            "event": {"kind": "PRICE_ACTION", "id": f"price:{decision_id}"},
            "learned_pattern": {"flow_pattern": "PRICE_UP"},
            "directional_signals": [
                {"source": "PRICE_ACTION", "direction": "LONG"},
            ],
        })
        return candidate

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

    def test_technical_plan_is_frozen_as_real_decision_input(self):
        candidate = self.candidate()
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 1)
        thesis = json.loads(self.con.execute(
            "SELECT thesis_json FROM paper_agent_decision_theses"
        ).fetchone()[0])
        self.assertEqual(
            thesis["technical_trade_plan"]["timeframes_used"],
            ["1m", "5m", "15m", "1h", "4h"],
        )
        self.assertIn(
            "MULTITIMEFRAME_TECHNICAL_EXECUTION_PLAN",
            thesis["capabilities_under_test"],
        )
        self.assertNotIn(
            "TECHNICAL_CONTEXT_SHADOW",
            thesis["capabilities_observed_but_not_decision_inputs"],
        )

        current_plan = json.loads(self.con.execute(
            "SELECT technical_execution_plan_json FROM paper_agent_positions"
        ).fetchone()[0])
        self.assertEqual(
            current_plan["version"], thesis["technical_trade_plan"]["version"]
        )
        self.assertEqual(
            current_plan["data_cutoff_ms"],
            thesis["technical_trade_plan"]["data_cutoff_ms"],
        )


    def test_missing_technical_plan_is_rejected_before_position_write(self):
        candidate = self.candidate()
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }), patch.object(
            agent.technical_trade_plan, "build_for_candidate",
            side_effect=ValueError("no technical cache"),
        ):
            selected = agent.select_new_positions(self.con, NOW)
        self.assertEqual(selected, 0)
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM paper_agent_positions").fetchone()[0], 0,
        )
        reasons = json.loads(self.con.execute(
            "SELECT reason_counts_json FROM paper_behavior_selection_runs"
        ).fetchone()[0])
        self.assertEqual(reasons, {"TECHNICAL_PLAN_UNAVAILABLE": 1})


    def test_adverse_same_setup_reentry_requires_one_risk_unit_reset(self):
        first = self.fingerprinted_candidate(70, 100.0)
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [first], "review": {},
        }):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 1)
        self.con.execute("""UPDATE paper_agent_positions SET
            status='CLOSED',exit_time=?,exit_price=?,close_reason='PROTECTIVE_STOP'
            WHERE decision_reference_id=?""", (
            (NOW + timedelta(seconds=5)).isoformat(), 99.5, 70,
        ))
        self.con.commit()

        repeated = self.fingerprinted_candidate(71, 99.5)
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [repeated], "review": {},
        }):
            self.assertEqual(
                agent.select_new_positions(self.con, NOW + timedelta(seconds=10)), 0
            )
        reasons = json.loads(self.con.execute(
            "SELECT reason_counts_json FROM paper_behavior_selection_runs "
            "ORDER BY run_id DESC LIMIT 1"
        ).fetchone()[0])
        self.assertEqual(
            reasons, {"ADVERSE_SAME_SETUP_REENTRY_WITHOUT_1R_RESET": 1}
        )

        reset = self.fingerprinted_candidate(72, 97.0)
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [reset], "review": {},
        }):
            self.assertEqual(
                agent.select_new_positions(self.con, NOW + timedelta(seconds=20)), 1
            )

    def test_materially_new_independent_signal_allows_immediate_reentry(self):
        first = self.fingerprinted_candidate(73, 100.0)
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [first], "review": {},
        }):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 1)
        self.con.execute("""UPDATE paper_agent_positions SET
            status='CLOSED',exit_time=?,exit_price=?,close_reason='THESIS_INVALIDATED'
            WHERE decision_reference_id=?""", (
            (NOW + timedelta(seconds=5)).isoformat(), 100.0, 73,
        ))
        self.con.commit()

        changed = self.fingerprinted_candidate(74, 100.0)
        changed["evidence"]["directional_signals"].append(
            {"source": "TECHNICAL", "direction": "LONG"}
        )
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [changed], "review": {},
        }):
            self.assertEqual(
                agent.select_new_positions(self.con, NOW + timedelta(seconds=10)), 1
            )

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

    def test_signal_count_alone_cannot_manufacture_leverage(self):
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
        leverage = self.con.execute(
            "SELECT leverage FROM paper_agent_positions"
        ).fetchone()[0]
        self.assertEqual(leverage, 1)

    def test_five_independent_signals_allocate_margin_not_gross_exposure(self):
        candidate = self.candidate()
        candidate["experiment_tier"] = "PROMOTION_READY"
        candidate["confidence"] = .65
        candidate["expected_net_edge_pct"] = .30
        candidate["evidence"]["directional_signals"] = [
            {"source": source, "direction": "LONG"}
            for source in ("TECHNICAL", "NEWS", "ONCHAIN", "DERIVATIVES")
        ]
        candidate["evidence"]["execution_calibration"] = {"feedback": {
            "closed_count": 60, "independent_days": 12,
            "mean_net_return_pct": .30, "cost_after_win_rate": .65,
        }}
        candidate["evidence"]["learned_pattern"] = {
            "calibrated_net_edge_pct": .30, "calibrated_win_rate": .65,
        }
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
        live = self.live_state(
            candidate["symbol"], planned,
            market_type="USD_M_PERPETUAL", price=100.0,
        )
        self.assertEqual(
            agent.fill_pending_from_live_state(
                self.con, planned, live, self.metadata
            ),
            (1, 0),
        )
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

    def test_candidate_ranking_prefers_executable_profit_per_risk_over_edge_pct(self):
        tiny_high_edge = self.candidate()
        tiny_high_edge.update({
            "decision_id": 21, "symbol": "TINYUSDT",
            "expected_net_edge_pct": 1.00, "risk_cap_pct": 0.25,
            "stop_reference_pct": 5.0,
            "evidence_sha256": "1" * 64,
        })
        larger_lower_edge = self.candidate()
        larger_lower_edge.update({
            "decision_id": 22, "symbol": "LARGEUSDT",
            "expected_net_edge_pct": 0.30, "risk_cap_pct": 10.0,
            "stop_reference_pct": 0.5,
            "evidence_sha256": "2" * 64,
        })
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE",
            "candidates": [tiny_high_edge, larger_lower_edge],
            "review": {},
        }), patch.object(agent, "MAX_NEW_PER_CYCLE", 1):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 1)
        selected = self.con.execute(
            "SELECT symbol,notional FROM paper_agent_positions"
        ).fetchone()
        self.assertEqual(selected[0], "LARGEUSDT")
        self.assertAlmostEqual(selected[1], 50.0)

    def test_candidate_ranking_prefers_profit_per_risk_over_raw_profit(self):
        inefficient = self.candidate()
        inefficient.update({
            "decision_id": 23, "symbol": "RISKYUSDT",
            "expected_net_edge_pct": 0.40, "risk_cap_pct": 10.0,
            "stop_reference_pct": 5.0,
            "evidence_sha256": "3" * 64,
        })
        efficient = self.candidate()
        efficient.update({
            "decision_id": 24, "symbol": "EFFICIENTUSDT",
            "expected_net_edge_pct": 0.35, "risk_cap_pct": 0.25,
            "stop_reference_pct": 0.5,
            "evidence_sha256": "4" * 64,
        })
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE",
            "candidates": [inefficient, efficient],
            "review": {},
        }), patch.object(agent, "MAX_NEW_PER_CYCLE", 1):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 1)
        self.assertEqual(
            self.con.execute("SELECT symbol FROM paper_agent_positions").fetchone()[0],
            "EFFICIENTUSDT",
        )

    def test_candidate_ranking_uses_each_trade_dynamic_stop_distance(self):
        wide_stop = self.candidate()
        wide_stop.update({
            "decision_id": 25, "symbol": "WIDEUSDT",
            "expected_net_edge_pct": 0.45, "stop_reference_pct": 5.0,
            "evidence_sha256": "5" * 64,
        })
        tight_stop = self.candidate()
        tight_stop.update({
            "decision_id": 26, "symbol": "TIGHTUSDT",
            "expected_net_edge_pct": 0.30, "stop_reference_pct": 1.0,
            "evidence_sha256": "6" * 64,
        })
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [wide_stop, tight_stop],
            "review": {},
        }), patch.object(agent, "MAX_NEW_PER_CYCLE", 1):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 1)
        self.assertEqual(
            self.con.execute("SELECT symbol FROM paper_agent_positions").fetchone()[0],
            "TIGHTUSDT",
        )

    def test_dynamic_stop_distance_changes_position_size_for_same_risk_budget(self):
        tight = self.candidate()
        tight.update({
            "decision_id": 27, "symbol": "TIGHTSIZEUSDT",
            "stop_reference_pct": 1.0, "risk_cap_pct": 100.0, "risk_per_trade_pct": 0.5,
            "evidence_sha256": "7" * 64,
        })
        wide = self.candidate()
        wide.update({
            "decision_id": 28, "symbol": "WIDESIZEUSDT",
            "stop_reference_pct": 5.0, "risk_cap_pct": 100.0, "risk_per_trade_pct": 0.5,
            "evidence_sha256": "8" * 64,
        })
        # Remove the exploratory base-margin ceiling so this test isolates
        # stop-distance risk sizing rather than conviction sizing.
        fake_tier = SimpleNamespace(
            margin_fraction=1.0, leverage=1.0, market_type="SPOT",
            tier="TEST", independent_signal_count=1, signal_sources=("TEST",),
        )
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [tight, wide], "review": {},
        }), patch.object(agent.paper_candidate_orchestrator.risk_tiering, "tier_for", return_value=fake_tier), \
                patch.object(agent, "MAX_NEW_PER_CYCLE", 2):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 2)
        notionals = dict(self.con.execute(
            "SELECT symbol,notional FROM paper_agent_positions"
        ).fetchall())
        self.assertGreater(notionals["TIGHTSIZEUSDT"], notionals["WIDESIZEUSDT"])
        fee_rate = agent.taker_fee_rate("SPOT")
        tight_risk = notionals["TIGHTSIZEUSDT"] * (0.01 + 2 * fee_rate)
        wide_risk = notionals["WIDESIZEUSDT"] * (0.05 + 2 * fee_rate)
        self.assertAlmostEqual(tight_risk, wide_risk, places=6)
        self.assertAlmostEqual(tight_risk, 50.0, places=6)

    def test_selection_uses_same_venue_fee_contract_as_execution(self):
        candidate = self.candidate()
        candidate["risk_cap_pct"] = 100.0
        fake_tier = SimpleNamespace(
            margin_fraction=1.0, leverage=1.0, market_type="SPOT",
            tier="TEST", independent_signal_count=1, signal_sources=("TEST",),
        )
        with patch.dict("os.environ", {
            "AGCA_PAPER_SPOT_TAKER_FEE_BPS": "20",
        }, clear=False), patch.object(
            agent.behavior_decision_core, "evaluate",
            return_value={"status": "ACTIONABLE", "candidates": [candidate], "review": {}},
        ), patch.object(agent.paper_candidate_orchestrator.risk_tiering, "tier_for", return_value=fake_tier):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 1)
        row = self.con.execute(
            "SELECT notional,selection_reason FROM paper_agent_positions"
        ).fetchone()
        expected = 75.0 / (0.02 + 2 * 0.002)
        self.assertAlmostEqual(float(row[0]), expected, places=6)
        self.assertIn("taker_fee_bps=20.000", row[1])
        thesis = json.loads(self.con.execute(
            "SELECT thesis_json FROM paper_agent_decision_theses"
        ).fetchone()[0])
        execution_risk = thesis["execution_risk"]
        self.assertAlmostEqual(execution_risk["taker_fee_rate"], 0.002)
        self.assertAlmostEqual(
            execution_risk["planned_stop_and_fee_risk_usdt"],
            float(row[0]) * (0.02 + 2 * 0.002),
            places=6,
        )

    def test_full_portfolio_still_exports_ranked_opportunity_telemetry(self):
        for index in range(agent.MAX_OPEN_POSITIONS):
            candidate = self.candidate()
            candidate.update({
                "decision_id": 100 + index,
                "symbol": f"OPEN{index}USDT",
                "evidence_sha256": f"{index + 1:x}" * 64,
            })
            with patch.object(agent.behavior_decision_core, "evaluate", return_value={
                "status": "ACTIONABLE", "candidates": [candidate], "review": {},
            }):
                self.assertEqual(agent.select_new_positions(self.con, NOW), 1)
            self.con.execute(
                "UPDATE paper_agent_positions SET status='OPEN', entry_price=100, quantity=0.5 "
                "WHERE symbol=?", (candidate["symbol"],),
            )
            self.con.execute(
                "INSERT INTO market_klines VALUES(?,?,?,?,?,?,?,?,?,?)",
                (candidate["symbol"], "1h", 0, 100, 100, 100, 100, 1,
                 int(NOW.timestamp() * 1000), 100),
            )
            self.con.commit()
        telemetry = {}
        fresh = self.candidate()
        fresh.update({"decision_id": 999, "symbol": "FRESHUSDT",
                      "evidence_sha256": "f" * 64})
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [fresh], "review": {},
        }):
            selected = agent.select_new_positions(self.con, NOW, telemetry=telemetry)
        self.assertEqual(selected, 0)
        self.assertTrue(telemetry["candidates"])
        self.assertEqual(
            telemetry["candidates"][0]["reason"],
            "PORTFOLIO_POSITION_CAPACITY_FULL",
        )

    def test_existing_pending_entry_reserves_cash_and_gross_exposure(self):
        pending = self.candidate()
        pending.update({
            "decision_id": 31, "symbol": "PENDINGUSDT",
            "risk_cap_pct": 10.0, "evidence_sha256": "9" * 64,
        })
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [pending], "review": {},
        }):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 1)
        first_notional = self.con.execute(
            "SELECT notional FROM paper_agent_positions WHERE symbol='PENDINGUSDT'"
        ).fetchone()[0]

        second = self.candidate()
        second.update({
            "decision_id": 32, "symbol": "SECONDUSDT",
            "risk_cap_pct": 10.0, "evidence_sha256": "b" * 64,
        })
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [second], "review": {},
        }):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 1)
        second_notional = self.con.execute(
            "SELECT notional FROM paper_agent_positions WHERE symbol='SECONDUSDT'"
        ).fetchone()[0]
        self.assertGreater(first_notional, 0)
        self.assertGreater(second_notional, 0)
        self.assertLessEqual(first_notional + second_notional, 10000.0 * agent.MAX_TOTAL_EXPOSURE)

    def test_stale_open_position_mark_blocks_new_risk(self):
        self.con.execute("""INSERT INTO paper_agent_positions(
            forecast_id,agent_version,policy_version,symbol,horizon_hours,direction,
            status,selected_at,as_of,planned_entry_time,due_time,notional,confidence,
            p_up,p_down,target_price,model_version,artifact_hash,selection_reason,
            market_type,validation_cohort,evidence_eligible,strategy_id,decision_source,
            decision_reference_id,leverage,margin_allocated,risk_tier,
            independent_signal_count,signal_sources_json,entry_time,entry_price,quantity,
            entry_fee
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            -90, "test", "test", "STALEUSDT", 1, "LONG", "OPEN",
            NOW.isoformat(), NOW.isoformat(), NOW.isoformat(),
            (NOW + timedelta(hours=1)).isoformat(), 100.0, .6, .6, .4, 101.0,
            "test", "c"*64, "test", "SPOT", agent.FORWARD_COHORT, 1,
            "test", "MARKET_BEHAVIOR_CORE", 90, 1.0, 100.0, "TEST", 1, "[]",
            NOW.isoformat(), 100.0, 1.0, 0.1,
        ))
        stale_close = int((NOW - timedelta(hours=2)).timestamp() * 1000)
        self.con.execute("""INSERT INTO market_klines(
            symbol,interval,open_time,open,high,low,close,volume,close_time,quote_volume
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""", (
            "STALEUSDT","1h",stale_close-3599999,100,100,100,100,1,stale_close,100,
        ))
        candidate = self.candidate()
        candidate.update({"decision_id": 91, "symbol": "FRESHUSDT", "evidence_sha256": "d"*64})
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 0)

    def add_open_mark_position(self):
        self.con.execute("""INSERT INTO paper_agent_positions(
            forecast_id,agent_version,policy_version,symbol,horizon_hours,direction,
            status,selected_at,as_of,planned_entry_time,due_time,notional,confidence,
            p_up,p_down,target_price,model_version,artifact_hash,selection_reason,
            market_type,validation_cohort,evidence_eligible,strategy_id,decision_source,
            decision_reference_id,leverage,margin_allocated,risk_tier,
            independent_signal_count,signal_sources_json,entry_time,entry_price,quantity,
            entry_fee
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            -92, "test", "test", "MARKUSDT", 1, "LONG", "OPEN",
            NOW.isoformat(), NOW.isoformat(), NOW.isoformat(),
            (NOW + timedelta(hours=1)).isoformat(), 100.0, .6, .6, .4, 101.0,
            "test", "e"*64, "test", "SPOT", agent.FORWARD_COHORT, 1,
            "test", "MARKET_BEHAVIOR_CORE", 92, 1.0, 100.0, "TEST", 1, "[]",
            NOW.isoformat(), 100.0, 1.0, 0.1,
        ))
    def test_portfolio_uses_fresh_mark_for_equity_and_gross_exposure(self):
        self.add_open_mark_position()
        close_time = int((NOW - timedelta(minutes=5)).timestamp() * 1000)
        self.con.execute("""INSERT INTO market_klines(
            symbol,interval,open_time,open,high,low,close,volume,close_time,quote_volume
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""", (
            "MARKUSDT","1h",close_time-3599999,100,121,99,120,1,close_time,120,
        ))
        snapshot = agent.portfolio_snapshot(self.con, NOW)
        self.assertTrue(snapshot["valuation_complete"])
        self.assertAlmostEqual(snapshot["gross_exposure"], 120.0)
        self.assertAlmostEqual(snapshot["unrealized_pnl"], 20.0)

    def test_realtime_portfolio_values_dynamic_spot_from_executable_side(self):
        self.add_open_mark_position()
        quote = MarketSnapshot(
            reference_price=999, available_quote_volume=None,
            observed_at=NOW.isoformat(), best_bid=119, best_ask=121,
            quote_timestamp_ms=int(NOW.timestamp()*1000)-100,
            market_type="SPOT", source="BINANCE_SPOT_WS_TICKER_BID_ASK",
        )
        snapshot = agent.portfolio_snapshot(self.con, NOW,
            market_snapshots={("MARKUSDT", "SPOT"): quote})
        self.assertTrue(snapshot["valuation_complete"])
        self.assertEqual(snapshot["gross_exposure"], 119)
        self.assertEqual(snapshot["unrealized_pnl"], 19)
        self.con.execute("UPDATE paper_agent_positions SET direction='SHORT'")
        snapshot = agent.portfolio_snapshot(self.con, NOW,
            market_snapshots={("MARKUSDT", "SPOT"): quote})
        self.assertEqual(snapshot["gross_exposure"], 121)
        self.assertEqual(snapshot["unrealized_pnl"], -21)

    def test_realtime_valuation_rejects_stale_future_or_wrong_market_quote(self):
        from dataclasses import replace
        self.add_open_mark_position()
        quote = MarketSnapshot(reference_price=100, available_quote_volume=None,
            observed_at=NOW.isoformat(), best_bid=99, best_ask=101,
            quote_timestamp_ms=int(NOW.timestamp()*1000),
            market_type="SPOT", source="BINANCE_SPOT_WS_TICKER_BID_ASK")
        for changed in (
            replace(quote, quote_timestamp_ms=quote.quote_timestamp_ms-2001),
            replace(quote, quote_timestamp_ms=quote.quote_timestamp_ms+1),
            replace(quote, market_type="USD_M_PERPETUAL"),
            replace(quote, best_bid=102),
        ):
            snapshot = agent.portfolio_snapshot(self.con, NOW,
                market_snapshots={("MARKUSDT", "SPOT"): changed})
            self.assertFalse(snapshot["valuation_complete"])
            self.assertEqual(snapshot["missing_marks"], ("MARKUSDT",))
        self.assertFalse(agent.portfolio_snapshot(
            self.con, NOW, market_snapshots={})["valuation_complete"])

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
        at = NOW + timedelta(minutes=2)
        live = self.live_state("CRVUSDT", at, market_type="SPOT", price=100.0)
        filled, cancelled = agent.fill_pending_from_live_state(
            self.con, at, live, self.metadata
        )
        self.assertEqual((filled, cancelled), (1, 0))
        self.assertEqual(
            self.con.execute(
                "SELECT status,market_type,technical_basis_ratio "
                "FROM paper_agent_positions"
            ).fetchone()[:2],
            ("OPEN", "SPOT"),
        )
        ratio = self.con.execute(
            "SELECT technical_basis_ratio FROM paper_agent_positions"
        ).fetchone()[0]
        self.assertIsNotNone(ratio)
        self.assertGreater(ratio, 0)


    def test_pending_entry_replans_locally_without_mutating_selection_thesis(self):
        candidate = self.candidate()
        candidate.update({
            "decision_id": 901,
            "symbol": "REPLANUSDT",
            "evidence_sha256": "9" * 64,
        })
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 1)

        original_notional = float(self.con.execute(
            "SELECT notional FROM paper_agent_positions"
        ).fetchone()[0])
        frozen_thesis = json.loads(self.con.execute(
            "SELECT thesis_json FROM paper_agent_decision_theses"
        ).fetchone()[0])
        self.assertAlmostEqual(
            frozen_thesis["technical_trade_plan"]["risk"]["stop_distance_pct"],
            2.0,
        )

        at = NOW + timedelta(seconds=30)
        replanned = self.technical_plan(candidate, "SPOT")
        replanned["generated_at"] = at.isoformat()
        replanned["as_of"] = (at - timedelta(seconds=1)).isoformat()
        replanned["data_cutoff_ms"] = int(
            (at - timedelta(seconds=1)).timestamp() * 1000
        )
        replanned["risk"]["stop_distance_pct"] = 4.0
        replanned["risk"]["invalidation_price"] = 96.0
        replanned["risk"]["invalidation_offset_pct"] = -4.0
        replanned["risk"]["risk_compatible"] = True

        with patch.object(
            agent.technical_trade_plan, "replan_reason",
            return_value="STRUCTURE_REFRESH_DUE",
        ), patch.object(
            agent.technical_trade_plan, "rebuild_for_pending",
            return_value=replanned,
        ):
            filled, cancelled = agent.fill_pending_from_live_state(
                self.con, at,
                self.live_state("REPLANUSDT", at, market_type="SPOT", price=100.0),
                self.metadata,
            )
        self.assertEqual((filled, cancelled), (1, 0))

        status, revised_notional, basis, plan_json = self.con.execute("""
            SELECT status,notional,technical_basis_ratio,
                   technical_execution_plan_json
            FROM paper_agent_positions
        """).fetchone()
        self.assertEqual(status, "OPEN")
        self.assertLess(float(revised_notional), original_notional)
        self.assertIsNotNone(basis)
        current_plan = json.loads(plan_json)
        self.assertAlmostEqual(
            current_plan["risk"]["stop_distance_pct"], 4.0
        )

        # Original selection evidence remains immutable after execution replan.
        after_thesis = json.loads(self.con.execute(
            "SELECT thesis_json FROM paper_agent_decision_theses"
        ).fetchone()[0])
        self.assertEqual(after_thesis, frozen_thesis)
        self.assertAlmostEqual(
            after_thesis["technical_trade_plan"]["risk"]["stop_distance_pct"],
            2.0,
        )

    def test_replanned_entry_runs_full_position_management_lifecycle(self):
        from risk_manager import risk_manager

        candidate = self.candidate()
        candidate.update({
            "decision_id": 1901,
            "symbol": "LIFECYCLEUSDT",
            "evidence_sha256": "1" * 64,
        })
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 1)

        frozen_thesis = json.loads(self.con.execute(
            "SELECT thesis_json FROM paper_agent_decision_theses"
        ).fetchone()[0])
        at = NOW + timedelta(seconds=30)
        replanned = self.technical_plan(candidate, "SPOT")
        replanned["generated_at"] = at.isoformat()
        replanned["as_of"] = (at - timedelta(seconds=1)).isoformat()
        replanned["data_cutoff_ms"] = int(
            (at - timedelta(seconds=1)).timestamp() * 1000
        )
        replanned["risk"]["stop_distance_pct"] = 4.0
        replanned["risk"]["invalidation_price"] = 96.0
        replanned["risk"]["invalidation_offset_pct"] = -4.0
        replanned["risk"]["risk_compatible"] = True

        with patch.object(
            agent.technical_trade_plan, "replan_reason",
            return_value="STRUCTURE_REFRESH_DUE",
        ), patch.object(
            agent.technical_trade_plan, "rebuild_for_pending",
            return_value=replanned,
        ):
            self.assertEqual(
                agent.fill_pending_from_live_state(
                    self.con, at,
                    self.live_state(
                        "LIFECYCLEUSDT", at, market_type="SPOT", price=100.0
                    ),
                    self.metadata,
                ),
                (1, 0),
            )

        position = self.con.execute("""
            SELECT position_id,entry_time,entry_price,quantity,technical_basis_ratio,
                   technical_execution_plan_json,status
            FROM paper_agent_positions
        """).fetchone()
        self.assertEqual(position[6], "OPEN")
        position_id, entry_time, entry_price, original_qty, basis, plan_json, _ = position
        current_plan = json.loads(plan_json)
        self.assertAlmostEqual(
            current_plan["risk"]["stop_distance_pct"], 4.0
        )

        # Position management requires the same pre-entry risk evidence that is
        # present in production. Keep this lifecycle test on the real schema
        # rather than bypassing that fail-closed prerequisite.
        risk_manager.ensure_tables(self.con)
        risk_at = at - timedelta(seconds=1)
        policy_id = self.con.execute("""
            INSERT INTO risk_policy_snapshots(
                created_at,policy_version,market_state,coverage_count,coverage_total,
                max_total_exposure_pct,max_alt_exposure_pct,max_single_position_pct,
                risk_per_trade_pct,daily_loss_limit_pct,max_new_positions,
                new_positions_allowed,reason_json,detail_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            risk_at.isoformat(), "TEST", "NORMAL", 1, 1,
            60.0, 60.0, 10.0, 0.75, 5.0, 3, 1, "[]", "{}",
        )).lastrowid
        self.con.execute("""
            INSERT INTO asset_risk_snapshots(
                policy_snapshot_id,symbol,as_of,price,return_24h_pct,range_24h_pct,
                hourly_volatility_pct,drawdown_7d_pct,quote_volume_24h,risk_score,
                risk_tier,max_position_pct,volatility_stop_reference_pct,
                new_position_allowed,reason_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            policy_id, "LIFECYCLEUSDT", risk_at.isoformat(), float(entry_price),
            0.0, 2.0, 1.0, 0.0, 1_000_000.0, 0, "LOW", 10.0, 2.0, 1,
            "[]", risk_at.isoformat(),
        ))
        self.con.commit()

        initialized = agent.position_management.initialize_open_positions(
            self.con, at + timedelta(milliseconds=100)
        )
        self.assertEqual(initialized, {"initialized": 1, "blocked": []})

        managed = self.con.execute("""
            SELECT current_stop_price,tp1_price,tp2_price,remaining_quantity
            FROM paper_position_management WHERE position_id=?
        """, (position_id,)).fetchone()
        expected = agent.technical_trade_plan.execution_levels(
            current_plan, float(entry_price), basis_ratio=basis
        )
        self.assertAlmostEqual(managed[0], expected["invalidation_price"])
        self.assertAlmostEqual(managed[1], expected["tp1_price"])
        self.assertAlmostEqual(managed[2], expected["tp2_price"])
        self.assertAlmostEqual(managed[3], float(original_qty))

        rules = ExecutionRules(.000001, .000001, 5)
        tp_at = at + timedelta(seconds=1)
        reduced = agent.position_management.process_market_state(
            self.con,
            self.live_state(
                "LIFECYCLEUSDT", tp_at, market_type="SPOT", price=float(managed[1])
            ),
            tp_at,
            rules,
        )
        self.assertEqual(
            (reduced["status"], reduced["reason"]),
            ("POSITION_REDUCED", "TAKE_PROFIT_TECHNICAL_1"),
        )
        remaining_qty, tightened_stop = self.con.execute("""
            SELECT remaining_quantity,current_stop_price
            FROM paper_position_management WHERE position_id=?
        """, (position_id,)).fetchone()
        self.assertLess(float(remaining_qty), float(original_qty))
        self.assertGreater(float(tightened_stop), float(entry_price))

        close_at = tp_at + timedelta(seconds=1)
        closed = agent.position_management.process_market_state(
            self.con,
            self.live_state(
                "LIFECYCLEUSDT", close_at, market_type="SPOT",
                price=float(tightened_stop) * 0.999,
            ),
            close_at,
            rules,
        )
        self.assertEqual(
            (closed["status"], closed["reason"]),
            ("POSITION_CLOSED", "PROTECTIVE_STOP"),
        )
        self.assertEqual(
            self.con.execute("""
                SELECT status,close_reason FROM paper_agent_positions
                WHERE position_id=?
            """, (position_id,)).fetchone(),
            ("CLOSED", "PROTECTIVE_STOP"),
        )
        self.assertAlmostEqual(
            self.con.execute("""
                SELECT remaining_quantity FROM paper_position_management
                WHERE position_id=?
            """, (position_id,)).fetchone()[0],
            0.0,
        )

        events = self.con.execute("""
            SELECT event_type,reason,real_order_allowed
            FROM paper_position_events WHERE position_id=?
            ORDER BY event_id
        """, (position_id,)).fetchall()
        event_types = [row[0] for row in events]
        self.assertIn("STOP_INITIALIZED", event_types)
        self.assertIn("POSITION_REDUCED", event_types)
        self.assertIn("RISK_EXIT_DECIDED", event_types)
        self.assertIn("POSITION_CLOSED", event_types)
        self.assertTrue(all(row[2] == 0 for row in events))

        # Replanning may update the mutable execution plan, but the original
        # selection thesis must stay immutable through the complete lifecycle.
        self.assertEqual(
            json.loads(self.con.execute(
                "SELECT thesis_json FROM paper_agent_decision_theses"
            ).fetchone()[0]),
            frozen_thesis,
        )
        position_net = self.con.execute("""
            SELECT net_pnl FROM paper_agent_positions WHERE position_id=?
        """, (position_id,)).fetchone()[0]
        portfolio_net = self.con.execute("""
            SELECT realized_pnl FROM paper_agent_portfolio WHERE portfolio_id=1
        """).fetchone()[0]
        self.assertAlmostEqual(float(portfolio_net), float(position_net))


    def test_realtime_housekeeping_cancels_expired_pending_without_live_quote(self):
        candidate = self.candidate()
        candidate.update({"decision_id": 1001, "symbol": "ZOMBIEUSDT",
                          "evidence_sha256": "f" * 64})
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 1)
        cancelled = agent.cancel_expired_pending_entries(
            self.con, NOW + timedelta(minutes=11)
        )
        self.assertEqual(cancelled, 1)
        self.assertEqual(
            self.con.execute("""SELECT status,cancel_reason
                FROM paper_agent_positions""").fetchone(),
            ("CANCELLED", "EXACT_SPOT_ENTRY_CAPTURE_EXPIRED"),
        )
        event = self.con.execute("""SELECT event_type,reason,real_order_allowed
            FROM paper_position_events ORDER BY event_id DESC LIMIT 1""").fetchone()
        self.assertEqual(
            event,
            ("ENTRY_REJECTED", "ENTRY_OBSERVATION_WINDOW_EXPIRED", 0),
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
            ("CANCELLED", "EXACT_SPOT_ENTRY_CAPTURE_EXPIRED"),
        )

    def test_implausible_spot_usdm_basis_fails_closed(self):
        candidate = self.candidate()
        candidate.update({"decision_id": 11, "symbol": "LAUSDT",
                          "evidence_sha256": "e" * 64})
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 1)
        at = NOW + timedelta(seconds=30)
        live = self.live_state("LAUSDT", at, market_type="SPOT", price=110.0)
        filled, cancelled = agent.fill_pending_from_live_state(
            self.con, at, live, self.metadata
        )
        self.assertEqual((filled, cancelled), (0, 1))
        self.assertEqual(
            self.con.execute(
                "SELECT status,cancel_reason FROM paper_agent_positions"
            ).fetchone(),
            ("CANCELLED", "TECHNICAL_EXECUTION_BASIS_INVALID"),
        )

    def test_usdm_entry_waits_outside_frozen_technical_zone(self):
        candidate = self.candidate()
        candidate.update({"decision_id": 12, "symbol": "WAITUSDT",
                          "evidence_sha256": "f" * 64})
        tier = SimpleNamespace(
            margin_fraction=.005, leverage=1.0,
            market_type="USD_M_PERPETUAL", tier="TEST",
            independent_signal_count=1, signal_sources=("MARKET_BEHAVIOR",),
        )
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }), patch.object(agent.paper_candidate_orchestrator.risk_tiering, "tier_for", return_value=tier):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 1)
        at = NOW + timedelta(seconds=30)
        live = self.live_state(
            "WAITUSDT", at, market_type="USD_M_PERPETUAL", price=101.0
        )
        filled, cancelled = agent.fill_pending_from_live_state(
            self.con, at, live, self.metadata
        )
        self.assertEqual((filled, cancelled), (0, 0))
        self.assertEqual(
            self.con.execute("SELECT status FROM paper_agent_positions").fetchone()[0],
            "PENDING_ENTRY",
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
