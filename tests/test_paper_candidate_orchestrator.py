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
        self.assertAlmostEqual(sized.notional, 50.0)
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
        seen = set()
        with patch.object(technical_trade_plan, "build_for_candidate", return_value=plan()) as build, patch.object(
            realtime_technical_context, "load_latest", return_value=None
        ):
            for index in range(10):
                build.reset_mock()
                result = orchestrator.orchestrate(object(), NOW + timedelta(seconds=index * 5),
                                                   behavior_result=behavior)
                self.assertEqual(build.call_count, 12)
                self.assertEqual(result["capacity"]["opportunity_candidate_count"], 40)
                self.assertEqual(result["rejections"]["DEEP_ANALYSIS_CAPACITY_DEFERRED"], 28)
                self.assertEqual(len(behavior["candidates"]), 40)
                seen.update(row.candidate["decision_id"] for row in result["ready"])
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
