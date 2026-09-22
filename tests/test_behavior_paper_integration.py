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
            "stop_reference_pct": 2.0, "risk_per_trade_pct": 0.75,
            "evidence": evidence, "evidence_sha256": "a" * 64,
        }

    def technical_plan(self, *, relation="SUPPORTS_DIRECTION", quality=1.10):
        return {
            "version": "TECHNICAL_TRADE_PLAN_V1_MULTISKILL",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "market_type": "SPOT",
            "generated_at": NOW.isoformat(),
            "source": "BINANCE_CANONICAL",
            "timeframes_used": ["1m", "5m", "15m", "1h", "4h"],
            "technical_score": 0.50 if quality >= 1 else -0.50,
            "relation_to_behavior_direction": relation,
            "quality_multiplier": quality,
            "entry": {
                "low": 99.7, "high": 100.1, "anchor": 99.9,
                "basis": "TEST",
            },
            "risk": {
                "invalidation_price": 98.5,
                "stop_distance_pct": 1.6,
                "risk_compatible": True,
                "maximum_policy_stop_pct": 5.0,
                "basis": "TEST",
            },
            "targets": {
                "tp1_price": 101.2,
                "tp2_price": 103.4,
                "tp1_reward_pct": 1.3,
                "tp2_reward_pct": 3.5,
                "basis": "TEST",
            },
            "frames": {},
            "higher_context": {"trend_1h": "BULLISH", "trend_4h": "BULLISH"},
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

    def test_canonical_realtime_selection_uses_multiskill_technical_plan(self):
        candidate = self.candidate()
        plan = self.technical_plan()
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }), patch.object(
            agent.technical_trade_plan, "build_for_candidate", return_value=plan
        ):
            selected = agent.select_new_positions(
                self.con, NOW, self.metadata, technical_planning=True
            )
        self.assertEqual(selected, 1)
        notional, target = self.con.execute(
            "SELECT notional,target_price FROM paper_agent_positions"
        ).fetchone()
        self.assertAlmostEqual(notional, 55.0)
        self.assertAlmostEqual(target, 103.4)
        thesis = json.loads(self.con.execute(
            "SELECT thesis_json FROM paper_agent_decision_theses"
        ).fetchone()[0])
        self.assertEqual(
            thesis["technical_trade_plan"]["timeframes_used"],
            ["1m", "5m", "15m", "1h", "4h"],
        )
        self.assertEqual(
            thesis["execution_risk"]["stop_basis"],
            "MULTITIMEFRAME_TECHNICAL_STRUCTURE",
        )
        self.assertAlmostEqual(
            thesis["execution_risk"]["planned_stop_price"], 98.5
        )
        self.assertIn(
            "MULTITIMEFRAME_TECHNICAL_EXECUTION_PLAN",
            thesis["capabilities_under_test"],
        )

    def test_pending_entry_waits_for_frozen_technical_entry_zone(self):
        candidate = self.candidate()
        plan = self.technical_plan(quality=1.0)
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }), patch.object(
            agent.technical_trade_plan, "build_for_candidate", return_value=plan
        ):
            self.assertEqual(
                agent.select_new_positions(
                    self.con, NOW, self.metadata, technical_planning=True
                ),
                1,
            )

        def live(price, seconds):
            at = NOW + timedelta(seconds=seconds)
            ms = int(at.timestamp() * 1000)
            return LiveState(
                "BTCUSDT", market_type="SPOT", last_price=price,
                best_bid=price - 0.01, best_ask=price + 0.01,
                visible_quote_volume=1_000_000,
                bid_quote_volume=1_000_000, ask_quote_volume=1_000_000,
                quote_source="TEST_BINANCE",
                market_timestamp_ms=ms, quote_timestamp_ms=ms,
                liquidity_timestamp_ms=ms,
            )

        filled, cancelled = agent.fill_pending_from_live_state(
            self.con, NOW + timedelta(seconds=1), live(101.0, 1), self.metadata
        )
        self.assertEqual((filled, cancelled), (0, 0))
        self.assertEqual(
            self.con.execute(
                "SELECT status FROM paper_agent_positions"
            ).fetchone()[0],
            "PENDING_ENTRY",
        )
        filled, cancelled = agent.fill_pending_from_live_state(
            self.con, NOW + timedelta(seconds=2), live(100.0, 2), self.metadata
        )
        self.assertEqual((filled, cancelled), (1, 0))
        self.assertEqual(
            self.con.execute(
                "SELECT status FROM paper_agent_positions"
            ).fetchone()[0],
            "OPEN",
        )

    def test_shadow_technical_context_is_frozen_but_not_a_decision_input(self):
        context = {
            "version": agent.realtime_technical_context.VERSION,
            "symbol": "BTCUSDT",
            "generated_at": (NOW - timedelta(minutes=5)).isoformat(),
            "as_of": (NOW - timedelta(hours=1)).isoformat(),
            "market_scope": "TEST",
            "observation_only": True,
            "real_order_allowed": False,
            "timeframes": ["1h", "4h"],
            "advanced_1h": {"rsi14": 0.2},
            "structure_4h": {"trend": "BULLISH"},
            "summary": {"alignment": "ALIGNED_BULLISH"},
            "authority": {
                "direction_input": False,
                "ranking_input": False,
                "confidence_input": False,
                "sizing_input": False,
                "stop_input": False,
                "purpose": "FORWARD_OBSERVATION_AND_LATER_CHALLENGER_EVIDENCE",
            },
        }
        agent.realtime_technical_context.save_latest(self.con, context, 0.5)
        self.con.commit()
        candidate = self.candidate()
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }):
            self.assertEqual(agent.select_new_positions(self.con, NOW), 1)

        artifact_hash = self.con.execute(
            "SELECT artifact_hash FROM paper_agent_positions"
        ).fetchone()[0]
        self.assertEqual(artifact_hash, candidate["evidence_sha256"])
        thesis = json.loads(self.con.execute(
            "SELECT thesis_json FROM paper_agent_decision_theses"
        ).fetchone()[0])
        self.assertEqual(
            thesis["technical_context_observed"]["summary"]["alignment"],
            "ALIGNED_BULLISH",
        )
        self.assertIn(
            "TECHNICAL_CONTEXT_SHADOW",
            thesis["capabilities_observed_but_not_decision_inputs"],
        )
        self.assertNotIn(
            "TECHNICAL_CONTEXT_SHADOW",
            thesis["capabilities_under_test"],
        )

    def test_missing_stop_reference_is_rejected_before_position_write(self):
        candidate = self.candidate()
        candidate.pop("stop_reference_pct")
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }):
            selected = agent.select_new_positions(self.con, NOW)
        self.assertEqual(selected, 0)
        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM paper_agent_positions").fetchone()[0], 0,
        )
        reasons = json.loads(self.con.execute(
            "SELECT reason_counts_json FROM paper_behavior_selection_runs"
        ).fetchone()[0])
        self.assertEqual(reasons, {"STOP_REFERENCE_MISSING_OR_INVALID": 1})

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
        }), patch.object(agent.risk_tiering, "tier_for", return_value=fake_tier), \
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
        ), patch.object(agent.risk_tiering, "tier_for", return_value=fake_tier):
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

    def test_portfolio_uses_fresh_mark_for_equity_and_gross_exposure(self):
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
