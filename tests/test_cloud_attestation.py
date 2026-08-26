import hashlib
import json
import os
import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

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
        with patch.dict(os.environ, {"AGCA_EXECUTION_MODE": "PAPER_ONLY"}):
            payload, encoded, digest = cloud_attestation.build(
                self.con, "HEALTHY", results, now
            )
        self.assertEqual(payload["execution_mode"], "PAPER_ONLY")
        self.assertFalse(payload["may_send_real_orders"])
        self.assertEqual(payload["sqlite_quick_check"], "ok")
        self.assertEqual(payload["positions"]["open"], 1)
        self.assertEqual(payload["paper_agent_status"], "SUCCESS")
        self.assertEqual(hashlib.sha256(encoded.encode()).hexdigest(), digest)
        self.assertEqual(json.loads(encoded), payload)

    def test_non_paper_mode_fails_closed(self):
        with patch.dict(os.environ, {"AGCA_EXECUTION_MODE": "LIVE"}):
            with self.assertRaises(RuntimeError):
                cloud_attestation.build(self.con, "HEALTHY", {})


if __name__ == "__main__":
    unittest.main()
