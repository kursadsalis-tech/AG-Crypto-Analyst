import sqlite3
import unittest
from datetime import datetime, timezone

from database.causal_evidence_store import load_events, save_exact_macro_event


class CausalEvidenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript("""
            CREATE TABLE crypto_news(
                id INTEGER,source TEXT,title TEXT,published_at TEXT,collected_at TEXT
            );
            CREATE TABLE onchain_observations(
                id INTEGER,collected_at TEXT,source TEXT,chain TEXT,metric TEXT,
                value REAL,unit TEXT
            );
            CREATE TABLE whale_events(
                id INTEGER,source TEXT,block_time TEXT,observed_at TEXT,asset TEXT,
                direction TEXT,event_type TEXT,usd_estimate REAL
            );
            INSERT INTO crypto_news VALUES(
                1,'official','title','2026-08-30T10:00:01Z','2026-08-30 10:00:09'
            );
            INSERT INTO onchain_observations VALUES(
                2,'2026-08-30T10:00:10Z','node','Bitcoin','fee',2,'sat/vB'
            );
            INSERT INTO whale_events VALUES(
                3,'node','2026-08-30T09:59:00Z','2026-08-30T10:00:11Z',
                'BTC','UNKNOWN','LARGE',10000000
            );
        """)

    def tearDown(self):
        self.con.close()

    def test_existing_sources_use_actual_availability(self):
        save_exact_macro_event(self.con, {
            "event_id": "fed-1", "source": "Federal Reserve",
            "event_name": "FOMC", "occurred_at": "2026-08-30T10:00:00Z",
            "available_at": "2026-08-30T10:00:12Z",
        })
        events = load_events(self.con)
        self.assertEqual({row["kind"] for row in events}, {
            "NEWS", "MACRO", "ONCHAIN", "WHALE"
        })
        news = next(row for row in events if row["kind"] == "NEWS")
        whale = next(row for row in events if row["kind"] == "WHALE")
        self.assertEqual(news["available_at"], "2026-08-30T10:00:09+00:00")
        self.assertEqual(whale["available_at"], "2026-08-30T10:00:11+00:00")


if __name__ == "__main__":
    unittest.main()
