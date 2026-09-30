import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from database import technical_bar_store
from data.binance_technical_bars import SOURCE_WS
from paper_trading import behavior_decision_core
from risk_manager import local_symbol_risk as local
from risk_manager import risk_manager as manager


NOW = datetime(2026, 9, 30, 12, 1, tzinfo=timezone.utc)


def asset(symbol, **updates):
    metrics = {"symbol": symbol, "as_of": NOW.isoformat(), "price": 100,
               "return_24h_pct": 0.5, "range_24h_pct": 2,
               "hourly_volatility_pct": 0.3, "drawdown_7d_pct": -1,
               "quote_volume_24h": 100_000_000}
    metrics.update(updates)
    return manager.classify_asset_risk(metrics)


class GlobalRiskFailureDomainTests(unittest.TestCase):
    def test_cross_source_failure_reduces_caps_and_count_without_global_veto(self):
        assets = [asset(symbol) for symbol in manager.SYMBOLS] + [asset("NEWUSDT")]
        policy = manager.build_global_policy(assets, {"CROSS_SOURCE_BTC": "timeout"})
        self.assertEqual(policy["market_state"], "DEFENSIVE")
        self.assertEqual(policy["helper_health"], "DEGRADED")
        self.assertEqual(policy["max_new_positions"], 1)
        self.assertEqual(policy["max_total_exposure_pct"], 20)
        self.assertTrue(assets[-1]["new_position_allowed"])
        self.assertIn("GLOBAL_HELPER_DEGRADED_BOUNDED_PAPER", policy["policy_reasons"])
        self.assertEqual(assets[-1]["max_position_pct"], 3)

    def test_missing_fixed_cohort_does_not_exclude_valid_dynamic_symbol(self):
        dynamic = asset("NEWUSDT")
        policy = manager.build_global_policy([dynamic], {symbol: "unavailable" for symbol in manager.SYMBOLS})
        self.assertTrue(policy["new_positions_allowed"])
        self.assertTrue(dynamic["new_position_allowed"])
        self.assertEqual(policy["coverage_count"], 0)
        self.assertEqual(policy["market_state"], "DEFENSIVE")
        self.assertIn("BTC_REFERENCE_MISSING", policy["policy_reasons"])
        self.assertIn("COVERAGE_BELOW_80_PERCENT", policy["policy_reasons"])

    def test_helper_failure_never_relaxes_observed_tail_stress(self):
        assets = [asset(symbol) for symbol in manager.SYMBOLS]
        assets[0] = asset("BTCUSDT", return_24h_pct=-15)
        policy = manager.build_global_policy(assets, {"CROSS_SOURCE_BTC": "timeout"})
        self.assertEqual(policy["market_state"], "LOCKDOWN")
        self.assertEqual(policy["max_single_position_pct"], .25)

    def test_low_liquidity_remains_hard_asset_veto_during_helper_degradation(self):
        thin = asset("NEWUSDT", quote_volume_24h=10_000)
        manager.build_global_policy([thin], {"BTCUSDT": "unavailable"})
        self.assertFalse(thin["new_position_allowed"])
        self.assertEqual(thin["max_position_pct"], 0)

    def test_healthy_reference_context_keeps_normal_limits(self):
        policy = manager.build_global_policy([asset(symbol) for symbol in manager.SYMBOLS], {})
        self.assertEqual(policy["market_state"], "NORMAL")
        self.assertEqual(policy["helper_health"], "READY")
        self.assertEqual(policy["max_new_positions"], 3)


class LocalSymbolRiskTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.con = sqlite3.connect(self.path)
        manager.ensure_tables(self.con)
        technical_bar_store.ensure_table(self.con)
        self.con.execute("""CREATE TABLE market_behavior_minute_features(
            instrument TEXT, minute_end_timestamp_ms INTEGER,
            market_data_provider TEXT, eligible_for_learning INTEGER)""")
        policy = manager.build_global_policy([asset(symbol) for symbol in manager.SYMBOLS], {},
                                             (NOW-timedelta(minutes=10)).isoformat())
        self.policy_id = manager.save_snapshot(self.con, policy, [], {})
        local.bootstrap_schema(self.path)

    def tearDown(self):
        self.con.close()
        os.unlink(self.path)

    def add_symbol(self, symbol="NEWUSDT", *, quote_volume=2000, bar_count=360,
                   last_close_ms=None, source=SOURCE_WS):
        last_close_ms = last_close_ms or int(NOW.timestamp()*1000)-1
        bars = []
        for index in range(bar_count):
            close_ms = last_close_ms - (bar_count-index-1)*60_000
            bars.append({"symbol": symbol, "open_time_ms": close_ms-59_999,
                         "close_time_ms": close_ms, "open": 100, "high": 100.1,
                         "low": 99.9, "close": 100, "volume": 20,
                         "quote_volume": quote_volume, "trade_count": 50,
                         "taker_buy_quote_volume": quote_volume/2, "source": source})
        technical_bar_store.upsert_bars(self.con, bars)
        self.con.execute("INSERT INTO market_behavior_minute_features VALUES(?,?,?,1)",
                         (symbol[:-4]+"-USDT-SWAP", int(NOW.timestamp()*1000)-30_000, "BINANCE"))
        self.con.commit()
        return bars

    def risks(self, now=NOW):
        return behavior_decision_core._latest_risk(self.con, now)

    def test_new_dynamic_symbol_covered_inside_180s_without_network_or_global_cycle(self):
        self.add_symbol()
        self.assertNotIn("NEWUSDT", self.risks())
        with mock.patch("requests.get", side_effect=AssertionError("network forbidden")):
            result = local.refresh_local_symbol_risk(self.path, now=NOW)
            risk = self.risks(NOW+timedelta(seconds=15))["NEWUSDT"]
        self.assertEqual(result["ready"], 1)
        self.assertTrue(risk["allowed"])
        self.assertEqual(risk["cap_pct"], 1.5)
        self.assertEqual(risk["measured_window_minutes"], 360)
        self.assertEqual(risk["measured_quote_volume"], 720_000)
        self.assertFalse(risk["long_window_metrics_available"])
        self.assertNotIn("drawdown_7d_pct", risk)
        self.assertNotIn("quote_volume_24h", risk)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM risk_policy_snapshots").fetchone()[0], 1)
        self.assertEqual(risk["global_limits"]["max_new_positions"], 3)

    def test_missing_history_is_real_evidence_gap_and_durable_reason(self):
        self.add_symbol(bar_count=359)
        result = local.refresh_local_symbol_risk(self.path, now=NOW)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertNotIn("NEWUSDT", self.risks())
        status, error = self.con.execute(f"SELECT status,error FROM {local.TABLE}").fetchone()
        self.assertEqual(status, "EVIDENCE_UNAVAILABLE")
        self.assertIn("INSUFFICIENT_ROWS", error)

    def test_noncontiguous_bars_are_rejected(self):
        self.add_symbol(bar_count=361)
        self.con.execute("DELETE FROM technical_minute_bars WHERE open_time_ms=(SELECT open_time_ms FROM technical_minute_bars ORDER BY open_time_ms LIMIT 1 OFFSET 30)")
        self.con.commit()
        result = local.refresh_local_symbol_risk(self.path, now=NOW)
        self.assertIn("NON_CONTIGUOUS", result["errors"]["NEWUSDT"])
        self.assertNotIn("NEWUSDT", self.risks())

    def test_stale_bars_are_not_freshened_by_recalculation(self):
        self.add_symbol(last_close_ms=int((NOW-timedelta(minutes=5)).timestamp()*1000)-1)
        result = local.refresh_local_symbol_risk(self.path, now=NOW)
        self.assertIn("LATEST_CANDLE_STALE", result["errors"]["NEWUSDT"])

    def test_nonfinite_corrupt_canonical_bars_fail_closed(self):
        self.add_symbol()
        self.con.execute("UPDATE technical_minute_bars SET close=? WHERE open_time_ms=(SELECT MAX(open_time_ms) FROM technical_minute_bars)", (float("inf"),))
        self.con.commit()
        result = local.refresh_local_symbol_risk(self.path, now=NOW)
        self.assertIn("NOT_FINITE", result["errors"]["NEWUSDT"])
        self.assertNotIn("NEWUSDT", self.risks())

    def test_foreign_bar_source_is_rejected(self):
        self.add_symbol(source="OTHER_EXCHANGE")
        result = local.refresh_local_symbol_risk(self.path, now=NOW)
        self.assertIn("NON_CANONICAL", result["errors"]["NEWUSDT"])

    def test_low_measured_liquidity_is_visible_veto(self):
        self.add_symbol(quote_volume=10)
        result = local.refresh_local_symbol_risk(self.path, now=NOW)
        risk = self.risks()["NEWUSDT"]
        self.assertEqual(result["vetoed"], 1)
        self.assertFalse(risk["allowed"])
        self.assertEqual(risk["cap_pct"], 0)

    def test_local_evidence_expires_and_does_not_migrate_to_new_policy(self):
        self.add_symbol()
        local.refresh_local_symbol_risk(self.path, now=NOW)
        self.assertNotIn("NEWUSDT", self.risks(NOW+timedelta(seconds=121)))
        new_policy = manager.build_global_policy([asset(symbol) for symbol in manager.SYMBOLS], {}, NOW.isoformat())
        manager.save_snapshot(self.con, new_policy, [], {})
        self.assertNotIn("NEWUSDT", self.risks())

    def test_local_producer_cannot_bypass_full_historical_asset_veto(self):
        self.add_symbol()
        local.refresh_local_symbol_risk(self.path, now=NOW)
        self.con.execute("""INSERT INTO asset_risk_snapshots(policy_snapshot_id,symbol,as_of,price,
            return_24h_pct,range_24h_pct,hourly_volatility_pct,drawdown_7d_pct,quote_volume_24h,
            risk_score,risk_tier,max_position_pct,volatility_stop_reference_pct,new_position_allowed,
            reason_json,created_at) VALUES(?,?,?,?,0,1,1,0,1000,3,'EXTREME',0,2,0,'[]',?)""",
                         (self.policy_id, "NEWUSDT", NOW.isoformat(), 100, NOW.isoformat()))
        self.con.commit()
        self.assertFalse(self.risks()["NEWUSDT"]["allowed"])
        self.assertEqual(self.risks()["NEWUSDT"]["cap_pct"], 0)
        result = local.refresh_local_symbol_risk(self.path, now=NOW+timedelta(seconds=30))
        self.assertEqual(result["attempted"], 0)

    def test_stale_or_blocked_global_policy_cannot_be_replaced_by_local_evidence(self):
        self.add_symbol()
        self.con.execute("UPDATE risk_policy_snapshots SET new_positions_allowed=0")
        self.con.commit()
        self.assertEqual(local.refresh_local_symbol_risk(self.path, now=NOW)["status"], "GLOBAL_POLICY_BLOCKED")
        self.con.execute("UPDATE risk_policy_snapshots SET new_positions_allowed=1,created_at=?", ((NOW-timedelta(hours=1)).isoformat(),))
        self.con.commit()
        self.assertEqual(local.refresh_local_symbol_risk(self.path, now=NOW)["status"], "GLOBAL_POLICY_UNAVAILABLE")
        self.assertEqual(self.risks(), {})

    def test_bounded_batches_rotate_unavailable_symbols_without_starvation(self):
        self.add_symbol("AUSDT", bar_count=3)
        self.add_symbol("BUSDT")
        first = local.refresh_local_symbol_risk(self.path, now=NOW, max_symbols=1)
        second = local.refresh_local_symbol_risk(self.path, now=NOW+timedelta(seconds=15), max_symbols=1)
        self.assertEqual((first["attempted"], second["attempted"]), (1, 1))
        self.assertIn("AUSDT", first["errors"])
        self.assertIn("BUSDT", self.risks(NOW+timedelta(seconds=15)))

    def test_tampered_local_contract_or_nonfinite_cap_is_rejected(self):
        self.add_symbol()
        local.refresh_local_symbol_risk(self.path, now=NOW)
        self.con.execute(f"UPDATE {local.TABLE} SET contract_version='OLD'")
        self.con.commit()
        self.assertNotIn("NEWUSDT", self.risks())
        payload = json.loads(self.con.execute(f"SELECT evidence_json FROM {local.TABLE}").fetchone()[0])
        payload["cap_pct"] = float("nan")
        self.con.execute(f"UPDATE {local.TABLE} SET contract_version=?,evidence_json=?", (local.CONTRACT_VERSION, json.dumps(payload)))
        self.con.commit()
        self.assertNotIn("NEWUSDT", self.risks())

    def test_incomplete_candidate_critical_local_measurements_are_rejected(self):
        self.add_symbol()
        local.refresh_local_symbol_risk(self.path, now=NOW)
        payload = json.loads(self.con.execute(f"SELECT evidence_json FROM {local.TABLE}").fetchone()[0])
        del payload["measured_quote_volume"]
        self.con.execute(f"UPDATE {local.TABLE} SET evidence_json=?", (json.dumps(payload),))
        self.con.commit()
        self.assertNotIn("NEWUSDT", self.risks())

    def test_local_cap_cannot_exceed_latest_global_single_cap(self):
        self.add_symbol()
        self.con.execute("UPDATE risk_policy_snapshots SET max_single_position_pct=.25")
        self.con.commit()
        local.refresh_local_symbol_risk(self.path, now=NOW)
        self.assertEqual(self.risks()["NEWUSDT"]["cap_pct"], .25)


if __name__ == "__main__":
    unittest.main()
