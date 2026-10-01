import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from paper_trading import crypto_profit_agent as agent
from risk_manager import risk_manager as manager
from tests import test_behavior_paper_integration as integration_fixtures

NOW = integration_fixtures.NOW
quote = integration_fixtures.quote


class GlobalPolicyEntryLimitTests(unittest.TestCase):
    setUp = integration_fixtures.BehaviorPaperIntegrationTests.setUp
    technical_plan = integration_fixtures.BehaviorPaperIntegrationTests.technical_plan
    candidate = integration_fixtures.BehaviorPaperIntegrationTests.candidate
    live_state = integration_fixtures.BehaviorPaperIntegrationTests.live_state
    add_open_mark_position = integration_fixtures.BehaviorPaperIntegrationTests.add_open_mark_position

    def policy(self, **changes):
        manager.ensure_tables(self.con)
        assets = [manager.classify_asset_risk({
            "symbol": symbol, "as_of": NOW.isoformat(), "price": 100.0,
            "return_24h_pct": 0.5, "range_24h_pct": 2.0,
            "hourly_volatility_pct": 0.3, "drawdown_7d_pct": -1.0,
            "quote_volume_24h": 100_000_000,
        }) for symbol in (*manager.SYMBOLS, "NEWUSDT")]
        policy = manager.build_global_policy(assets, {}, NOW.isoformat())
        policy.update(changes)
        return manager.save_snapshot(self.con, policy, assets, {})

    def select(self, *candidates, **kwargs):
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": list(candidates), "review": {},
        }):
            return agent.select_new_positions(self.con, NOW, exchange_metadata=self.metadata, **kwargs)

    def pending_candidate(self, symbol="BTCUSDT"):
        self.policy()
        candidate = self.candidate()
        candidate["symbol"] = symbol
        candidate["instrument"] = symbol[:-4]+"-USDT-SWAP"
        self.assertEqual(self.select(candidate), 1)
        return candidate

    def fill(self, symbol="BTCUSDT", **kwargs):
        return agent.fill_pending_from_live_state(self.con, NOW,
            self.live_state(symbol, NOW), self.metadata, **kwargs)

    def reason(self):
        return self.con.execute("SELECT cancel_reason FROM paper_agent_positions WHERE status='CANCELLED'").fetchone()[0]

    def audit(self):
        return json.loads(self.con.execute("SELECT reason_counts_json FROM paper_behavior_selection_runs ORDER BY run_id DESC LIMIT 1").fetchone()[0])

    def test_realtime_missing_schema_is_not_an_offline_compatibility_allowance(self):
        self.assertEqual(self.select(self.candidate(), market_snapshots={}), 0)
        self.assertEqual(self.audit()["ENTRY_RISK_POLICY_UNAVAILABLE"], 1)

    def test_deployed_flag_rejects_incomplete_policy_schema_without_quote_map(self):
        self.con.execute("CREATE TABLE risk_policy_snapshots(id INTEGER PRIMARY KEY)")
        with patch.dict("os.environ", {"AGCA_BEHAVIOR_PATTERN_CACHE_REQUIRED": "1"}):
            self.assertEqual(self.select(self.candidate()), 0)
        self.assertEqual(self.audit()["ENTRY_RISK_POLICY_UNAVAILABLE"], 1)

    def test_actual_live_fill_rejects_schema_disappearing_after_selection(self):
        candidate = self.candidate()
        self.assertEqual(self.select(candidate), 1)
        self.assertEqual(self.fill(market_snapshots={}), (0, 1))
        self.assertEqual(self.reason(), "ENTRY_RISK_POLICY_UNAVAILABLE")

    def test_global_new_position_count_reduces_actual_selection(self):
        self.policy(max_new_positions=1)
        first = self.candidate()
        second = dict(first, symbol="ETHUSDT", decision_id=8)
        self.assertEqual(self.select(first, second), 1)
        self.assertEqual(self.audit()["PER_CYCLE_NEW_POSITION_CAP_REACHED"], 1)

    def test_global_total_cap_below_user_floor_blocks_selection(self):
        self.policy(max_total_exposure_pct=.5)
        self.assertEqual(self.select(self.candidate()), 0)
        self.assertEqual(self.audit()["PORTFOLIO_EXPOSURE_LIMIT"], 1)

    def test_global_alt_cap_consumes_fresh_marked_exposure(self):
        self.policy(max_alt_exposure_pct=2.5)
        self.add_open_mark_position()
        self.con.execute("UPDATE paper_agent_portfolio SET cash=cash-100")
        candidate = dict(self.candidate(), symbol="NEWUSDT")
        snapshot = {("MARKUSDT", "SPOT"): quote(NOW, 200, "SPOT")}
        self.assertEqual(self.select(candidate, market_snapshots=snapshot), 0)
        self.assertEqual(self.audit()["GLOBAL_ALT_EXPOSURE_LIMIT"], 1)

    def test_stale_canonical_policy_blocks_selection_with_durable_reason(self):
        self.policy(created_at=(NOW-timedelta(hours=1)).isoformat())
        self.assertEqual(self.select(self.candidate()), 0)
        self.assertEqual(self.audit()["ENTRY_RISK_POLICY_UNAVAILABLE"], 1)

    def test_actual_fill_revalidates_tightened_global_total_cap(self):
        self.pending_candidate()
        self.con.execute("UPDATE risk_policy_snapshots SET max_total_exposure_pct=.5")
        self.assertEqual(self.fill(market_snapshots={}), (0, 1))
        self.assertEqual(self.reason(), "ENTRY_GLOBAL_TOTAL_EXPOSURE_LIMIT")
        self.assertEqual(self.con.execute("SELECT cash FROM paper_agent_portfolio").fetchone()[0], 10000)

    def test_actual_fill_revalidates_tightened_alt_cap(self):
        self.pending_candidate("NEWUSDT")
        self.con.execute("UPDATE risk_policy_snapshots SET max_alt_exposure_pct=.5")
        self.assertEqual(self.fill("NEWUSDT", market_snapshots={}), (0, 1))
        self.assertEqual(self.reason(), "ENTRY_GLOBAL_ALT_EXPOSURE_LIMIT")

    def test_actual_fill_revalidates_tightened_asset_cap(self):
        self.pending_candidate()
        self.con.execute("UPDATE asset_risk_snapshots SET max_position_pct=.5 WHERE symbol='BTCUSDT'")
        self.assertEqual(self.fill(market_snapshots={}), (0, 1))
        self.assertEqual(self.reason(), "ENTRY_CURRENT_ASSET_EXPOSURE_LIMIT")

    def test_actual_fill_revalidates_tightened_stop_risk_budget(self):
        self.pending_candidate()
        self.con.execute("UPDATE risk_policy_snapshots SET risk_per_trade_pct=.005")
        self.assertEqual(self.fill(market_snapshots={}), (0, 1))
        self.assertEqual(self.reason(), "ENTRY_CURRENT_STOP_RISK_LIMIT")

    def test_actual_fill_rejects_missing_or_vetoed_candidate_risk(self):
        self.pending_candidate()
        self.con.execute("DELETE FROM asset_risk_snapshots WHERE symbol='BTCUSDT'")
        self.assertEqual(self.fill(market_snapshots={}), (0, 1))
        self.assertEqual(self.reason(), "ENTRY_ASSET_RISK_UNAVAILABLE")

    def test_actual_fill_uses_marked_exposure_after_price_move(self):
        self.pending_candidate()
        self.add_open_mark_position()
        self.con.execute("UPDATE paper_agent_portfolio SET cash=cash-100")
        self.con.execute("UPDATE risk_policy_snapshots SET max_total_exposure_pct=2.5")
        snapshots = {("MARKUSDT", "SPOT"): quote(NOW, 200, "SPOT")}
        self.assertEqual(self.fill(market_snapshots=snapshots), (0, 1))
        self.assertEqual(self.reason(), "ENTRY_GLOBAL_TOTAL_EXPOSURE_LIMIT")

    def test_actual_fill_rejects_missing_mark_when_other_position_open(self):
        self.pending_candidate()
        self.add_open_mark_position()
        self.assertEqual(self.fill(market_snapshots={}), (0, 1))
        self.assertEqual(self.reason(), "ENTRY_PORTFOLIO_VALUATION_UNAVAILABLE")

    def test_actual_fill_counts_other_pending_reservations_once(self):
        self.pending_candidate()
        second = dict(self.candidate(), symbol="ETHUSDT", decision_id=8)
        self.assertEqual(self.select(second), 1)
        self.con.execute("UPDATE risk_policy_snapshots SET max_total_exposure_pct=1.5")
        self.assertEqual(self.fill(market_snapshots={}), (0, 1))
        self.assertEqual(self.reason(), "ENTRY_GLOBAL_TOTAL_EXPOSURE_LIMIT")

    def test_valid_fresh_policy_allows_fill_without_double_counting_own_intent(self):
        self.pending_candidate()
        self.con.execute("UPDATE risk_policy_snapshots SET max_total_exposure_pct=1.1")
        self.assertEqual(self.fill(market_snapshots={}), (1, 0))
        self.assertEqual(self.con.execute("SELECT status FROM paper_agent_positions").fetchone()[0], "OPEN")


if __name__ == "__main__":
    unittest.main()
