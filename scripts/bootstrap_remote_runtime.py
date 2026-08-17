"""Mirror all existing operational runtime tables to the remote store once."""

from __future__ import annotations

import sqlite3

from database.runtime_store import REMOTE_RUNTIME_TABLES, _mirror_after_local_commit


DB_NAME = "crypto_data.db"


def main() -> int:
    con = sqlite3.connect(DB_NAME)
    try:
        existing = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        selected = existing & REMOTE_RUNTIME_TABLES
        if not selected:
            print("No AGCA runtime tables found in the local SQLite database.")
            return 1
        _mirror_after_local_commit(con, selected)
        print(f"Remote runtime bootstrap complete: {len(selected)} tables")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
