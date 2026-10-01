import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from runtime import trade_learning_worker as worker


class TradeLearningWorkerTests(unittest.TestCase):
    def test_cycle_retries_transient_database_lock(self):
        with (
            mock.patch.object(
                worker,
                "run_cycle_once",
                side_effect=[
                    sqlite3.OperationalError("database is locked"),
                    {"learning_status": "OK"},
                ],
            ) as cycle_once,
            mock.patch.object(worker.time, "sleep") as sleeper,
        ):
            result = worker.run_cycle(attempts=2)

        self.assertEqual(cycle_once.call_count, 2)
        sleeper.assert_called_once_with(0.5)
        self.assertEqual(result["learning_status"], "OK")
        self.assertEqual(result["sqlite_attempts"], 2)

    def test_learning_connection_is_plain_local_sqlite(self):
        fake = mock.MagicMock(spec=sqlite3.Connection)
        with mock.patch.object(
            worker, "connect_local", return_value=fake
        ) as connect_mock:
            result = worker.local_connection()

        self.assertIs(result, fake)
        connect_mock.assert_called_once_with(worker.DB_NAME, timeout=0.25)
        fake.execute.assert_called_once_with("PRAGMA busy_timeout=250")

    def test_input_key_ignores_runtime_churn_but_tracks_final_costs_and_observations(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            worker, "DB_NAME", str(Path(tmp) / "runtime.db")
        ):
            con = sqlite3.connect(worker.DB_NAME)
            con.executescript("""
                CREATE TABLE paper_agent_positions(position_id INTEGER PRIMARY KEY,
                    status TEXT,evidence_eligible INTEGER,net_pnl REAL,funding_cost REAL);
                INSERT INTO paper_agent_positions VALUES(1,'CLOSED',1,3,0);
                CREATE TABLE external_trader_trade_observations(observation_id INTEGER PRIMARY KEY);
                CREATE TABLE heartbeat(at TEXT);
            """)
            before = worker.learning_input_key()
            con.execute("INSERT INTO heartbeat VALUES('next')")
            con.commit()
            self.assertEqual(worker.learning_input_key(), before)
            con.execute("UPDATE paper_agent_positions SET net_pnl=2,funding_cost=1")
            con.commit()
            corrected = worker.learning_input_key()
            self.assertNotEqual(corrected, before)
            con.execute("INSERT INTO external_trader_trade_observations VALUES(1)")
            con.commit()
            self.assertNotEqual(worker.learning_input_key(), corrected)
            con.close()

    def test_unchanged_inputs_do_not_repeat_full_learning(self):
        with (mock.patch.object(worker, "learning_input_key", return_value={"external": "same", "positions": "same"}),
              mock.patch.object(worker, "run_cycle", return_value={"learning_status": "OK"}) as cycle,
              mock.patch.object(worker, "write_health") as health,
              mock.patch.object(worker.time, "sleep", side_effect=[None, SystemExit])):
            with self.assertRaises(SystemExit):
                worker.main()
        cycle.assert_called_once_with(initialize=True, refresh_external=True)
        self.assertTrue(health.call_args.args[0]["skipped_unchanged_inputs"])

    def test_new_own_trade_does_not_repeat_unchanged_external_research(self):
        with (mock.patch.object(worker, "learning_input_key", side_effect=[
                {"external": "same", "positions": "before"},
                {"external": "same", "positions": "after"}]),
              mock.patch.object(worker, "run_cycle", return_value={}) as cycle,
              mock.patch.object(worker, "write_health"),
              mock.patch.object(worker.time, "sleep", side_effect=[None, SystemExit])):
            with self.assertRaises(SystemExit):
                worker.main()
        self.assertEqual(cycle.call_args_list, [
            mock.call(initialize=True, refresh_external=True),
            mock.call(initialize=False, refresh_external=False)])

    def test_cycle_exports_controlled_challenger_health(self):
        fake = mock.MagicMock(spec=sqlite3.Connection)
        with (
            mock.patch.object(worker, "local_connection", return_value=fake),
            mock.patch.object(worker.experience_memory, "review_closed_positions", return_value=2),
            mock.patch.object(worker.trade_learning, "run", return_value={
                "status": "CHALLENGER_EVIDENCE_READY",
                "closed": 120,
                "weeks": 8,
                "promotion_allowed": False,
                "controlled_challenger": {
                    "challenger_count": 3,
                    "reviewable_count": 1,
                    "statuses": {
                        "FORWARD_COLLECTING": 2,
                        "FORWARD_BETTER_REVIEWABLE": 1,
                    },
                },
            }),
            mock.patch.object(worker.strategy_allocator, "rebalance", return_value={"A": 1.0}),
        ):
            result = worker.run_cycle_once(
                initialize=False, refresh_external=False
            )

        self.assertEqual(result["challenger_count"], 3)
        self.assertEqual(result["reviewable_challenger_count"], 1)
        self.assertEqual(
            result["challenger_statuses"],
            {
                "FORWARD_COLLECTING": 2,
                "FORWARD_BETTER_REVIEWABLE": 1,
            },
        )
        self.assertFalse(result["promotion_allowed"])
        self.assertFalse(result["policy_mutation_allowed"])
        fake.commit.assert_called_once()
        fake.close.assert_called_once()

    def test_live_cycle_uses_prebootstrapped_schema(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "runtime/trade_learning_worker.py"
        ).read_text(encoding="utf-8")
        start = source.index("def run_cycle_once(")
        end = source.index("\n\ndef run_cycle(", start)
        block = source[start:end]
        self.assertIn("trade_learning.ensure_tables(con)", block)
        self.assertIn("controlled_challenger.ensure_tables(con)", block)
        self.assertIn("external_trader_learning.ensure_tables(con)", block)
        self.assertIn("strategy_allocator.ensure_tables(con)", block)
        self.assertIn("refresh_external=refresh_external,\n            ensure=False", block)
        self.assertIn("progress=_PROGRESS.advance", block)
        self.assertIn("con, stamp, ensure=False", block)

    def test_cycle_does_not_hide_non_lock_error(self):
        with (
            mock.patch.object(
                worker,
                "run_cycle_once",
                side_effect=sqlite3.OperationalError("disk I/O error"),
            ),
            mock.patch.object(worker.time, "sleep") as sleeper,
        ):
            with self.assertRaisesRegex(sqlite3.OperationalError, "disk I/O"):
                worker.run_cycle(attempts=3)
        sleeper.assert_not_called()


class RealtimeHotPathContractTests(unittest.TestCase):
    def test_realtime_worker_contains_no_trade_learning_or_allocator_calls(self):
        source = open(
            "runtime/paper_realtime_worker.py", encoding="utf-8"
        ).read()
        self.assertNotIn("trade_learning.run(", source)
        self.assertNotIn("strategy_allocator.rebalance(", source)
        self.assertNotIn("experience_memory.review_closed_positions(", source)


if __name__ == "__main__":
    unittest.main()
