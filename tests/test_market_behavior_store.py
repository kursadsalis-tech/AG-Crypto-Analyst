import sqlite3
import tempfile
import unittest
from pathlib import Path

from database.market_behavior_store import (
    EXTERNAL_EVIDENCE_TABLE, TABLE, persist_closed_minute,
)
from database.runtime_store import REMOTE_RUNTIME_TABLES


def report(end, instrument="AAA"):
    row = {
        "instrument": instrument, "minute_start_timestamp_ms": end - 60_000,
        "sample_count": 4, "max_sample_gap_ms": 10_000,
        "book_imbalance_20_mean": 0.1, "book_imbalance_20_last": 0.2,
        "taker_imbalance_snapshot_mean": -0.1, "taker_imbalance_snapshot_last": -0.2,
        "spread_bps_mean": 1, "open_interest_usd_first": 100,
        "mid_price_first": 10, "mid_price_last": 11,
        "open_interest_usd_last": 110, "open_interest_usd_change": 10,
        "funding_rate_last": 0.0001,
        "long_liquidation_notional_usd_estimate": 0,
        "short_liquidation_notional_usd_estimate": 0, "liquidation_event_count": 0,
        "quality_gates": {"ok": True}, "eligible_for_learning": True,
    }
    return {"minute_is_closed": True, "minute_end_timestamp_ms": end, "rows": [row]}


class MarketBehaviorStoreTests(unittest.TestCase):
    def test_idempotent_upsert_and_local_only_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            persist_closed_minute(path, report(100_000))
            persist_closed_minute(path, report(100_000))
            with sqlite3.connect(path) as connection:
                count = connection.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
            self.assertEqual(count, 1)
            self.assertNotIn(TABLE, REMOTE_RUNTIME_TABLES)

    def test_missing_binance_oi_is_not_persisted_as_real_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            incoming = report(120_000, "1000PEPEUSDT")
            incoming["source_exchange"] = "BINANCE_USD_M"
            first = persist_closed_minute(path, incoming)
            self.assertEqual(first["upserted"], 1)
            # A re-observation that loses the required OI must remove any
            # previously accepted sample for that exact minute as well.
            row = incoming["rows"][0]
            row["eligible_for_learning"] = False
            row["quality_gates"] = {"required_sources": False}
            row["open_interest_usd_first"] = 0
            row["open_interest_usd_last"] = 0
            row["open_interest_usd_change"] = 0
            second = persist_closed_minute(path, incoming)
            self.assertEqual(second["upserted"], 0)
            self.assertEqual(second["ineligible_binance_rows_skipped"], 1)
            with sqlite3.connect(path) as connection:
                stored = connection.execute(
                    f"SELECT COUNT(*) FROM {TABLE} WHERE instrument='1000PEPEUSDT'"
                ).fetchone()[0]
            self.assertEqual(stored, 0)

    def test_retention_prunes_old_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            persist_closed_minute(path, report(100_000), retention_days=1)
            result = persist_closed_minute(path, report(200_000_000, "BBB"), retention_days=1)
            self.assertEqual(result["pruned"], 1)
            self.assertEqual(result["row_count"], 1)

    def test_external_evidence_is_idempotent_and_local_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.db"
            payload = report(120_000)
            payload["causal_external_evidence"] = {
                "assignment_clock": "AVAILABLE_AT",
                "events": [{
                    "event_id": "fed-1", "kind": "MACRO", "source": "Fed",
                    "occurred_at": "1970-01-01T00:01:10+00:00",
                    "available_at": "1970-01-01T00:01:10+00:00",
                    "symbols": [], "direction": "UNKNOWN", "detail": {},
                }],
            }
            first = persist_closed_minute(path, payload)
            second = persist_closed_minute(path, payload)
            self.assertEqual(first["external_evidence_row_count"], 1)
            self.assertEqual(second["external_evidence_row_count"], 1)
            with sqlite3.connect(path) as connection:
                row = connection.execute(
                    f"SELECT minute_end_timestamp_ms,kind FROM {EXTERNAL_EVIDENCE_TABLE}"
                ).fetchone()
            self.assertEqual(row, (120_000, "MACRO"))
            self.assertNotIn(EXTERNAL_EVIDENCE_TABLE, REMOTE_RUNTIME_TABLES)

    def test_rejects_open_minute(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                persist_closed_minute(Path(tmp) / "test.db", {"minute_is_closed": False})


if __name__ == "__main__":
    unittest.main()
