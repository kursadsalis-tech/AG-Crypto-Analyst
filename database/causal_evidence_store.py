"""Project stored exact-time evidence into the local closed-minute timeline."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from data.causal_minute_evidence import events_for_closed_minute, parse_exact_utc
from database.runtime_store import connect


MACRO_TABLE = "exact_time_macro_events"


def _tables(con):
    return {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}


def _sqlite_utc(value):
    """Normalize SQLite CURRENT_TIMESTAMP, whose documented clock is UTC."""
    text = str(value or "").strip()
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1) + "+00:00"
    return parse_exact_utc(text)


def _later(first, second, second_is_sqlite_utc=False):
    right = _sqlite_utc(second) if second_is_sqlite_utc else parse_exact_utc(second)
    return max(parse_exact_utc(first), right).isoformat()


def _symbols(value):
    if not value:
        return []
    try:
        decoded = json.loads(value)
        if isinstance(decoded, list):
            return [str(item) for item in decoded]
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return [item.strip() for item in str(value).split(",") if item.strip()]


def ensure_macro_table(con):
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {MACRO_TABLE} (
            event_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            event_name TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            detail_json TEXT NOT NULL
        )
    """)


def save_exact_macro_event(con, event):
    occurred = parse_exact_utc(event["occurred_at"])
    available = parse_exact_utc(event["available_at"])
    if available < occurred:
        raise ValueError("AVAILABLE_BEFORE_OCCURRENCE")
    ensure_macro_table(con)
    con.execute(f"""
        INSERT INTO {MACRO_TABLE}(
            event_id,source,event_name,occurred_at,available_at,detail_json
        ) VALUES(?,?,?,?,?,?)
        ON CONFLICT(event_id) DO NOTHING
    """, (
        str(event["event_id"]), str(event["source"]), str(event["event_name"]),
        occurred.isoformat(), available.isoformat(),
        json.dumps(event.get("detail") or {}, sort_keys=True, separators=(",", ":")),
    ))


def load_events(con, start_at=None, end_at=None):
    tables = _tables(con)
    events = []
    window = (start_at, end_at) if start_at is not None and end_at is not None else None
    if "crypto_news" in tables:
        has_analysis = "crypto_news_analysis" in tables
        join = "LEFT JOIN crypto_news_analysis a ON a.news_id=n.id" if has_analysis else ""
        coins = "a.coins" if has_analysis else "NULL"
        direction = "a.direction" if has_analysis else "'UNKNOWN'"
        news_where = ""
        params = ()
        if window:
            news_where = """WHERE julianday(n.published_at) < julianday(?)
                AND julianday(n.collected_at) < julianday(?)
                AND (julianday(n.published_at) >= julianday(?)
                     OR julianday(n.collected_at) >= julianday(?))"""
            params = (end_at, end_at, start_at, start_at)
        for row in con.execute(f"""
            SELECT n.id,n.source,n.title,n.published_at,n.collected_at,
                   {coins},{direction} FROM crypto_news n {join}
            {news_where or 'WHERE n.published_at IS NOT NULL AND n.collected_at IS NOT NULL'}
        """, params).fetchall():
            try:
                available = _later(row[3], row[4], second_is_sqlite_utc=True)
            except ValueError:
                continue
            events.append({
                "event_id": f"news:{row[0]}", "kind": "NEWS", "source": row[1],
                "occurred_at": row[3], "available_at": available,
                "symbols": _symbols(row[5]), "direction": row[6] or "UNKNOWN",
                "detail": {"title": row[2]},
            })
    if "onchain_observations" in tables:
        where = " WHERE julianday(collected_at)>=julianday(?) AND julianday(collected_at)<julianday(?)" if window else ""
        for row in con.execute("""
            SELECT id,collected_at,source,chain,metric,value,unit
            FROM onchain_observations
        """ + where, window or ()).fetchall():
            events.append({
                "event_id": f"onchain:{row[0]}", "kind": "ONCHAIN",
                "source": row[2], "occurred_at": row[1], "available_at": row[1],
                "symbols": [], "detail": {
                    "chain": row[3], "metric": row[4], "value": row[5], "unit": row[6],
                },
            })
    if "whale_events" in tables:
        where = " WHERE julianday(observed_at)>=julianday(?) AND julianday(observed_at)<julianday(?)" if window else ""
        for row in con.execute("""
            SELECT id,source,block_time,observed_at,asset,direction,
                   event_type,usd_estimate FROM whale_events
        """ + where, window or ()).fetchall():
            try:
                available = _later(row[2], row[3])
            except ValueError:
                continue
            events.append({
                "event_id": f"whale:{row[0]}", "kind": "WHALE", "source": row[1],
                "occurred_at": row[2], "available_at": available,
                "symbols": [row[4]], "direction": row[5],
                "detail": {"event_type": row[6], "usd_estimate": row[7]},
            })
    if MACRO_TABLE in tables:
        where = " WHERE julianday(available_at)>=julianday(?) AND julianday(available_at)<julianday(?)" if window else ""
        for row in con.execute(f"""
            SELECT event_id,source,event_name,occurred_at,available_at,detail_json
            FROM {MACRO_TABLE}
        """ + where, window or ()).fetchall():
            events.append({
                "event_id": f"macro:{row[0]}", "kind": "MACRO", "source": row[1],
                "occurred_at": row[3], "available_at": row[4], "symbols": [],
                "detail": {"event_name": row[2], **json.loads(row[5])},
            })
    return events


def evidence_for_closed_minute(database_path, minute_end_timestamp_ms, now=None):
    minute_end = datetime.fromtimestamp(
        int(minute_end_timestamp_ms) / 1000, timezone.utc
    )
    minute_start = datetime.fromtimestamp(
        (int(minute_end_timestamp_ms) - 60_000) / 1000, timezone.utc
    )
    with connect(database_path) as con:
        events = load_events(con, minute_start.isoformat(), minute_end.isoformat())
    return events_for_closed_minute(
        events, minute_end_timestamp_ms,
        now=now or datetime.now(timezone.utc),
    )
