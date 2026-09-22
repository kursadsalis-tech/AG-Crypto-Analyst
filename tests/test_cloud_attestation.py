import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from runtime import cloud_attestation


class CloudAttestationTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript("""
            CREATE TABLE paper_agent_portfolio(
                portfolio_id INTEGER PRIMARY KEY,initial_capital REAL,cash REAL,
                realized_pnl REAL,total_fees REAL,validation_cohort TEXT
            );
            CREATE TABLE paper_agent_positions(
                status TEXT,validation_cohort TEXT,evidence_eligible INTEGER
            );
            INSERT INTO paper_agent_portfolio VALUES(
                1,10000,6000,25,4,'FORWARD_CLEAN_V2_20260825'
            );
            INSERT INTO paper_agent_positions VALUES(
                'OPEN','FORWARD_CLEAN_V2_20260825',1
            );
        """)

    def tearDown(self):
        self.con.close()

    def test_attestation_is_paper_only_complete_and_hash_verified(self):
        now = datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc)
        results = {
            "product_cycle": {"status": "PARTIAL"},
            "hourly_job": {
                "status": "SUCCESS",
                "paper_agent": {"status": "SUCCESS"},
            },
        }
        checkout_source = {"git_sha": "a" * 40, "git_dirty": False}
        process_source = {"git_sha": "b" * 40, "git_dirty": False}
        realtime = {
            "state": "CURRENT", "status": "CONNECTED", "age_seconds": 0.1,
            "required": True, "real_order_allowed": False,
            "process_runtime_source": process_source,
        }
        with patch.dict(os.environ, {"AGCA_EXECUTION_MODE": "PAPER_ONLY"}), \
                patch.object(cloud_attestation, "runtime_source_state", return_value=checkout_source), \
                patch.object(cloud_attestation, "paper_realtime_health", return_value=realtime):
            payload, encoded, digest = cloud_attestation.build(
                self.con, "HEALTHY", results, now
            )
        self.assertEqual(payload["execution_mode"], "PAPER_ONLY")
        self.assertFalse(payload["may_send_real_orders"])
        self.assertEqual(payload["runtime_source"], {
            "git_sha": process_source["git_sha"],
            "git_dirty": False,
            "checkout_git_sha": checkout_source["git_sha"],
            "checkout_git_dirty": False,
        })
        self.assertEqual(payload["sqlite_quick_check"], "ok")
        self.assertEqual(payload["positions"]["open"], 1)
        self.assertEqual(payload["attestation_version"], "AGCA_RUNTIME_ATTESTATION_V4_PROCESS_BOUND_PAPER")
        self.assertNotIn("hourly_job_status", payload)
        self.assertNotIn("product_cycle_status", payload)
        self.assertEqual(hashlib.sha256(encoded.encode()).hexdigest(), digest)
        self.assertEqual(json.loads(encoded), payload)

    def test_runtime_source_state_reports_commit_and_dirty_checkout(self):
        clean = Mock(
            returncode=0, stdout="b" * 40 + "\n", stderr=""
        )
        dirty = Mock(
            returncode=0, stdout=" M paper_trading/risk_tiering.py\n", stderr=""
        )
        with patch.object(cloud_attestation.subprocess, "run", side_effect=[clean, dirty]):
            state = cloud_attestation.runtime_source_state("/tmp/repo")
        self.assertEqual(state["git_sha"], "b" * 40)
        self.assertTrue(state["git_dirty"])

    def test_non_paper_mode_fails_closed(self):
        with patch.dict(os.environ, {"AGCA_EXECUTION_MODE": "LIVE"}):
            with self.assertRaises(RuntimeError):
                cloud_attestation.build(self.con, "HEALTHY", {})

    def test_open_position_requires_current_connected_realtime_manager(self):
        now = datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper-realtime.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"status": "CONNECTED", "at": now.isoformat(),
                           "execution_mode": "PAPER_ONLY",
                           "real_order_allowed": False,
                           "process_runtime_source": {
                               "git_sha": "c" * 40, "git_dirty": False,
                           }}, handle)
            payload, _, _ = cloud_attestation.build(
                self.con, "HEALTHY", {}, now,
                paper_realtime_health_path=path,
            )
        health = payload["paper_realtime_manager"]
        self.assertEqual(health["state"], "CURRENT")
        self.assertTrue(health["required"])

    def test_open_position_rejects_idle_realtime_manager(self):
        now = datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper-realtime.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"status": "IDLE", "at": now.isoformat(),
                           "execution_mode": "PAPER_ONLY",
                           "real_order_allowed": False,
                           "process_runtime_source": {
                               "git_sha": "d" * 40, "git_dirty": False,
                           }}, handle)
            result = cloud_attestation.paper_realtime_health(now, path, open_positions=1)
        self.assertEqual(result["state"], "UNHEALTHY")

    def test_realtime_health_without_process_source_is_invalid(self):
        now = datetime(2026, 9, 2, 3, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "paper-realtime.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({
                    "status": "CONNECTED", "at": now.isoformat(),
                    "execution_mode": "PAPER_ONLY",
                    "real_order_allowed": False,
                }, handle)
            result = cloud_attestation.paper_realtime_health(
                now, path, open_positions=1
            )
        self.assertEqual(result["state"], "INVALID")
        self.assertIsNone(result["process_runtime_source"])

    def test_attestation_uses_hash_valid_fresh_integrity_cache(self):
        now = datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sqlite-health.json")
            health = {
                "health_contract_version": "AGCA_SQLITE_INTEGRITY_CACHE_V1",
                "checked_at": now.isoformat(), "result": "ok",
            }
            health["health_sha256"] = hashlib.sha256(json.dumps(
                health, sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest()
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(health, handle)
            with patch.dict(os.environ, {
                "AGCA_EXECUTION_MODE": "PAPER_ONLY",
                "AGCA_ATTESTATION_USE_INTEGRITY_CACHE": "1",
                "AGCA_SQLITE_INTEGRITY_CACHE_PATH": path,
            }):
                payload, _, _ = cloud_attestation.build(self.con, "HEALTHY", {}, now)
        self.assertEqual(payload["sqlite_quick_check"], "ok")
        self.assertEqual(payload["sqlite_integrity_evidence"]["state"], "CURRENT")

    def test_tampered_integrity_cache_fails_closed_without_full_scan(self):
        now = datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sqlite-health.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({
                    "health_contract_version": "AGCA_SQLITE_INTEGRITY_CACHE_V1",
                    "checked_at": now.isoformat(), "result": "ok",
                    "health_sha256": "0" * 64,
                }, handle)
            with patch.dict(os.environ, {
                "AGCA_EXECUTION_MODE": "PAPER_ONLY",
                "AGCA_ATTESTATION_USE_INTEGRITY_CACHE": "1",
                "AGCA_SQLITE_INTEGRITY_CACHE_PATH": path,
            }):
                payload, _, _ = cloud_attestation.build(self.con, "HEALTHY", {}, now)
        self.assertEqual(payload["sqlite_quick_check"], "INVALID")

    def test_market_behavior_health_is_bound_fail_closed(self):
        now = datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "health.json")
            health = {
                "health_contract_version": "AGCA_MARKET_BEHAVIOR_HEALTH_V1",
                "status": "HEALTHY", "completed_at": now.isoformat(),
                "execution_mode": "PAPER_ONLY", "row_count": 12,
                "eligible_row_count": 12, "minute_end_timestamp_ms": 1,
                "report_sha256": "a" * 64,
                "raw_market_payloads_persisted": False,
                "may_send_real_orders": False,
            }
            canonical = json.dumps(
                health, sort_keys=True, separators=(",", ":")
            ).encode()
            health["health_sha256"] = hashlib.sha256(canonical).hexdigest()
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(health, handle)
            payload, _, _ = cloud_attestation.build(
                self.con, "HEALTHY", {}, now, behavior_health_path=path
            )
        observer = payload["market_behavior_observer"]
        self.assertEqual(observer["state"], "CURRENT")
        self.assertEqual(observer["eligible_row_count"], 12)
        self.assertFalse(observer["may_send_real_orders"])

    def test_tampered_market_behavior_health_is_invalid(self):
        now = datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "health.json")
            health = {
                "health_contract_version": "AGCA_MARKET_BEHAVIOR_HEALTH_V1",
                "status": "HEALTHY", "completed_at": now.isoformat(),
                "execution_mode": "PAPER_ONLY", "row_count": 12,
                "eligible_row_count": 12, "minute_end_timestamp_ms": 1,
                "report_sha256": "a" * 64,
                "raw_market_payloads_persisted": False,
                "may_send_real_orders": False,
            }
            canonical = json.dumps(
                health, sort_keys=True, separators=(",", ":")
            ).encode()
            health["health_sha256"] = hashlib.sha256(canonical).hexdigest()
            health["eligible_row_count"] = 11
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(health, handle)
            result = cloud_attestation.market_behavior_health(now, path)
        self.assertEqual(result["state"], "INVALID")

    def test_exact_macro_health_hash_is_bound_to_attestation(self):
        now = datetime(2026, 9, 16, 18, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "macro-health.json")
            health = {
                "health_contract_version": "AGCA_EXACT_MACRO_OBSERVER_HEALTH_V1",
                "status": "HEALTHY", "completed_at": now.isoformat(),
                "execution_mode": "PAPER_ONLY",
                "source_status": "OFFICIAL_RELEASE_PAGE_OBSERVED",
                "active_release_at": now.isoformat(), "events_observed": 3,
                "stored_event_count": 3, "raw_payloads_persisted": False,
                "may_send_real_orders": False,
            }
            canonical = json.dumps(
                health, sort_keys=True, separators=(",", ":")
            ).encode()
            health["health_sha256"] = hashlib.sha256(canonical).hexdigest()
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(health, handle)
            payload, _, _ = cloud_attestation.build(
                self.con, "HEALTHY", {}, now,
                exact_macro_health_path=path,
            )
        macro = payload["exact_macro_observer"]
        self.assertEqual(macro["state"], "CURRENT")
        self.assertEqual(macro["events_observed"], 3)
        self.assertFalse(macro["may_send_real_orders"])


if __name__ == "__main__":
    unittest.main()
