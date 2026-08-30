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
                "INSERT INTO behavior_event_forward_outcomes VALUES(?,?,?,?,?,?,?,?,?,?)",
                (int(stamp.timestamp() * 1000), f"event:{index}",
                 "BTC-USDT-SWAP", "NEWS", 240, 1.0, .3, .4, 1.0,
                 (stamp + timedelta(hours=4)).isoformat()),
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

    def test_insufficient_independent_days_cannot_trade(self):
        self.add_history(days=1)
        self.add_current()
        result = core.evaluate(self.con, NOW)
        self.assertEqual(result["status"], "NO_ACTIONABLE_PATTERN")
        self.assertEqual(result["candidates"], [])
        self.assertFalse(result["review"]["unified_forecast_used"])

    def test_cost_after_multiday_pattern_creates_behavior_candidate(self):
        self.add_history(days=3)
        self.add_current()
        result = core.evaluate(self.con, NOW)
        self.assertEqual(result["status"], "ACTIONABLE")
        candidate = result["candidates"][0]
        self.assertEqual(candidate["direction"], "LONG")
        self.assertEqual(candidate["decision_source"], "MARKET_BEHAVIOR_CORE")
        self.assertFalse(candidate["evidence"]["unified_forecast_used"])
        self.assertIn("ORDER_BOOK", candidate["evidence"]["capabilities_used"])
        self.assertIn("LIQUIDATIONS", candidate["evidence"]["capabilities_used"])

    def test_missing_or_blocked_risk_is_fail_closed(self):
        self.add_history(days=3)
        self.add_current()
        self.con.execute("UPDATE asset_risk_snapshots SET new_position_allowed=0")
        result = core.evaluate(self.con, NOW)
        self.assertEqual(result["candidates"], [])

    def test_disagreeing_book_and_taker_flow_does_not_trade(self):
        self.add_history(days=3)
        self.add_current()
        self.con.execute(
            "UPDATE market_behavior_minute_features SET taker_imbalance_snapshot_mean=-.4"
        )
        result = core.evaluate(self.con, NOW)
        self.assertEqual(result["candidates"], [])

    def test_future_available_outcome_cannot_train_policy(self):
        self.add_history(days=3)
        self.add_current()
        self.con.execute(
            "UPDATE behavior_event_forward_outcomes SET outcome_available_at=?",
            ((NOW + timedelta(hours=1)).isoformat(),),
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


if __name__ == "__main__":
    unittest.main()
