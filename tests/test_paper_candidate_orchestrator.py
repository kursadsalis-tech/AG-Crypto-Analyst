from copy import deepcopy
import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from analysis import realtime_technical_context, technical_trade_plan
from decision_engine import paper_candidate_orchestrator as orchestrator


NOW = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)


def candidate():
    return {
        "decision_id": 77,
        "decision_source": "MARKET_BEHAVIOR_CORE",
        "symbol": "BTCUSDT",
        "instrument": "BTC-USDT",
        "horizon": 4,
        "direction": "LONG",
        "confidence": 0.60,
        "expected_net_edge_pct": 0.40,
        "execution_size_multiplier": 1.0,
        "experiment_tier": "EXPLORATORY_LEARNING",
        "current_price": 100.0,
        "target_price": 100.4,
        "risk_cap_pct": 10.0,
        "risk_per_trade_pct": 1.0,
        "evidence_sha256": "a" * 64,
        "evidence": {
            "primary_signal_source": "MARKET_BEHAVIOR",
            "directional_signals": [
                {"source": "MARKET_BEHAVIOR", "direction": "LONG"}
            ],
            "event": {
                "kind": "MARKET_STRUCTURE",
                "reported_direction": "OBSERVED",
            },
            "learned_pattern": {},
            "execution_calibration": {},
        },
    }


def plan(*, risk_compatible=True):
    return {
        "version": "TEST",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "execution_market_type": "SPOT",
        "quality_multiplier": 1.0,
        "technical_score": 0.25,
        "timeframes_used": ["1m", "5m", "15m"],
        "risk": {
            "risk_compatible": risk_compatible,
            "stop_distance_pct": 2.0,
            "invalidation_price": 98.0,
        },
        "entry": {"anchor": 100.0, "low": 99.8, "high": 100.2},
        "targets": {"tp1_price": 102.0, "tp2_price": 104.0},
    }


class SharedPaperCandidateOrchestratorTests(unittest.TestCase):
    def test_negative_or_invalid_calibrated_edge_never_reaches_planning(self):
        for edge in (-0.0372, 0.0, float("nan"), float("inf"), None):
            for field in ("expected", "calibrated"):
                with self.subTest(edge=edge, field=field):
                    row = candidate()
                    if field == "expected":
                        row["expected_net_edge_pct"] = edge
                    else:
                        row["evidence"]["execution_calibration"]["calibrated_net_edge_pct"] = edge
                    with patch.object(technical_trade_plan, "build_for_candidate") as build:
                        result = orchestrator.orchestrate(object(), NOW,
                            behavior_result={"status": "ACTIONABLE", "candidates": [row]})
                    build.assert_not_called()
                    self.assertEqual(result["ready"], [])
                    reason = "NEGATIVE_EDGE_VETO" if edge in (-0.0372, 0.0) else "INVALID_EDGE_EVIDENCE"
                    self.assertEqual(result["rejections"], {reason: 1})

    def test_both_directions_reach_paper_execution_geometry(self):
        for direction, market in (("LONG", "SPOT"), ("SHORT", "USD_M_PERPETUAL")):
            with self.subTest(direction=direction):
                row = candidate()
                row["direction"] = direction
                row["evidence"]["directional_signals"][0]["direction"] = direction
                row["evidence"]["execution_calibration"]["calibrated_net_edge_pct"] = 0.25
                frozen = {**plan(), "direction": direction, "execution_market_type": market}
                with patch.object(technical_trade_plan, "build_for_candidate", return_value=frozen), patch.object(
                    realtime_technical_context, "load_latest", return_value=None
                ):
                    result = orchestrator.orchestrate(object(), NOW,
                        behavior_result={"status": "ACTIONABLE", "candidates": [row]})
                self.assertEqual(len(result["ready"]), 1)
                self.assertEqual(result["ready"][0].risk_tier.market_type, market)
                self.assertEqual(result["ready"][0].candidate["direction"], direction)

    def test_combines_behavior_technical_risk_and_cost_without_position_authority(self):
        behavior = {
            "status": "ACTIONABLE",
            "candidates": [candidate()],
            "review": {"real_orders": False},
        }
        with patch.object(
            technical_trade_plan, "build_for_candidate",
            return_value=plan(),
        ), patch.object(
            realtime_technical_context, "load_latest",
            return_value={"version": "TEST_CONTEXT"},
        ):
            result = orchestrator.orchestrate(
                object(), NOW, behavior_result=behavior
            )

        self.assertEqual(result["rejections"], {})
        self.assertEqual(result["technical_plan_errors"], [])
        self.assertEqual(len(result["ready"]), 1)

        ready = result["ready"][0]
        self.assertEqual(ready.candidate["technical_plan"]["version"], "TEST")
        self.assertEqual(
            ready.candidate["technical_context"]["version"], "TEST_CONTEXT"
        )
        self.assertEqual(ready.raw_stop_pct, 2.0)
        self.assertEqual(ready.effective_stop_pct, 2.0)
        self.assertEqual(ready.risk_tier.market_type, "SPOT")
        self.assertEqual(ready.risk_tier.leverage, 1.0)

        sized = orchestrator.size_candidate(
            ready, equity=10_000.0,
            exposure_cap=6_000.0, available_cash=10_000.0,
        )
        self.assertAlmostEqual(sized.notional, 100.0)
        self.assertGreater(sized.expected_profit_usdt, 0.0)
        self.assertGreater(sized.expected_profit_per_risk, 0.0)

        frozen = orchestrator.execution_risk(ready, sized.notional)
        self.assertEqual(frozen["gross_exposure"], sized.notional)
        self.assertEqual(frozen["planned_stop_distance_pct"], 2.0)
        self.assertEqual(frozen["market_type"], "SPOT")
        self.assertFalse(behavior["review"]["real_orders"])

    def test_bounded_planning_defers_without_shrinking_candidate_universe(self):
        candidates = [{**candidate(), "decision_id": i, "symbol": f"COIN{i}USDT"}
                      for i in range(40)]
        behavior = {"status": "ACTIONABLE", "candidates": candidates}
        original_candidates = deepcopy(candidates)
        seen = set()
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        from paper_trading.crypto_profit_agent import ensure_tables
        ensure_tables(con)
        # Eight completed scans cover all 32 tail members at four per scan.
        # These times hit only even five-second buckets: the old scheduler
        # repeatedly visits half the tail, including with irregular durations.
        elapsed_seconds = (0, 10, 20, 31, 42, 51, 62, 80)
        with patch.object(technical_trade_plan, "build_for_candidate", return_value=plan()) as build, patch.object(
            realtime_technical_context, "load_latest", return_value=None
        ):
            for scan_index, seconds in enumerate(elapsed_seconds):
                build.reset_mock()
                result = orchestrator.orchestrate(con, NOW + timedelta(seconds=seconds),
                                                   behavior_result=behavior)
                self.assertEqual(build.call_count, 12)
                self.assertEqual(result["capacity"]["opportunity_candidate_count"], 40)
                self.assertEqual(result["rejections"]["DEEP_ANALYSIS_CAPACITY_DEFERRED"], 28)
                self.assertEqual(len(behavior["candidates"]), 40)
                planned = [row.candidate["decision_id"] for row in result["ready"]]
                seen.update(planned)
                # An aborted scan must not mutate the input or consume a turn.
                with patch.object(realtime_technical_context, "load_latest",
                                  side_effect=RuntimeError("scan aborted")):
                    with self.assertRaisesRegex(RuntimeError, "scan aborted"):
                        orchestrator.orchestrate(con, NOW + timedelta(seconds=seconds + 1),
                                                 behavior_result=behavior)
                retry = orchestrator.orchestrate(
                    con, NOW + timedelta(seconds=seconds + 2), behavior_result=behavior)
                self.assertEqual([row.candidate["decision_id"] for row in retry["ready"]], planned)
                self.assertEqual(behavior["candidates"], original_candidates)
                # Persist the same completion record as the canonical caller.
                con.execute("""INSERT INTO paper_behavior_selection_runs(
                    created_at, core_status, candidate_count, selected_count,
                    active_position_count, capacity, available_exposure,
                    reason_counts_json) VALUES(?, 'ACTIONABLE', 40, 0, 0, 12, 0, ?)""",
                    ((NOW + timedelta(seconds=seconds + 2)).isoformat(),
                     json.dumps(result["rejections"])))
                # One or three pre-orchestrator completions between real scans
                # make MAX(run_id) advance by even strides and starve the tail.
                for extra in range(1 if scan_index % 2 == 0 else 3):
                    reasons = ({"PORTFOLIO_VALUATION_STALE_OR_MISSING": 1} if extra == 0 else
                               {"DEEP_ANALYSIS_CAPACITY_DEFERRED": 0})
                    con.execute("""INSERT INTO paper_behavior_selection_runs(
                        created_at, core_status, candidate_count, selected_count,
                        active_position_count, capacity, available_exposure,
                        reason_counts_json) VALUES(?, 'BLOCKED', 0, 0, 0, 12, 0, ?)""",
                        ((NOW + timedelta(seconds=seconds + 3 + extra)).isoformat(),
                         json.dumps(reasons)))
                con.commit()
            self.assertEqual(seen, set(range(40)))
            build.reset_mock()
            zero = orchestrator.orchestrate(object(), NOW, behavior_result=behavior, deep_analysis_slots=0)
            build.assert_not_called()
            self.assertEqual(zero["capacity"]["opportunity_candidate_count"], 40)
            self.assertEqual(zero["capacity"]["deep_analysis_deferred_count"], 40)

    def test_technical_plan_failure_is_visible_and_candidate_fails_closed(self):
        behavior = {
            "status": "ACTIONABLE",
            "candidates": [candidate()],
            "review": {"real_orders": False},
        }
        with patch.object(
            technical_trade_plan, "build_for_candidate",
            side_effect=ValueError("no contiguous bars"),
        ):
            result = orchestrator.orchestrate(
                object(), NOW, behavior_result=behavior
            )

        self.assertEqual(result["ready"], [])
        self.assertEqual(
            result["rejections"], {"TECHNICAL_PLAN_UNAVAILABLE": 1}
        )
        self.assertEqual(
            result["technical_plan_errors"][0]["error_type"], "ValueError"
        )

    def test_incompatible_technical_risk_is_not_sized(self):
        behavior = {
            "status": "ACTIONABLE",
            "candidates": [candidate()],
            "review": {"real_orders": False},
        }
        with patch.object(
            technical_trade_plan, "build_for_candidate",
            return_value=plan(risk_compatible=False),
        ):
            result = orchestrator.orchestrate(
                object(), NOW, behavior_result=behavior
            )

        self.assertEqual(result["ready"], [])
        self.assertEqual(
            result["rejections"],
            {"TECHNICAL_RISK_GEOMETRY_INFEASIBLE": 1},
        )


if __name__ == "__main__":
    unittest.main()
