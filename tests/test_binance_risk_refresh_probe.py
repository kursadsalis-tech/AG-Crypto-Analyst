"""Risk refresh acceptance never writes SQLite, including blocked native identity."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import probe_binance_risk_refresh as probe


class RiskRefreshReadOnlyTests(unittest.TestCase):
    def test_non_native_legacy_symbol_blocks_without_any_risk_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "risk.db"
            con = sqlite3.connect(db)
            con.executescript("""
                CREATE TABLE market_behavior_minute_features(
                    instrument TEXT, minute_end_timestamp_ms INTEGER,
                    eligible_for_learning INTEGER
                );
                INSERT INTO market_behavior_minute_features VALUES(
                    'PEPEUSDT',9999999999999,1
                );
            """)
            con.close()
            fake = SimpleNamespace(symbols={"1000PEPEUSDT": object()})
            with patch.object(probe, "load_cache", return_value=fake), \
                 patch.object(probe.risk, "behavior_risk_symbols",
                              return_value=("PEPEUSDT",)), \
                 patch.object(probe.risk, "btc_independent_validation") as independent, \
                 patch.dict(os.environ, {"AGCA_EXECUTION_MODE": "PAPER_ONLY"}):
                result = probe.shadow_risk(
                    db_path=str(db), metadata_path=str(Path(tmp)/"cached"),
                    database_url="fake-dsn",
                )
            self.assertEqual(result["status"], "BLOCKED_DYNAMIC_UNIVERSE")
            self.assertEqual(result["unrecognised_count"], 1)
            self.assertFalse(result["snapshot_written"])
            independent.assert_not_called()
            con = sqlite3.connect(db)
            self.assertIsNone(con.execute("""SELECT name FROM sqlite_master
                WHERE name='risk_policy_snapshots'""").fetchone())
            con.close()

    def test_missing_private_egress_fails_before_opening_db(self):
        with patch.dict(os.environ, {"AGCA_EXECUTION_MODE": "PAPER_ONLY"}):
            with self.assertRaisesRegex(RuntimeError, "EGRESS_NOT_CONFIGURED"):
                probe.shadow_risk(db_path="/unavailable", database_url="")

    def test_actual_context_plus_dynamic_uses_both_venue_feeds_but_no_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp)/"risk.db"
            with sqlite3.connect(db) as con:
                con.execute("CREATE TABLE audit_sample(id INTEGER PRIMARY KEY)")
            fake = SimpleNamespace(symbols={"BTCUSDT": object(),
                                            "1000PEPEUSDT": object()})
            with patch.object(probe, "load_cache", return_value=fake), \
                 patch.object(probe.risk, "behavior_risk_symbols",
                              return_value=("BTCUSDT", "1000PEPEUSDT")), \
                 patch.object(probe.risk, "SYMBOLS", ("BTCUSDT", "ETHUSDT")), \
                 patch.object(probe.risk, "btc_independent_validation",
                              return_value={"deviation_pct": 0.2}), \
                 patch.object(probe.risk, "spot_closed_hourly", return_value=[] ) as spot, \
                 patch.object(probe.risk, "fetch_dynamic_closed_1h_klines",
                              return_value=[] ) as futures, \
                 patch.object(probe.risk, "candle_metrics", side_effect=lambda s, c: {"symbol": s}), \
                 patch.object(probe.risk, "classify_asset_risk", side_effect=lambda m: m), \
                 patch.object(probe.risk, "build_global_policy", return_value={
                     "market_state": "CAUTION", "coverage_count": 2,
                     "coverage_total": 2, "new_positions_allowed": True,
                 }), patch.dict(os.environ, {"AGCA_EXECUTION_MODE": "PAPER_ONLY"}):
                report = probe.shadow_risk(
                    db_path=str(db), metadata_path=str(Path(tmp)/"cached"),
                    database_url="fake-dsn",
                )
            self.assertEqual(report["status"], "COMPLETE")
            self.assertEqual(report["risk_assets_computed"], 3)
            self.assertEqual(report["market_state"], "CAUTION")
            self.assertEqual(futures.call_count, 2)
            spot.assert_called_once_with("fake-dsn", "ETHUSDT")
            self.assertFalse(report["snapshot_written"])
            with sqlite3.connect(db) as con:
                self.assertIsNone(con.execute("""SELECT name FROM sqlite_master
                    WHERE name='risk_policy_snapshots'""").fetchone())


if __name__ == "__main__":
    unittest.main()
