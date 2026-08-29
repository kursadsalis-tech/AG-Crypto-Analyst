#!/usr/bin/env python3
"""Observe exact-time FOMC releases with a bounded, schedule-aware cadence."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data import federal_reserve_exact_events as fed
from data.causal_minute_evidence import parse_exact_utc
from database.causal_evidence_store import ensure_macro_table, save_exact_macro_event
from database.runtime_store import connect


HEALTH_VERSION = "AGCA_EXACT_MACRO_OBSERVER_HEALTH_V1"
NORMAL_REFRESH = timedelta(hours=24)
ERROR_RETRY = timedelta(minutes=15)
WINDOW_BEFORE = timedelta(minutes=15)
WINDOW_AFTER = timedelta(minutes=90)
PAGE_GRACE = timedelta(minutes=10)


def write_json_atomic(path, payload):
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=output.parent,
        prefix=output.name + ".", delete=False,
    ) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.chmod(temporary, 0o600)
    os.replace(temporary, output)


def load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _schedule(state):
    values = []
    for value in state.get("scheduled_release_times") or []:
        try:
            values.append(parse_exact_utc(value))
        except ValueError:
            continue
    return values


def active_release(schedule, now):
    candidates = [value for value in schedule
                  if value - WINDOW_BEFORE <= now <= value + WINDOW_AFTER]
    return min(candidates, key=lambda value: abs((now - value).total_seconds())) \
        if candidates else None


def refresh_due(state, now):
    release = active_release(_schedule(state), now)
    if release is not None:
        return True
    try:
        checked = parse_exact_utc(state.get("calendar_checked_at"))
    except ValueError:
        try:
            attempted = parse_exact_utc(state.get("calendar_attempted_at"))
        except ValueError:
            return True
        return now - attempted >= ERROR_RETRY
    return now - checked >= NORMAL_REFRESH


def observe(database, health_path, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if os.getenv("AGCA_EXECUTION_MODE", "PAPER_ONLY") != "PAPER_ONLY":
        raise RuntimeError("exact macro observer accepts PAPER_ONLY exclusively")
    previous = load_state(health_path)
    schedule = _schedule(previous)
    calendar_html = None
    source_status = "NOT_DUE"
    error = None
    events = []
    page_url = None
    if refresh_due(previous, now):
        try:
            calendar_html = fed.fetch_text(fed.CALENDAR_URL)
            schedule = fed.decision_release_times(calendar_html, now.year)
            source_status = "CALENDAR_CURRENT"
        except Exception as exc:
            source_status = "CALENDAR_ERROR"
            error = f"{type(exc).__name__}: {exc}"
    release = active_release(schedule, now)
    if release is not None and calendar_html is not None:
        page_url = fed.meeting_page_for_date(calendar_html, release)
        if page_url:
            try:
                page_html = fed.fetch_text(page_url)
                parsed = fed.parse_meeting_page(page_html, page_url)
                events = [event for event in parsed
                          if abs((parse_exact_utc(event["available_at"]) - release)
                                 .total_seconds()) <= 60]
                source_status = "OFFICIAL_RELEASE_PAGE_OBSERVED"
            except Exception as exc:
                source_status = "RELEASE_PAGE_ERROR"
                error = f"{type(exc).__name__}: {exc}"

    with connect(database) as con:
        ensure_macro_table(con)
        for event in events:
            save_exact_macro_event(con, event, observed_at=now.isoformat())
        con.commit()
        stored = con.execute(
            "SELECT COUNT(*) FROM exact_time_macro_events"
        ).fetchone()[0]

    if error:
        status = "DEGRADED"
    elif release is not None and not page_url:
        status = "WAITING_FOR_OFFICIAL_RELEASE" if now <= release + PAGE_GRACE else "DEGRADED"
    else:
        status = "HEALTHY"
    payload = {
        "health_contract_version": HEALTH_VERSION,
        "status": status, "completed_at": now.isoformat(),
        "execution_mode": "PAPER_ONLY", "source_status": source_status,
        "calendar_checked_at": (
            now.isoformat() if calendar_html is not None
            else previous.get("calendar_checked_at")
        ),
        "calendar_attempted_at": (
            now.isoformat() if source_status != "NOT_DUE"
            else previous.get("calendar_attempted_at")
        ),
        "scheduled_release_times": [value.isoformat() for value in schedule],
        "active_release_at": release.isoformat() if release else None,
        "official_page_url": page_url,
        "events_observed": len(events), "stored_event_count": int(stored),
        "error": error, "raw_payloads_persisted": False,
        "may_send_real_orders": False,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["health_sha256"] = hashlib.sha256(canonical).hexdigest()
    write_json_atomic(health_path, payload)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--health-output", required=True)
    parser.add_argument("--lock-file", required=True)
    args = parser.parse_args()
    lock_path = Path(args.lock_file)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit("Another exact macro observer owns the lock") from exc
        result = observe(args.database, args.health_output)
    print(json.dumps({
        "status": result["status"], "source_status": result["source_status"],
        "events_observed": result["events_observed"],
    }, sort_keys=True))
    return result


if __name__ == "__main__":
    main()
