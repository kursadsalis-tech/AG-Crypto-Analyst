"""Durable, deduplicated health transitions for the real-time PAPER manager."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from database.runtime_store import connect
from runtime.cloud_attestation import paper_realtime_health


DB_PATH = os.getenv("AGCA_STATE_DB", "/var/lib/agca/crypto_data.db")
STATE_PATH = Path("/var/lib/agca/paper-realtime-alert-state.json")


def now():
    return datetime.now(timezone.utc)


def ensure_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS paper_operator_alerts(
        alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        component TEXT NOT NULL,
        state TEXT NOT NULL,
        reason TEXT NOT NULL,
        acknowledged INTEGER NOT NULL DEFAULT 0,
        real_order_allowed INTEGER NOT NULL DEFAULT 0
    )""")


def main():
    con = connect(DB_PATH, timeout=30)
    try:
        ensure_table(con)
        open_count = con.execute(
            "SELECT COUNT(*) FROM paper_agent_positions WHERE status='OPEN'"
        ).fetchone()[0]
        health = paper_realtime_health(open_positions=open_count)
        healthy = health["state"] == "CURRENT"
        previous = {}
        try:
            previous = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        prior_healthy = previous.get("healthy")
        if prior_healthy is None or bool(prior_healthy) != healthy:
            state = "RECOVERED" if healthy else "FAULT"
            reason = f"state={health['state']} status={health['status']} open={open_count}"
            con.execute("""INSERT INTO paper_operator_alerts(
                created_at,component,state,reason,real_order_allowed
            ) VALUES(?,?,?,?,0)""", (
                now().isoformat(), "PAPER_REALTIME_MANAGER", state, reason,
            ))
            con.commit()
        payload = {"healthy": healthy, "checked_at": now().isoformat(),
                   "state": health["state"], "status": health["status"]}
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = STATE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, STATE_PATH)
        print(json.dumps(payload, sort_keys=True))
    finally:
        con.close()


if __name__ == "__main__":
    main()
