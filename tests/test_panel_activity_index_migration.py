import sqlite3
import unittest

from paper_trading import crypto_profit_agent, trade_learning
from scripts import migrate_panel_activity_indexes as migration


class PanelActivityIndexMigrationTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript("""
            CREATE TABLE paper_behavior_selection_runs(
                run_id INTEGER PRIMARY KEY, created_at TEXT);
            CREATE TABLE paper_agent_positions(
                position_id INTEGER PRIMARY KEY, selected_at TEXT);
            CREATE TABLE paper_position_events(
                event_id INTEGER PRIMARY KEY, observed_at TEXT);
            CREATE TABLE paper_agent_trade_learning_reviews(
                review_id INTEGER PRIMARY KEY, created_at TEXT);
        """)

    def tearDown(self):
        self.con.close()

    def index_names(self, table):
        return {row[1] for row in self.con.execute(f"PRAGMA index_list({table})")}

    def test_migration_requires_all_tables_before_writing(self):
        self.con.execute("DROP TABLE paper_position_events")
        with self.assertRaisesRegex(RuntimeError, "paper_position_events"):
            migration.apply_activity_indexes(self.con)
        for _, table, _ in migration.ACTIVITY_INDEXES:
            if table == "paper_position_events":
                continue
            self.assertFalse(self.index_names(table))

    def test_migration_creates_all_four_indexes_and_is_idempotent(self):
        migration.apply_activity_indexes(self.con)
        migration.apply_activity_indexes(self.con)
        for name, table, _ in migration.ACTIVITY_INDEXES:
            self.assertIn(name, self.index_names(table))

    def test_indexes_are_normal_single_column_indexes(self):
        migration.apply_activity_indexes(self.con)
        for name, table, column in migration.ACTIVITY_INDEXES:
            rows = {row[1]: row for row in self.con.execute(f"PRAGMA index_list({table})")}
            self.assertIn(name, rows)
            self.assertEqual(rows[name][4], 0)  # not a partial index
            keys = [row[2] for row in self.con.execute(f"PRAGMA index_info({name})")]
            self.assertEqual(keys, [column])

    def test_activity_queries_use_covering_indexes_without_temp_sort(self):
        migration.apply_activity_indexes(self.con)
        for name, table, column in migration.ACTIVITY_INDEXES:
            plan = migration.activity_query_plan(self.con, table, column)
            text = " | ".join(plan)
            self.assertIn(f"USING COVERING INDEX {name}", text)
            self.assertNotIn("USE TEMP B-TREE", text)

    def test_latest_timestamp_results_are_preserved(self):
        expected = "2026-09-30T00:00:03+00:00"
        for _, table, column in migration.ACTIVITY_INDEXES:
            self.con.executemany(
                f"INSERT INTO {table}({column}) VALUES(?)",
                [("2026-09-30T00:00:01+00:00",), (None,), (expected,),
                 ("2026-09-30T00:00:02+00:00",)],
            )
            before = self.con.execute(
                f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL "
                f"ORDER BY {column} DESC LIMIT 1"
            ).fetchone()[0]
            self.assertEqual(before, expected)
        migration.apply_activity_indexes(self.con)
        for _, table, column in migration.ACTIVITY_INDEXES:
            after = self.con.execute(
                f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL "
                f"ORDER BY {column} DESC LIMIT 1"
            ).fetchone()[0]
            self.assertEqual(after, expected)

    def test_schema_owners_create_indexes_for_new_databases(self):
        con = sqlite3.connect(":memory:")
        try:
            crypto_profit_agent.ensure_tables(con)
            trade_learning.ensure_tables(con)
            for name, table, _ in migration.ACTIVITY_INDEXES:
                indexes = {row[1] for row in con.execute(f"PRAGMA index_list({table})")}
                self.assertIn(name, indexes)
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
