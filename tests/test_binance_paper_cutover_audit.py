"""Read-only cutover audit: actual pending-entry status and risk freshness."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts.audit_binance_paper_cutover import audit


NOW = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp()*1000)


class CutoverAuditTests(unittest.TestCase):
    def test_pending_entry_is_detected_even_if_legacy_pending_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "data.db"
            con = sqlite3.connect(db)
            con.executescript("""
                CREATE TABLE paper_agent_positions(
                    position_id INTEGER PRIMARY KEY, symbol TEXT, market_type TEXT,
                    direction TEXT, status TEXT
                );
                INSERT INTO paper_agent_positions VALUES
                (1,'1000PEPEUSDT','USD_M_PERPETUAL','LONG','PENDING_ENTRY'),
                (2,'BTCUSDT','USD_M_PERPETUAL','SHORT','CLOSED');
                CREATE TABLE paper_funding_reconciliation_segments(status TEXT);
                INSERT INTO paper_funding_reconciliation_segments VALUES('PENDING');
                CREATE TABLE risk_policy_snapshots(
                    id INTEGER PRIMARY KEY,created_at TEXT,market_state TEXT,
                    new_positions_allowed INTEGER,risk_per_trade_pct REAL,
                    coverage_count INTEGER,coverage_total INTEGER
                );
                CREATE TABLE asset_risk_snapshots(
                    policy_snapshot_id INTEGER,symbol TEXT,as_of TEXT,
                    new_position_allowed INTEGER,max_position_pct REAL
                );
                CREATE TABLE market_behavior_minute_features(
                    minute_end_timestamp_ms INTEGER,eligible_for_learning INTEGER
                );
            """)
            con.execute("INSERT INTO risk_policy_snapshots VALUES(1,?,?,?,?,?,?)", (
                (NOW-timedelta(minutes=4)).isoformat(), "NORMAL", 1, .75, 13, 13,
            ))
            con.execute("INSERT INTO asset_risk_snapshots VALUES(1,?,?,1,5)", (
                "1000PEPEUSDT", (NOW-timedelta(minutes=30)).isoformat(),
            ))
            con.execute("INSERT INTO market_behavior_minute_features VALUES(?,1)", (
                NOW_MS-60_000,
            ))
            con.commit()
            con.close()
            metadata = root / "metadata.json"
            metadata.write_text(json.dumps({
                "market": "USD_M_PERPETUAL",
                "fetched_at": (NOW-timedelta(minutes=5)).isoformat(),
                "payload": {"symbols": [{
                    "symbol": "1000PEPEUSDT", "status": "TRADING",
                    "contractType": "PERPETUAL", "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                }]},
            }))
            result = audit(
                db_path=db, metadata_path=metadata,
                health_paths={"paper": str(root/"missing-health.json")}, now_ms=NOW_MS,
            )
            self.assertEqual(result["mode"], "LOCAL_READ_ONLY_NO_POSITION_WRITES")
            self.assertEqual(result["positions"]["active_count"], 1)
            self.assertEqual(result["positions"]["active_rows"][0]["status"], "PENDING_ENTRY")
            self.assertEqual(result["positions"]["active_rows"][0]["valid_native_usdm_contract"], True)
            self.assertEqual(result["pending_funding"]["count"], 1)
            self.assertEqual(result["risk"]["policy"]["age_seconds"], 240)
            self.assertEqual(result["behavior"]["last_eligible_minute_age_seconds"], 60)
            self.assertEqual(result["metadata"]["eligible_native_contract_count"], 1)

    def test_absent_tables_are_not_silently_reported_as_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root/"db"
            with sqlite3.connect(db):
                pass
            result = audit(db_path=db, metadata_path=root/"missing",
                           health_paths={}, now_ms=NOW_MS)
            self.assertIsNone(result["positions"]["active_count"])
            self.assertIsNone(result["pending_funding"]["count"])
            self.assertIsNone(result["risk"]["policy"])


if __name__ == "__main__":
    unittest.main()
