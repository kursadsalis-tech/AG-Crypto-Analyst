import json
import sqlite3
import unittest

from data.behavior_event_forward_measurement import audit
from database.market_behavior_store import ensure_table


class BehaviorEventForwardMeasurementTests(unittest.TestCase):
    def test_missing_natural_tables_are_reported_not_invented(self):
        con = sqlite3.connect(":memory:")
        try:
            result = audit(con)
        finally:
            con.close()
        self.assertFalse(result["measurement_ready"])
        self.assertIn("BEHAVIOR_TABLE_MISSING", result["blocking_reasons"])

    def test_exact_minute_and_fixed_forward_horizon_are_counted(self):
        con = sqlite3.connect(":memory:")
        try:
            ensure_table(con)
            base = 120_000
            columns = """instrument,minute_end_timestamp_ms,minute_start_timestamp_ms,
                sample_count,max_sample_gap_ms,book_imbalance_20_mean,
                book_imbalance_20_last,taker_imbalance_snapshot_mean,
                taker_imbalance_snapshot_last,spread_bps_mean,mid_price_first,
                mid_price_last,open_interest_usd_first,open_interest_usd_last,
                open_interest_usd_change,funding_rate_last,
                long_liquidation_notional_usd_estimate,
                short_liquidation_notional_usd_estimate,liquidation_event_count,
                quality_gates_json,eligible_for_learning,created_at"""
            values = ("BTC-USDT-SWAP", base, 60_000, 4, 10_000, 0, 0, 0, 0,
                      1, 100, 100, 1, 1, 0, 0, 0, 0, 0, "{}", 1, "now")
            con.execute(f"INSERT INTO market_behavior_minute_features({columns}) VALUES({','.join('?' * 22)})", values)
            future = list(values)
            future[1], future[2], future[10], future[11] = base + 3_600_000, base + 3_540_000, 101, 101
            con.execute(f"INSERT INTO market_behavior_minute_features({columns}) VALUES({','.join('?' * 22)})", future)
            con.execute("""
                INSERT INTO causal_minute_external_evidence VALUES(
                    'event',?,'MACRO','Fed','t','t','[]','UNKNOWN','{}','now'
                )
            """, (base,))
            result = audit(con)
        finally:
            con.close()
        self.assertEqual(result["exact_minute_join_rows"], 1)
        self.assertEqual(result["forward_outcomes_by_horizon"]["60"], 1)
        self.assertTrue(result["measurement_ready"])


if __name__ == "__main__":
    unittest.main()
