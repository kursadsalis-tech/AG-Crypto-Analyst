"""Put exact-time external evidence on closed minutes without lookahead."""
from __future__ import annotations

from datetime import datetime, timezone


MINUTE_MS = 60_000
KINDS = frozenset({"NEWS", "MACRO", "ONCHAIN", "WHALE"})


def parse_exact_utc(value):
    text = str(value or "").strip()
    if not text or "T" not in text:
        raise ValueError("EXACT_INTRADAY_TIMESTAMP_REQUIRED")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def normalize_event(event):
    kind = str(event.get("kind") or "").upper()
    if kind not in KINDS:
        raise ValueError("UNSUPPORTED_EVIDENCE_KIND")
    occurred = parse_exact_utc(event.get("occurred_at"))
    available = parse_exact_utc(event.get("available_at"))
    if available < occurred:
        raise ValueError("AVAILABLE_BEFORE_OCCURRENCE")
    identity = str(event.get("event_id") or "").strip()
    source = str(event.get("source") or "").strip()
    if not identity or not source:
        raise ValueError("EVENT_ID_AND_SOURCE_REQUIRED")
    return {
        "event_id": identity, "kind": kind, "source": source,
        "occurred_at": occurred.isoformat(), "available_at": available.isoformat(),
        "available_at_ms": int(available.timestamp() * 1000),
        "symbols": sorted(set(event.get("symbols") or [])),
        "direction": str(event.get("direction") or "UNKNOWN"),
        "detail": dict(event.get("detail") or {}),
    }


def events_for_closed_minute(events, minute_end_timestamp_ms, now=None):
    minute_end = int(minute_end_timestamp_ms)
    minute_start = minute_end - MINUTE_MS
    now = now or datetime.now(timezone.utc)
    if minute_end > int(now.timestamp() * 1000):
        raise ValueError("MINUTE_NOT_CLOSED")
    normalized = [normalize_event(event) for event in events]
    selected = [event for event in normalized
                if minute_start <= event["available_at_ms"] < minute_end]
    selected.sort(key=lambda row: (row["available_at_ms"], row["kind"], row["event_id"]))
    counts = {kind: 0 for kind in sorted(KINDS)}
    for event in selected:
        counts[event["kind"]] += 1
    return {
        "contract_version": "CAUSAL_MINUTE_EVIDENCE_V1",
        "minute_start_timestamp_ms": minute_start,
        "minute_end_timestamp_ms": minute_end,
        "assignment_clock": "AVAILABLE_AT", "future_events_excluded": True,
        "event_count": len(selected), "counts": counts, "events": selected,
    }
