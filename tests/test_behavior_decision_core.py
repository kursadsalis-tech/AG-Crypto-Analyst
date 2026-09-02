import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from paper_trading import behavior_decision_core as core
from database.runtime_store import REMOTE_RUNTIME_TABLES


NOW = datetime(2026, 9, 4, 12, 2, tzinfo=timezone.utc)


class BehaviorDecisionCoreTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript("""
            CREATE TABLE behavior_event_forward_outcomes(
                event_minute_end_timestamp_ms INTEGER,independence_unit TEXT,
                instrument TEXT,kind TEXT,
                horizon_minutes INTEGER,return_from_pre_event_pct REAL,
                book_imbalance_20_mean REAL,taker_imbalance_snapshot_mean REAL,
                spread_bps_mean REAL,outcome_available_at TEXT
                ,event_id TEXT
            );
            CREATE TABLE market_behavior_minute_features(
                instrument TEXT,minute_end_timestamp_ms INTEGER,
                eligible_for_learning INTEGER,book_imbalance_20_mean REAL,
                taker_imbalance_snapshot_mean REAL,spread_bps_mean REAL,
                mid_price_last REAL,open_interest_usd_first REAL,
                open_interest_usd_change REAL,funding_rate_last REAL,
                long_liquidation_notional_usd_estimate REAL,
                short_liquidation_notional_usd_estimate REAL,
                liquidation_event_count INTEGER,quality_gates_json TEXT
            );
            CREATE TABLE causal_minute_external_evidence(
                minute_end_timestamp_ms INTEGER,kind TEXT,symbols_json TEXT,
                event_id TEXT,available_at TEXT,direction TEXT
            );
            CREATE TABLE risk_policy_snapshots(
                id INTEGER PRIMARY KEY,created_at TEXT,new_positions_allowed INTEGER
            );
            CREATE TABLE asset_risk_snapshots(
                policy_snapshot_id INTEGER,symbol TEXT,new_position_allowed INTEGER,
                max_position_pct REAL,price REAL,as_of TEXT
            );
            INSERT INTO risk_policy_snapshots VALUES(1,'2026-09-04T12:00:00+00:00',1);
            INSERT INTO asset_risk_snapshots VALUES(
                1,'BTCUSDT',1,10,100,'2026-09-04T11:59:59+00:00'
            );
        """)

    def add_history(self, count=30, days=3):
        for index in range(count):
            stamp = NOW - timedelta(days=index % days + 1, minutes=index)
            self.con.execute(
                "INSERT INTO behavior_event_forward_outcomes VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (int(stamp.timestamp() * 1000), f"event:{index}",
                 "BTC-USDT-SWAP", "NEWS", 240, 1.0, .3, .4, 1.0,
                 (stamp + timedelta(hours=4)).isoformat(), f"history:{index}"),
            )

    def add_current(self):
        minute = int((NOW - timedelta(minutes=1)).timestamp() * 1000)
        self.con.execute(
            "INSERT INTO market_behavior_minute_features VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("BTC-USDT-SWAP", minute, 1, .25, .35, 1.0, 100.0, 1_000_000,
             25_000, .0001, 0, 1000, 1, json.dumps({"all": True})),
        )
        self.con.execute(
            "INSERT INTO causal_minute_external_evidence VALUES(?,?,?,?,?,?)",
            (minute, "NEWS", json.dumps(["BTC"]), "news:1",
             (NOW - timedelta(minutes=1)).isoformat(), "positive"),
        )

    def test_promotion_thresholds_do_not_block_small_paper_learning_experiment(self):
        self.add_history(days=1)
        self.add_current()
        result = core.evaluate(self.con, NOW)
        self.assertEqual(result["status"], "ACTIONABLE")
        self.assertEqual(result["candidates"][0]["experiment_tier"], "EXPLORATORY_LEARNING")
        pattern = result["review"]["patterns"][0]
        self.assertTrue(pattern["exploration_eligible"])
        self.assertFalse(pattern["promotion_ready"])
        self.assertFalse(result["review"]["unified_forecast_used"])

    def test_news_hypothesis_can_stand_without_current_flow_confluence(self):
        self.add_history(count=5, days=1)
        self.add_current()
        self.con.execute(
            "UPDATE market_behavior_minute_features SET taker_imbalance_snapshot_mean=-.35"
        )
        result = core.evaluate(self.con, NOW)
        event_only = [candidate for candidate in result["candidates"]
                      if candidate["evidence"]["hypothesis_scope"] == "EXTERNAL_EVENT_ONLY"]
        self.assertTrue(event_only)
        self.assertEqual(event_only[0]["evidence"]["primary_signal_source"], "NEWS")
        self.assertNotIn("ORDER_BOOK", event_only[0]["evidence"]["capabilities_used"])

    def test_price_action_can_open_small_experiment_without_news(self):
        self.add_history(count=5, days=1)
        self.add_current()
        self.con.execute(
            "ALTER TABLE behavior_event_forward_outcomes ADD COLUMN pre_event_mid_price REAL"
        )
        self.con.execute(
            "ALTER TABLE behavior_event_forward_outcomes ADD COLUMN event_minute_mid_price REAL"
        )
        self.con.execute(
            "ALTER TABLE market_behavior_minute_features ADD COLUMN mid_price_first REAL"
        )
        self.con.execute("""UPDATE behavior_event_forward_outcomes SET
            pre_event_mid_price=100,event_minute_mid_price=100.5""")
        self.con.execute(
            "UPDATE market_behavior_minute_features SET mid_price_first=99.5"
        )
        self.con.execute("DELETE FROM causal_minute_external_evidence")
        result = core.evaluate(self.con, NOW)
        chart = [candidate for candidate in result["candidates"]
                 if candidate["evidence"]["hypothesis_scope"] == "PRICE_ACTION_ONLY"]
        self.assertTrue(chart)
        self.assertEqual(chart[0]["evidence"]["capabilities_used"],
                         ["PRICE_ACTION", "SPREAD_COST", "RISK_GATE"])

    def test_cost_after_multiday_pattern_creates_behavior_candidate(self):
        self.add_history(days=3)
        self.add_current()
        result = core.evaluate(self.con, NOW)
        self.assertEqual(result["status"], "ACTIONABLE")
        candidate = result["candidates"][0]
        self.assertEqual(candidate["direction"], "LONG")
        self.assertEqual(candidate["experiment_tier"], "PROMOTION_READY")
        self.assertEqual(candidate["decision_source"], "MARKET_BEHAVIOR_CORE")
        self.assertFalse(candidate["evidence"]["unified_forecast_used"])
        self.assertIn("ORDER_BOOK", candidate["evidence"]["capabilities_used"])
        self.assertIn("LIQUIDATIONS", candidate["evidence"]["capabilities_used"])

    def test_newer_eventless_minute_does_not_hide_fresh_event_minute(self):
        self.add_history(days=1)
        self.add_current()
        newer = int((NOW - timedelta(seconds=20)).timestamp() * 1000)
        self.con.execute(
            "INSERT INTO market_behavior_minute_features VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("BTC-USDT-SWAP", newer, 1, .25, .35, 1.0, 100.0, 1_000_000,
             25_000, .0001, 0, 1000, 1, json.dumps({"all": True})),
        )
        result = core.evaluate(self.con, NOW)
        self.assertEqual(result["status"], "ACTIONABLE")
        self.assertEqual(
            result["candidates"][0]["evidence"]["evidence_minute_end_ms"],
            int((NOW - timedelta(minutes=1)).timestamp() * 1000),
        )

    def test_endogenous_market_structure_can_trade_without_external_event(self):
        self.add_history(days=1)
        self.add_current()
        self.con.execute(
            "UPDATE behavior_event_forward_outcomes SET kind='MARKET_STRUCTURE'"
        )
        self.con.execute("DELETE FROM causal_minute_external_evidence")
        result = core.evaluate(self.con, NOW)
        self.assertEqual(result["status"], "ACTIONABLE")
        self.assertEqual(result["candidates"][0]["evidence"]["event"]["kind"],
                         "MARKET_STRUCTURE")

    def test_new_risk_snapshot_reuses_immutable_decision_thesis(self):
        self.add_history(days=1)
        self.add_current()
        first = core.evaluate(self.con, NOW)["candidates"][0]
        self.con.execute(
            "UPDATE asset_risk_snapshots SET max_position_pct=0.25,as_of=?",
            ((NOW + timedelta(seconds=10)).isoformat(),),
        )
        second = core.evaluate(self.con, NOW)["candidates"][0]
        self.assertEqual(second["decision_id"], first["decision_id"])
        self.assertEqual(second["evidence_sha256"], first["evidence_sha256"])
        self.assertEqual(second["risk_cap_pct"], first["risk_cap_pct"])

    def test_missing_or_blocked_risk_is_fail_closed(self):
        self.add_history(days=3)
        self.add_current()
        self.con.execute("UPDATE asset_risk_snapshots SET new_position_allowed=0")
        result = core.evaluate(self.con, NOW)
        self.assertEqual(result["candidates"], [])
        missed = self.con.execute("""SELECT rejection_reason,review_status
            FROM paper_behavior_missed_opportunities""").fetchone()
        self.assertEqual(missed, ("RISK_VETO", "AWAITING_OUTCOME"))

    def test_missing_dynamic_risk_is_recorded_for_counterfactual_learning(self):
        self.add_history(days=1)
        self.add_current()
        self.con.execute("DELETE FROM asset_risk_snapshots")
        result = core.evaluate(self.con, NOW)
        self.assertEqual(result["candidates"], [])
        missed = self.con.execute("""SELECT symbol,rejection_reason,real_order_allowed
            FROM paper_behavior_missed_opportunities""").fetchone()
        self.assertEqual(missed, ("BTCUSDT", "RISK_COVERAGE_MISSING", 0))

    def test_missed_opportunity_learns_cost_after_counterfactual(self):
        self.add_history(days=1)
        self.add_current()
        self.con.execute("DELETE FROM asset_risk_snapshots")
        core.evaluate(self.con, NOW)
        minute = int((NOW - timedelta(minutes=1)).timestamp() * 1000)
        self.con.execute(
            "INSERT INTO behavior_event_forward_outcomes VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (minute, "news:1", "BTC-USDT-SWAP", "NEWS", 240, 1.0,
             .25, .35, 1.0, (NOW + timedelta(hours=4)).isoformat(), "news:1"),
        )
        self.assertEqual(core.settle_missed_opportunities(self.con), 1)
        settled = self.con.execute("""SELECT review_status,counterfactual_net_return_pct
            FROM paper_behavior_missed_opportunities""").fetchone()
        self.assertEqual(settled[0], "COUNTERFACTUAL_SETTLED_NOT_TRADED")
        self.assertAlmostEqual(settled[1], 0.74)

    def test_disagreeing_flow_does_not_block_independent_news_experiment(self):
        self.add_history(days=3)
        self.add_current()
        self.con.execute(
            "UPDATE market_behavior_minute_features SET taker_imbalance_snapshot_mean=-.4"
        )
        result = core.evaluate(self.con, NOW)
        self.assertTrue(result["candidates"])
        self.assertEqual(
            {row["evidence"]["hypothesis_scope"] for row in result["candidates"]},
            {"EXTERNAL_EVENT_ONLY"},
        )

    def test_learned_opposing_flow_can_be_traded_as_absorption_pattern(self):
        self.add_history(days=1)
        self.add_current()
        self.con.execute(
            "UPDATE behavior_event_forward_outcomes SET taker_imbalance_snapshot_mean=-.4"
        )
        self.con.execute(
            "UPDATE market_behavior_minute_features SET taker_imbalance_snapshot_mean=-.4"
        )
        result = core.evaluate(self.con, NOW)
        self.assertEqual(result["status"], "ACTIONABLE")
        self.assertEqual(result["candidates"][0]["evidence"]["learned_pattern"]["flow_pattern"],
                         "BOOK_POS_TAKER_NEG")

    def test_future_available_outcome_cannot_train_policy(self):
        self.add_history(days=3)
        self.add_current()
        self.con.execute(
            "UPDATE behavior_event_forward_outcomes SET outcome_available_at=?",
            ((NOW + timedelta(hours=1)).isoformat(),),
        )
        result = core.evaluate(self.con, NOW)
        self.assertEqual(result["candidates"], [])

    def test_fewer_than_five_closed_samples_cannot_open_experiment(self):
        self.add_history(count=4, days=1)
        self.add_current()
        result = core.evaluate(self.con, NOW)
        self.assertEqual(result["candidates"], [])

    def test_non_positive_cost_after_hypothesis_cannot_open_experiment(self):
        self.add_history(count=5, days=1)
        self.add_current()
        self.con.execute(
            "UPDATE behavior_event_forward_outcomes SET return_from_pre_event_pct=0"
        )
        result = core.evaluate(self.con, NOW)
        self.assertEqual(result["candidates"], [])

    def test_event_available_after_closed_minute_is_rejected(self):
        self.add_history(days=3)
        self.add_current()
        self.con.execute(
            "UPDATE causal_minute_external_evidence SET available_at=?",
            ((NOW + timedelta(minutes=1)).isoformat(),),
        )
        result = core.evaluate(self.con, NOW)
        self.assertEqual(result["candidates"], [])

    def test_behavior_decision_memory_is_local_only(self):
        self.assertNotIn("paper_behavior_policy_reviews", REMOTE_RUNTIME_TABLES)
        self.assertNotIn("paper_behavior_decisions", REMOTE_RUNTIME_TABLES)
        self.assertNotIn("paper_behavior_missed_opportunities", REMOTE_RUNTIME_TABLES)


if __name__ == "__main__":
    unittest.main()
