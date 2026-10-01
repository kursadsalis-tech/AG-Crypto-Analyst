"""Latest-only, local Binance radar evidence; no decision or order authority.

The existing technical worker owns publication. Deep collectors only read this
snapshot. Storage and refresh cost do not depend on the number of deep slots.
"""
from __future__ import annotations

import json
from database.sqlite_policy import connect_local
from pathlib import Path

MAX_RADAR_AGE_MS = 180_000
TABLE = "broad_market_radar"


def bootstrap_schema(database_path):
    with connect_local(database_path, timeout=5) as con:
        con.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            snapshot_timestamp_ms INTEGER NOT NULL,
            universe_json TEXT NOT NULL
        )""")


def persist_universe(database_path, universe):
    if universe.get("selection_contract", {}).get("exchange") != "BINANCE":
        raise ValueError("radar requires canonical Binance provenance")
    if not universe.get("selected"):
        raise ValueError("radar universe cannot be empty")
    payload = json.dumps(universe, sort_keys=True, separators=(",", ":"), allow_nan=False)
    with connect_local(database_path, timeout=5) as con:
        con.execute(f"""INSERT INTO {TABLE} VALUES(1,?,?)
            ON CONFLICT(singleton) DO UPDATE SET
                snapshot_timestamp_ms=excluded.snapshot_timestamp_ms,
                universe_json=excluded.universe_json
            WHERE excluded.snapshot_timestamp_ms>{TABLE}.snapshot_timestamp_ms""",
            (int(universe["snapshot_timestamp_ms"]), payload))


def load_universe(database_path, *, now_ms):
    # Read-only URI prevents a misconfigured observer from creating a second DB.
    with connect_local(Path(database_path).resolve().as_uri() + "?mode=ro",
                         uri=True, timeout=5) as con:
        row = con.execute(f"SELECT snapshot_timestamp_ms,universe_json FROM {TABLE} WHERE singleton=1").fetchone()
    if row is None or not 0 <= int(now_ms) - row[0] <= MAX_RADAR_AGE_MS:
        raise ValueError("broad radar missing, future or stale")
    universe = json.loads(row[1])
    if (universe.get("selection_contract", {}).get("exchange") != "BINANCE"
            or not universe.get("selected")):
        raise ValueError("broad radar canonical evidence unavailable")
    return universe
