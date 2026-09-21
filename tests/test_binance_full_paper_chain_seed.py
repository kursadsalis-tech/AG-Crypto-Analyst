import sqlite3
from pathlib import Path
import tempfile
import unittest

from scripts.probe_binance_full_paper_chain import _seed_from_canonical


class FullPaperChainSeedTests(unittest.TestCase):
    def _build(self, path, active=False):
        with sqlite3.connect(path) as con:
            con.executescript("""
                CREATE TABLE paper_agent_portfolio(
                    portfolio_id INTEGER PRIMARY KEY, cash REAL
                );
                INSERT INTO paper_agent_portfolio VALUES(1,10000);
                CREATE TABLE paper_agent_positions(
                    position_id INTEGER PRIMARY KEY, status TEXT
                );
                CREATE TABLE paper_behavior_decisions(
                    decision_id INTEGER PRIMARY KEY, evidence_json TEXT
                );
                CREATE TABLE behavior_event_forward_outcomes(
                    event_id TEXT, horizon_minutes INTEGER
                );
                CREATE TABLE huge_irrelevant_table(x TEXT);
                INSERT INTO huge_irrelevant_table VALUES('do-not-copy');
            """)
            con.execute(
                "INSERT INTO paper_agent_positions VALUES(1,?)",
                ("OPEN" if active else "CLOSED",),
            )
            con.execute(
                "INSERT INTO paper_behavior_decisions VALUES(1,'{}')"
            )
            con.execute(
                "INSERT INTO behavior_event_forward_outcomes VALUES('e',240)"
            )

    def test_selective_seed_avoids_irrelevant_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp)/"src.db", Path(tmp)/"dst.db"
            self._build(src, active=False)
            result = _seed_from_canonical(str(src), str(dst))
            self.assertEqual(result["blocked_active_positions"], 0)
            self.assertEqual(result["table_rows"]["paper_agent_positions"], 1)
            with sqlite3.connect(dst) as con:
                names = {r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
            self.assertIn("paper_agent_positions", names)
            self.assertNotIn("huge_irrelevant_table", names)

    def test_active_position_blocks_before_clone_population(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp)/"src.db", Path(tmp)/"dst.db"
            self._build(src, active=True)
            result = _seed_from_canonical(str(src), str(dst))
            self.assertEqual(result["blocked_active_positions"], 1)
            with sqlite3.connect(dst) as con:
                names = {r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
            self.assertEqual(names, set())


if __name__ == "__main__":
    unittest.main()
