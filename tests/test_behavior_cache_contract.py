"""Exact producer compatibility and complete recovery evidence at the cache boundary."""
import json
import sqlite3
import unittest
from datetime import timedelta
from unittest.mock import patch

from paper_trading import behavior_decision_core as core
from tests import test_behavior_decision_core as fixtures


class BehaviorCacheContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.BehaviorDecisionCoreTests()
        self.fixture.setUp()
        self.con = self.fixture.con
        self.fixture.add_history(days=1)
        self.fixture.add_current()
        core.refresh_pattern_cache(self.con, fixtures.NOW)

    def tearDown(self):
        self.con.close()

    def assert_rejected(self, status):
        with patch.dict("os.environ", {"AGCA_BEHAVIOR_PATTERN_CACHE_REQUIRED": "1"}):
            result = core.evaluate(self.con, fixtures.NOW)
        self.assertEqual(result["status"], "BEHAVIOR_PATTERN_CACHE_UNAVAILABLE")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["review"]["pattern_cache"]["status"], status)
        return result

    def test_old_or_future_contract_rejected_without_fallback(self):
        for version in ("PAPER_BEHAVIOR_PATTERN_CACHE_V1", "PAPER_BEHAVIOR_PATTERN_CACHE_V3"):
            with self.subTest(version=version):
                core.refresh_pattern_cache(self.con, fixtures.NOW)
                self.con.execute("UPDATE paper_behavior_pattern_cache SET contract_version=? WHERE cache_id=(SELECT MAX(cache_id) FROM paper_behavior_pattern_cache)", (version,))
                self.assert_rejected("CONTRACT_MISMATCH")

    def test_producer_and_core_versions_require_exact_equality(self):
        for column in ("producer_version", "core_version"):
            with self.subTest(column=column):
                core.refresh_pattern_cache(self.con, fixtures.NOW)
                self.con.execute(f"UPDATE paper_behavior_pattern_cache SET {column}='OLDER_PRODUCER' WHERE cache_id=(SELECT MAX(cache_id) FROM paper_behavior_pattern_cache)")
                self.assert_rejected("PRODUCER_VERSION_MISMATCH")

    def test_cache_rejection_preserves_open_position_and_history(self):
        self.con.execute("INSERT INTO paper_agent_positions(position_id,status,direction,notional) VALUES(9,'OPEN','SHORT',120)")
        before = self.con.execute("SELECT * FROM paper_agent_positions").fetchall()
        self.con.execute("UPDATE paper_behavior_pattern_cache SET contract_version='PAPER_BEHAVIOR_PATTERN_CACHE_V1'")
        self.assert_rejected("CONTRACT_MISMATCH")
        self.assertEqual(before, self.con.execute("SELECT * FROM paper_agent_positions").fetchall())

    def test_payload_schema_rejects_entire_snapshot_instead_of_partial_acceptance(self):
        valid = json.loads(self.con.execute("SELECT patterns_json FROM paper_behavior_pattern_cache").fetchone()[0])
        mutations = [None, {}, [None], valid + [valid[0]]]
        for field, value in (("direction", "SIDEWAYS"), ("calibrated_net_edge_pct", float("nan")),
                             ("sample_count", True), ("promotion_ready", "false"),
                             ("execution_size_multiplier", -1), ("flow_pattern", "WRONG_KEY")):
            rows = json.loads(json.dumps(valid))
            rows[0]["pattern"][field] = value
            mutations.append(rows)
        rows = json.loads(json.dumps(valid))
        del rows[0]["pattern"]["execution_feedback"]
        mutations.append(rows)
        for payload in mutations:
            with self.subTest(payload=repr(payload)[:100]):
                self.con.execute("UPDATE paper_behavior_pattern_cache SET patterns_json=?", (json.dumps(payload),))
                self.assert_rejected("INVALID_PAYLOAD")

    def test_count_authority_and_source_integrity_are_checked(self):
        for assignment in ("pattern_count=99", "real_order_allowed=1", "producer_source_json='{}'",
                           "producer_git_sha='invalid-sha'"):
            with self.subTest(assignment=assignment):
                core.refresh_pattern_cache(self.con, fixtures.NOW)
                self.con.execute(f"UPDATE paper_behavior_pattern_cache SET {assignment} WHERE cache_id=(SELECT MAX(cache_id) FROM paper_behavior_pattern_cache)")
                self.assert_rejected("INVALID_PAYLOAD")

    def test_latest_future_timestamp_cannot_fall_back_to_older_valid_snapshot(self):
        core.refresh_pattern_cache(self.con, fixtures.NOW)
        self.con.execute("UPDATE paper_behavior_pattern_cache SET generated_at=? WHERE cache_id=(SELECT MAX(cache_id) FROM paper_behavior_pattern_cache)",
                         ((fixtures.NOW + timedelta(seconds=1)).isoformat(),))
        self.assert_rejected("STALE")

    def test_v2_roundtrip_publishes_producer_identity(self):
        patterns, detail = core.load_cached_patterns(self.con, fixtures.NOW)
        self.assertTrue(patterns)
        self.assertEqual(detail["status"], "READY")
        self.assertEqual(detail["contract_version"], "PAPER_BEHAVIOR_PATTERN_CACHE_V2")
        self.assertEqual(detail["producer_version"], core.PATTERN_CACHE_PRODUCER_VERSION)
        self.assertEqual(detail["core_version"], core.VERSION)
        self.assertEqual(detail["producer_source"], core.PROCESS_SOURCE)

    def test_legacy_schema_upgrade_retains_old_snapshot_and_rejects_it(self):
        con = sqlite3.connect(":memory:")
        try:
            con.execute("""CREATE TABLE paper_behavior_pattern_cache(
                cache_id INTEGER PRIMARY KEY,generated_at TEXT,horizon_minutes INTEGER,
                outcome_cutoff_at TEXT,pattern_count INTEGER,patterns_json TEXT,
                contract_version TEXT,real_order_allowed INTEGER)""")
            con.execute("INSERT INTO paper_behavior_pattern_cache VALUES(1,?,240,NULL,0,'[]','PAPER_BEHAVIOR_PATTERN_CACHE_V1',0)", (fixtures.NOW.isoformat(),))
            self.assertEqual(core.load_cached_patterns(con, fixtures.NOW)[1]["status"], "CACHE_SCHEMA_UNAVAILABLE")
            core.ensure_tables(con)
            self.assertEqual(core.load_cached_patterns(con, fixtures.NOW)[1]["status"], "CONTRACT_MISMATCH")
            self.assertEqual(con.execute("SELECT cache_id,patterns_json FROM paper_behavior_pattern_cache").fetchall(), [(1, "[]")])
        finally:
            con.close()

    def test_recovery_and_latest_exit_evidence_required_in_v2(self):
        self.con.execute("DELETE FROM behavior_event_forward_outcomes")
        self.fixture._seed_losing_continuous_pattern(sign=1)
        self.fixture._add_new_recovery_observations(-1)
        self.con.execute("""UPDATE behavior_event_forward_outcomes SET
            event_minute_end_timestamp_ms=event_minute_end_timestamp_ms-864000000
            WHERE independence_unit NOT LIKE 'recovery:%'""")
        core.refresh_pattern_cache(self.con, fixtures.NOW)
        patterns, detail = core.load_cached_patterns(self.con, fixtures.NOW)
        self.assertEqual(detail["status"], "READY")
        recovery = next(value for value in patterns.values() if "recovery_hypothesis" in value)
        self.assertEqual(recovery["recovery_hypothesis"]["direction"], "SHORT")
        self.assertTrue(recovery["execution_feedback"]["latest_exit_at"])
        for missing in ("latest_exit_at", "observation_start_ms"):
            rows = json.loads(core._patterns_to_json(patterns))
            pattern = next(row["pattern"] for row in rows if "recovery_hypothesis" in row["pattern"])
            target = pattern["execution_feedback"] if missing == "latest_exit_at" else pattern["recovery_hypothesis"]
            del target[missing]
            self.con.execute("UPDATE paper_behavior_pattern_cache SET patterns_json=? WHERE cache_id=(SELECT MAX(cache_id) FROM paper_behavior_pattern_cache)", (json.dumps(rows),))
            self.assert_rejected("INVALID_PAYLOAD")


if __name__ == "__main__":
    unittest.main()
