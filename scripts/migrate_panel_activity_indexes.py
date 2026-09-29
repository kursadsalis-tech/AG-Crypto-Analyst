"""Canonical one-time SQLite migration for panel ACTIVITY lookup indexes.

The live panel never imports or runs this module.  It remains read-only.
Run this migration explicitly against a reviewed database only; CREATE INDEX
takes a SQLite writer lock while each index is built.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ACTIVITY_INDEXES = (
    ("idx_paper_behavior_selection_runs_created_at",
     "paper_behavior_selection_runs", "created_at"),
    ("idx_paper_positions_selected_at",
     "paper_agent_positions", "selected_at"),
    ("idx_paper_position_events_observed_at",
     "paper_position_events", "observed_at"),
    ("idx_paper_trade_learning_reviews_created_at",
     "paper_agent_trade_learning_reviews", "created_at"),
)


def _existing_tables(con):
    return {
        row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def apply_activity_indexes(con):
    """Create all four indexes atomically after validating required tables."""
    existing = _existing_tables(con)
    missing = [table for _, table, _ in ACTIVITY_INDEXES if table not in existing]
    if missing:
        raise RuntimeError("missing activity tables: " + ",".join(sorted(missing)))
    for name, table, column in ACTIVITY_INDEXES:
        con.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({column})")


def activity_query_plan(con, table, column):
    return [
        row[3] for row in con.execute(
            f"EXPLAIN QUERY PLAN SELECT {column} FROM {table} "
            f"WHERE {column} IS NOT NULL ORDER BY {column} DESC LIMIT 1"
        )
    ]


def verify_activity_indexes(con):
    """Fail unless every live ACTIVITY lookup is covering and sort-free."""
    report = {}
    for name, table, column in ACTIVITY_INDEXES:
        plan = activity_query_plan(con, table, column)
        text = " | ".join(plan)
        if f"USING COVERING INDEX {name}" not in text:
            raise RuntimeError(f"{table}.{column} is not using {name}: {text}")
        if "USE TEMP B-TREE" in text:
            raise RuntimeError(f"{table}.{column} still uses a temp sort: {text}")
        report[f"{table}.{column}"] = plan
    return report


def migrate(database):
    path = Path(database)
    con = sqlite3.connect(path, timeout=30)
    try:
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("BEGIN IMMEDIATE")
        apply_activity_indexes(con)
        plans = verify_activity_indexes(con)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return {"database": str(path), "indexes": len(ACTIVITY_INDEXES), "plans": plans}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True,
                        help="Exact SQLite database path to migrate")
    parser.add_argument("--apply", action="store_true",
                        help="Required acknowledgement that this performs a schema write")
    args = parser.parse_args(argv)
    if not args.apply:
        parser.error("--apply is required; the panel never migrates automatically")
    print(json.dumps(migrate(args.database), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
