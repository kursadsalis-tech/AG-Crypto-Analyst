"""Run the market Risk Manager at a safe live-product cadence."""
from __future__ import annotations

import sqlite3
from database.sqlite_policy import is_sqlite_busy
import time
from database.runtime_store import connect as runtime_connect
from datetime import datetime, timedelta, timezone

from risk_manager import risk_manager
from data.binance_technical_bars import bootstrap_missing as bootstrap_technical_bars


DB_NAME = risk_manager.DB_NAME
AGENT_NAME = "risk_manager"
MIN_INTERVAL = timedelta(minutes=15)


def utcnow():
    return datetime.now(timezone.utc)


def parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def ensure_state_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS agent_run_state (
            agent_name TEXT PRIMARY KEY,
            last_success_at TEXT,
            last_status TEXT,
            last_message TEXT
        )
    """)
    con.commit()


def is_due(con, now):
    row = con.execute(
        "SELECT last_success_at FROM agent_run_state WHERE agent_name=?",
        (AGENT_NAME,),
    ).fetchone()
    last_success = parse_time(row[0]) if row else None
    return last_success is None or now - last_success >= MIN_INTERVAL


def save_state(con, status, message, success_at=None):
    con.execute("""
        INSERT INTO agent_run_state(agent_name,last_success_at,last_status,last_message)
        VALUES (?,?,?,?)
        ON CONFLICT(agent_name) DO UPDATE SET
            last_success_at=COALESCE(
                excluded.last_success_at,
                agent_run_state.last_success_at
            ),
            last_status=excluded.last_status,
            last_message=excluded.last_message
    """, (
        AGENT_NAME,
        success_at.isoformat() if success_at else None,
        status,
        message,
    ))
    con.commit()


def runtime_connection():
    """Use the same bounded SQLite writer wait as the risk snapshot path."""
    con = runtime_connect(DB_NAME, timeout=30)
    con.execute("PRAGMA busy_timeout=250")
    return con


def persist_state(status, message, success_at=None, *, attempts=3):
    """Write cycle state through a fresh bounded connection."""
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        con = None
        try:
            con = runtime_connection()
            ensure_state_table(con)
            save_state(con, status, message, success_at)
            return
        except sqlite3.OperationalError as exc:
            if (
                not is_sqlite_busy(exc)
                or attempt >= attempts
            ):
                raise
        finally:
            if con is not None:
                con.close()
        time.sleep(min(0.5 * attempt, 2.0))


def main(force=False):
    now = utcnow()

    con = runtime_connection()
    try:
        ensure_state_table(con)
        due = force or is_due(con, now)
    finally:
        con.close()
    if not due:
        print("Risk Manager: 15 dakikalık aralık dolmadı; bu cycle atlandı.")
        return {"status": "SKIPPED"}

    try:
        result = risk_manager.main()

        con = runtime_connection()
        try:
            behavior_symbols = risk_manager.behavior_risk_symbols(con, now)
        finally:
            con.close()

        try:
            technical_bootstrap = bootstrap_technical_bars(
                DB_NAME, behavior_symbols, now=now, max_workers=4
            )
        except Exception as bootstrap_exc:
            technical_bootstrap = {
                "needed": len(behavior_symbols),
                "ready": 0,
                "bars": 0,
                "errors": {"SUPPORT_CYCLE": type(bootstrap_exc).__name__},
            }

        result["technical_bar_bootstrap"] = technical_bootstrap
        source_errors = result.get("source_errors") or {}
        if result.get("market_state") == "DATA_BLOCKED":
            status = "DATA_BLOCKED"
        else:
            status = "PARTIAL" if source_errors else "SUCCESS"
        message = (
            f"snapshot={result['snapshot_id']} | "
            f"market_state={result['market_state']} | "
            f"coverage={result['coverage_count']}/{result['coverage_total']} | "
            f"market_stress={result['market_stress_count']} | "
            f"asset_blocks={result['asset_block_count']} | "
            f"source_errors={len(source_errors)} | "
            f"technical_contexts={result.get('technical_context_count', 0)} | "
            f"technical_ms={result.get('technical_context_compute_ms', 0.0):.3f} | "
            f"technical_errors={len(result.get('technical_context_errors') or {})} | "
            f"regime_errors={len(result.get('technical_regime_errors') or {})} | "
            f"bar_bootstrap={technical_bootstrap.get('ready', 0)}/"
            f"{technical_bootstrap.get('needed', 0)} | "
            f"bar_rows={technical_bootstrap.get('bars', 0)}"
        )
        persist_state(status, message, now if status == "SUCCESS" else None)
        print(f"Risk Manager cycle: {status} | {message}")
        return {"status": status, "message": message, "result": result}
    except Exception as exc:
        message = f"{type(exc).__name__}:{exc}"
        persist_state("ERROR", message)
        print(f"Risk Manager cycle: HATA -> {message}")
        return {"status": "ERROR", "message": message}


if __name__ == "__main__":
    main(force=True)
