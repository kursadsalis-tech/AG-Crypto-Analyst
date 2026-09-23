import sqlite3
import unittest
from unittest import mock

from runtime import behavior_learning_worker as worker


class BehaviorLearningWorkerContentionTests(unittest.TestCase):
    def test_cycle_retries_transient_database_lock_with_fresh_attempt(self):
        success = {"pattern_count": 21}
        with (
            mock.patch.object(
                worker,
                "run_cycle_once",
                side_effect=[
                    sqlite3.OperationalError("database is locked"),
                    success,
                ],
            ) as cycle_once,
            mock.patch.object(worker.time, "sleep") as sleeper,
        ):
            result = worker.run_cycle(attempts=2)

        self.assertEqual(cycle_once.call_count, 2)
        sleeper.assert_called_once_with(0.5)
        self.assertEqual(result["pattern_count"], 21)
        self.assertEqual(result["sqlite_attempts"], 2)

    def test_cycle_does_not_hide_non_lock_operational_error(self):
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

    def test_learning_connection_is_plain_local_sqlite(self):
        fake = mock.MagicMock(spec=sqlite3.Connection)
        with mock.patch.object(
            worker.sqlite3, "connect", return_value=fake
        ) as connect_mock:
            result = worker.local_connection()

        self.assertIs(result, fake)
        connect_mock.assert_called_once_with(worker.DB_PATH, timeout=5)
        fake.execute.assert_called_once_with("PRAGMA busy_timeout=5000")


if __name__ == "__main__":
    unittest.main()
