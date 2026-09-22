"""Tamper-evident cloud runtime attestations mirrored to Supabase."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import string
from datetime import datetime, timezone
from pathlib import Path

from database.runtime_store import connect as runtime_connect
from paper_trading.crypto_profit_agent import FORWARD_COHORT
from paper_trading.governance import AUTHORIZED_POLICY_VERSION, validate_agent_authority


DB_NAME = os.getenv("AGCA_DATABASE_PATH", "crypto_data.db")
ATTESTATION_VERSION = "AGCA_RUNTIME_ATTESTATION_V3_REALTIME_PAPER"
MARKET_BEHAVIOR_HEALTH_VERSION = "AGCA_MARKET_BEHAVIOR_HEALTH_V1"
EXACT_MACRO_HEALTH_VERSION = "AGCA_EXACT_MACRO_OBSERVER_HEALTH_V1"
SQLITE_INTEGRITY_CACHE_VERSION = "AGCA_SQLITE_INTEGRITY_CACHE_V1"


def runtime_source_state(repo_path=None):
    """Best-effort identity of the exact git checkout used by the cloud runtime."""
    root = Path(
        repo_path
        or os.getenv("AGCA_RUNTIME_SOURCE_DIR", "")
        or Path(__file__).resolve().parents[1]
    )
    base = {"git_sha": None, "git_dirty": None}
    try:
        sha = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False, capture_output=True, text=True, timeout=2,
        )
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            check=False, capture_output=True, text=True, timeout=2,
        )
        if sha.returncode != 0 or dirty.returncode != 0:
            return base
        value = sha.stdout.strip()
        if len(value) != 40 or any(ch not in string.hexdigits for ch in value):
            return base
        return {"git_sha": value.lower(), "git_dirty": bool(dirty.stdout.strip())}
    except (OSError, subprocess.SubprocessError):
        return base


def paper_realtime_health(now=None, path=None, open_positions=0):
    now = now or datetime.now(timezone.utc)
    path = Path(path or os.getenv(
        "AGCA_PAPER_REALTIME_HEALTH", "/var/lib/agca/paper-realtime-health.json"
    ))
    base = {"state": "MISSING", "status": None, "age_seconds": None,
            "required": int(open_positions) > 0, "real_order_allowed": False}
    if not path.is_file():
        return base
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        written = datetime.fromisoformat(str(raw["at"]).replace("Z", "+00:00"))
        age = max(0.0, (now - written.astimezone(timezone.utc)).total_seconds())
        valid = (
            raw.get("execution_mode") == "PAPER_ONLY"
            and raw.get("real_order_allowed") is False
            and raw.get("status") in {"CONNECTED", "IDLE", "STARTING", "DEGRADED"}
        )
        max_age = float(os.getenv("AGCA_PAPER_REALTIME_HEALTH_MAX_AGE_SECONDS", "30"))
        state = "INVALID" if not valid else ("STALE" if age > max_age else "CURRENT")
        if int(open_positions) > 0 and raw.get("status") != "CONNECTED" and state == "CURRENT":
            state = "UNHEALTHY"
        return {"state": state, "status": raw.get("status"),
                "age_seconds": round(age, 3), "required": int(open_positions) > 0,
                "real_order_allowed": False}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {**base, "state": "INVALID"}


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def ensure_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS runtime_attestations (
            attestation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL UNIQUE,
            attestation_version TEXT NOT NULL,
            worker_id TEXT NOT NULL,
            worker_status TEXT NOT NULL,
            execution_mode TEXT NOT NULL,
            may_send_real_orders INTEGER NOT NULL,
            sqlite_quick_check TEXT NOT NULL,
            validation_cohort TEXT NOT NULL,
            pending_count INTEGER NOT NULL,
            open_count INTEGER NOT NULL,
            closed_count INTEGER NOT NULL,
            cancelled_count INTEGER NOT NULL,
            initial_capital REAL NOT NULL,
            cash REAL NOT NULL,
            realized_pnl REAL NOT NULL,
            total_fees REAL NOT NULL,
            realtime_manager_state TEXT,
            behavior_observer_state TEXT,
            macro_observer_state TEXT,
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL
        )
    """)


def _status(result):
    return result.get("status") if isinstance(result, dict) else None


def sqlite_integrity_cache(now=None, path=None):
    """Validate the last independently produced full SQLite quick-check."""
    now = now or datetime.now(timezone.utc)
    path = Path(path or os.getenv(
        "AGCA_SQLITE_INTEGRITY_CACHE_PATH", "/var/lib/agca/sqlite-integrity-health.json"
    ))
    if not path.is_file():
        return {"state": "MISSING", "result": None, "checked_at": None, "age_seconds": None}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        claimed = str(raw.pop("health_sha256"))
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        checked = datetime.fromisoformat(str(raw["checked_at"]).replace("Z", "+00:00"))
        age = max(0.0, (now - checked.astimezone(timezone.utc)).total_seconds())
        valid = (
            raw.get("health_contract_version") == SQLITE_INTEGRITY_CACHE_VERSION
            and raw.get("result") == "ok"
            and hashlib.sha256(canonical).hexdigest() == claimed
        )
        max_age = float(os.getenv("AGCA_SQLITE_INTEGRITY_MAX_AGE_SECONDS", "90000"))
        state = "INVALID" if not valid else ("STALE" if age > max_age else "CURRENT")
        return {"state": state, "result": raw.get("result"),
                "checked_at": checked.isoformat(), "age_seconds": round(age, 3)}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {"state": "INVALID", "result": None, "checked_at": None, "age_seconds": None}


def market_behavior_health(now=None, path=None):
    """Read the observer's atomic health record without granting it authority."""
    now = now or datetime.now(timezone.utc)
    path = Path(path or os.getenv(
        "AGCA_MARKET_BEHAVIOR_HEALTH_PATH",
        "/var/lib/agca/market-behavior-health.json",
    ))
    base = {
        "contract_version": MARKET_BEHAVIOR_HEALTH_VERSION,
        "state": "MISSING", "status": None, "completed_at": None,
        "age_seconds": None, "minute_end_timestamp_ms": None,
        "row_count": 0, "eligible_row_count": 0, "report_sha256": None,
        "raw_market_payloads_persisted": None,
        "may_send_real_orders": False,
    }
    if not path.is_file():
        return base
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        completed = datetime.fromisoformat(
            str(raw["completed_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        age = max(0.0, (now - completed).total_seconds())
        max_age = float(os.getenv("AGCA_MARKET_BEHAVIOR_HEALTH_MAX_AGE_SECONDS", "180"))
        claimed_hash = str(raw.get("health_sha256") or "")
        unhashed = dict(raw)
        unhashed.pop("health_sha256", None)
        canonical = json.dumps(
            unhashed, sort_keys=True, separators=(",", ":")
        ).encode()
        valid = (
            raw.get("health_contract_version") == MARKET_BEHAVIOR_HEALTH_VERSION
            and raw.get("execution_mode") == "PAPER_ONLY"
            and raw.get("may_send_real_orders") is False
            and raw.get("raw_market_payloads_persisted") is False
            and isinstance(raw.get("report_sha256"), str)
            and len(raw["report_sha256"]) == 64
            and all(character in string.hexdigits for character in raw["report_sha256"])
            and int(raw.get("row_count") or 0) >= int(raw.get("eligible_row_count") or 0) >= 0
            and hashlib.sha256(canonical).hexdigest() == claimed_hash
        )
        state = "INVALID" if not valid else ("STALE" if age > max_age else "CURRENT")
        return {
            **base, "state": state, "status": raw.get("status"),
            "completed_at": completed.isoformat(), "age_seconds": round(age, 3),
            "minute_end_timestamp_ms": raw.get("minute_end_timestamp_ms"),
            "row_count": int(raw.get("row_count") or 0),
            "eligible_row_count": int(raw.get("eligible_row_count") or 0),
            "report_sha256": raw.get("report_sha256"),
            "raw_market_payloads_persisted": raw.get("raw_market_payloads_persisted"),
            "may_send_real_orders": bool(raw.get("may_send_real_orders", False)),
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {**base, "state": "INVALID"}


def exact_macro_health(now=None, path=None):
    now = now or datetime.now(timezone.utc)
    path = Path(path or os.getenv(
        "AGCA_EXACT_MACRO_HEALTH_PATH", "/var/lib/agca/exact-macro-health.json"
    ))
    base = {
        "contract_version": EXACT_MACRO_HEALTH_VERSION,
        "state": "MISSING", "status": None, "completed_at": None,
        "age_seconds": None, "source_status": None, "active_release_at": None,
        "events_observed": 0, "stored_event_count": 0,
        "may_send_real_orders": False,
    }
    if not path.is_file():
        return base
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        completed = datetime.fromisoformat(
            str(raw["completed_at"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        age = max(0.0, (now - completed).total_seconds())
        claimed_hash = str(raw.get("health_sha256") or "")
        unhashed = dict(raw)
        unhashed.pop("health_sha256", None)
        canonical = json.dumps(
            unhashed, sort_keys=True, separators=(",", ":")
        ).encode()
        valid = (
            raw.get("health_contract_version") == EXACT_MACRO_HEALTH_VERSION
            and raw.get("execution_mode") == "PAPER_ONLY"
            and raw.get("may_send_real_orders") is False
            and raw.get("raw_payloads_persisted") is False
            and hashlib.sha256(canonical).hexdigest() == claimed_hash
        )
        max_age = float(os.getenv("AGCA_EXACT_MACRO_HEALTH_MAX_AGE_SECONDS", "180"))
        state = "INVALID" if not valid else ("STALE" if age > max_age else "CURRENT")
        return {
            **base, "state": state, "status": raw.get("status"),
            "completed_at": completed.isoformat(), "age_seconds": round(age, 3),
            "source_status": raw.get("source_status"),
            "active_release_at": raw.get("active_release_at"),
            "events_observed": int(raw.get("events_observed") or 0),
            "stored_event_count": int(raw.get("stored_event_count") or 0),
            "may_send_real_orders": False,
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return {**base, "state": "INVALID"}


def build(
    con, worker_status, last_results, now=None, behavior_health_path=None,
    exact_macro_health_path=None, paper_realtime_health_path=None,
):
    now = now or datetime.now(timezone.utc)
    mode = os.getenv("AGCA_EXECUTION_MODE", "PAPER_ONLY")
    integrity_evidence = None
    if os.getenv("AGCA_ATTESTATION_USE_INTEGRITY_CACHE", "0") == "1":
        integrity_evidence = sqlite_integrity_cache(now)
        integrity = (
            integrity_evidence["result"]
            if integrity_evidence["state"] == "CURRENT" else integrity_evidence["state"]
        )
    else:
        integrity = con.execute("PRAGMA quick_check").fetchone()[0]
    counts = {row[0]: int(row[1]) for row in con.execute("""
        SELECT status,COUNT(*) FROM paper_agent_positions
        WHERE validation_cohort=? AND evidence_eligible=1 GROUP BY status
    """, (FORWARD_COHORT,)).fetchall()}
    portfolio = con.execute("""
        SELECT initial_capital,cash,realized_pnl,total_fees,validation_cohort
        FROM paper_agent_portfolio WHERE portfolio_id=1
    """).fetchone()
    if not portfolio:
        raise RuntimeError("PAPER_PORTFOLIO_MISSING")
    authority = validate_agent_authority(
        execution_mode=mode,
        policy_version=AUTHORIZED_POLICY_VERSION,
        initial_capital=float(portfolio[0]),
    )
    payload = {
        "attestation_version": ATTESTATION_VERSION,
        "created_at": iso(now),
        "worker_id": socket.gethostname(),
        "worker_status": worker_status,
        "execution_mode": mode,
        "may_send_real_orders": bool(authority["may_send_real_orders"]),
        "runtime_source": runtime_source_state(),
        "sqlite_quick_check": str(integrity),
        "sqlite_integrity_evidence": integrity_evidence,
        "validation_cohort": str(portfolio[4]),
        "positions": {
            "pending": counts.get("PENDING_ENTRY", 0),
            "open": counts.get("OPEN", 0),
            "closed": counts.get("CLOSED", 0),
            "cancelled": counts.get("CANCELLED", 0),
        },
        "portfolio": {
            "initial_capital": float(portfolio[0]),
            "cash": float(portfolio[1]),
            "realized_pnl": float(portfolio[2]),
            "total_fees": float(portfolio[3]),
        },
        "market_behavior_observer": market_behavior_health(
            now=now, path=behavior_health_path
        ),
        "exact_macro_observer": exact_macro_health(
            now=now, path=exact_macro_health_path
        ),
        "paper_realtime_manager": paper_realtime_health(
            now=now, path=paper_realtime_health_path,
            open_positions=counts.get("OPEN", 0),
        ),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return payload, encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def record(worker_status, last_results, now=None, database=DB_NAME):
    con = runtime_connect(database, timeout=30)
    try:
        con.execute("PRAGMA busy_timeout=30000")
        ensure_table(con)
        payload, encoded, digest = build(con, worker_status, last_results, now)
        positions = payload["positions"]
        portfolio = payload["portfolio"]
        con.execute("""
            INSERT INTO runtime_attestations(
                created_at,attestation_version,worker_id,worker_status,
                execution_mode,may_send_real_orders,sqlite_quick_check,
                validation_cohort,pending_count,open_count,closed_count,
                cancelled_count,initial_capital,cash,realized_pnl,total_fees,
                realtime_manager_state,behavior_observer_state,macro_observer_state,
                payload_json,payload_sha256
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            payload["created_at"], ATTESTATION_VERSION, payload["worker_id"],
            payload["worker_status"], payload["execution_mode"],
            int(payload["may_send_real_orders"]), payload["sqlite_quick_check"],
            payload["validation_cohort"], positions["pending"], positions["open"],
            positions["closed"], positions["cancelled"], portfolio["initial_capital"],
            portfolio["cash"], portfolio["realized_pnl"], portfolio["total_fees"],
            payload["paper_realtime_manager"]["state"],
            payload["market_behavior_observer"]["state"],
            payload["exact_macro_observer"]["state"], encoded, digest,
        ))
        con.commit()
        return {"payload": payload, "sha256": digest,
                "mirrored": con.remote_mirror_result.succeeded_for(
                    "runtime_attestations"
                )}
    finally:
        con.close()


def main():
    health_path = Path(os.getenv(
        "AGCA_PAPER_REALTIME_HEALTH", "/var/lib/agca/paper-realtime-health.json"
    ))
    health = json.loads(health_path.read_text(encoding="utf-8"))
    service = os.getenv("AGCA_WORKER_SERVICE", "agca-paper-realtime-worker.service")
    active = subprocess.run(
        ["systemctl", "is-active", "--quiet", service], check=False,
    ).returncode == 0
    now = datetime.now(timezone.utc)
    health_status = str(health.get("status") or "DEGRADED")
    health_at = datetime.fromisoformat(str(health.get("at")).replace("Z", "+00:00"))
    age = (now - health_at.astimezone(timezone.utc)).total_seconds()
    max_age = float(os.getenv("AGCA_ATTESTATION_HEALTH_MAX_AGE_SECONDS", "30"))
    if not active:
        worker_status = "STOPPED"
    elif age > max_age:
        worker_status = "STALE"
    else:
        worker_status = health_status
    result = record(worker_status, {}, now=now)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    main()
