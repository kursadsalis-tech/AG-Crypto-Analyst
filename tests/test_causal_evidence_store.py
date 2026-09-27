import sqlite3
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timezone

from database.causal_evidence_store import load_events, save_exact_macro_event, evidence_for_closed_minute


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

    def test_closed_minute_projection_never_installs_mirror_ddl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.db"
            with sqlite3.connect(path) as target:
                self.con.commit()
                self.con.backup(target)
            statements = []
            original = sqlite3.connect
            def traced(*args, **kwargs):
                con = original(*args, **kwargs)
                con.set_trace_callback(statements.append)
                return con
            end = int(datetime(2026, 8, 30, 10, 1, tzinfo=timezone.utc).timestamp()*1000)
            with patch("database.causal_evidence_store.sqlite3.connect", side_effect=traced):
                result = evidence_for_closed_minute(path, end)
            self.assertEqual(result["event_count"], 3)
            self.assertFalse(any(sql.lstrip().upper().startswith(("CREATE", "ALTER", "INSERT", "UPDATE", "DELETE")) for sql in statements))
            self.assertIn("PRAGMA query_only=ON", statements)

    def test_existing_sources_use_actual_availability(self):
        macro = {
            "event_id": "fed-1", "source": "Federal Reserve",
            "event_name": "FOMC", "occurred_at": "2026-08-30T10:00:00Z",
            "available_at": "2026-08-30T10:00:12Z",
        }
        save_exact_macro_event(
            self.con, macro, observed_at="2026-08-30T10:00:15Z"
        )
        events = load_events(self.con)
        self.assertEqual({row["kind"] for row in events}, {
            "NEWS", "MACRO", "ONCHAIN", "WHALE"
        })
        news = next(row for row in events if row["kind"] == "NEWS")
        whale = next(row for row in events if row["kind"] == "WHALE")
        self.assertEqual(news["available_at"], "2026-08-30T10:00:09+00:00")
        self.assertEqual(whale["available_at"], "2026-08-30T10:00:11+00:00")
        macro_row = next(row for row in events if row["kind"] == "MACRO")
        self.assertEqual(macro_row["available_at"], "2026-08-30T10:00:15+00:00")

    def test_preloaded_macro_waits_for_release_and_late_collection_stays_late(self):
        event = {
            "event_id": "scheduled", "source": "Federal Reserve",
            "event_name": "FOMC", "occurred_at": "2026-09-16T18:00:00Z",
            "available_at": "2026-09-16T18:00:00Z",
        }
        save_exact_macro_event(
            self.con, event, observed_at="2026-09-15T12:00:00Z"
        )
        row = self.con.execute(
            "SELECT official_release_at,observed_at,available_at "
            "FROM exact_time_macro_events WHERE event_id='scheduled'"
        ).fetchone()
        self.assertEqual(row[2], "2026-09-16T18:00:00+00:00")

        late = dict(event, event_id="late")
        save_exact_macro_event(
            self.con, late, observed_at="2026-09-16T18:07:00Z"
        )
        available = self.con.execute(
            "SELECT available_at FROM exact_time_macro_events WHERE event_id='late'"
        ).fetchone()[0]
        self.assertEqual(available, "2026-09-16T18:07:00+00:00")


if __name__ == "__main__":
    unittest.main()
