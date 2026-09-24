"""Quarantined research evidence from public copy-trading sources.

External traders are observations, never instructions.  This module preserves
the time at which AGCA first knew each fact, keeps exchange-specific metrics
separate, and forbids external evidence from changing the PAPER policy.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlparse


RESEARCH_VERSION = "EXTERNAL_TRADER_RESEARCH_V1"
ALLOWED_EXCHANGES = frozenset({"BYBIT", "OKX"})
ALLOWED_SOURCE_HOSTS = {
    "BYBIT": frozenset({"bybit.com", "www.bybit.com"}),
    "OKX": frozenset({"okx.com", "www.okx.com"}),
}
ALLOWED_SAMPLING_FRAMES = frozenset({
    "FULL_LEADERBOARD", "PREDECLARED_WATCHLIST", "ANECDOTAL_ONLY",
})
MIN_COHORT_TRADERS = 30
MIN_CLOSED_TRADES = 200
MIN_OBSERVATION_DAYS = 56


def _iso(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse(value):
    return datetime.fromisoformat(_iso(value))


def _canonical_hash(payload):
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ensure_tables(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS external_trader_source_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_trader_id TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            source_updated_at TEXT,
            sampling_frame TEXT NOT NULL,
            leaderboard_scope TEXT,
            leaderboard_rank INTEGER,
            leaderboard_size INTEGER,
            metrics_json TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            raw_sha256 TEXT NOT NULL,
            research_eligible INTEGER NOT NULL DEFAULT 0,
            exclusion_reason TEXT,
            UNIQUE(exchange,source_trader_id,observed_at,raw_sha256)
        );

        CREATE TABLE IF NOT EXISTS external_trader_trade_observations (
            observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange TEXT NOT NULL,
            source_trader_id TEXT NOT NULL,
            source_trade_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('LONG','SHORT')),
            entry_time TEXT,
            exit_time TEXT,
            first_observed_at TEXT NOT NULL,
            source_updated_at TEXT,
            entry_price REAL,
            exit_price REAL,
            notional REAL,
            leverage REAL,
            reported_pnl REAL,
            reported_roi_pct REAL,
            fees REAL,
            funding REAL,
            status TEXT NOT NULL CHECK(status IN ('OPEN','CLOSED')),
            sampling_frame TEXT NOT NULL,
            raw_sha256 TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            retrospective_only INTEGER NOT NULL DEFAULT 1,
            research_eligible INTEGER NOT NULL DEFAULT 0,
            exclusion_reason TEXT,
            UNIQUE(exchange,source_trader_id,source_trade_id,raw_sha256)
        );

        CREATE TABLE IF NOT EXISTS external_trader_learning_reviews (
            review_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            research_version TEXT NOT NULL,
            status TEXT NOT NULL,
            trader_count INTEGER NOT NULL,
            closed_trade_count INTEGER NOT NULL,
            observation_days INTEGER NOT NULL,
            evidence_json TEXT NOT NULL,
            policy_mutation_allowed INTEGER NOT NULL DEFAULT 0,
            promotion_allowed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(research_version,trader_count,closed_trade_count,observation_days)
        );

        CREATE INDEX IF NOT EXISTS ix_external_trader_status_identity
        ON external_trader_trade_observations(
            exchange,status,source_trader_id,source_trade_id,observation_id
        );

        CREATE INDEX IF NOT EXISTS ix_external_trader_eligible_closed_identity
        ON external_trader_trade_observations(
            exchange,source_trader_id,source_trade_id
        ) WHERE research_eligible=1 AND status='CLOSED';

        CREATE INDEX IF NOT EXISTS ix_external_trader_eligible_open_identity
        ON external_trader_trade_observations(
            exchange,source_trader_id,source_trade_id,observation_id,symbol,direction
        ) WHERE research_eligible=1 AND status='OPEN';

        CREATE INDEX IF NOT EXISTS ix_external_trader_eligible_snapshot_identity_time
        ON external_trader_source_snapshots(
            exchange,source_trader_id,observed_at
        ) WHERE research_eligible=1;
    """)


def _source_gate(payload):
    exchange = str(payload.get("exchange", "")).upper()
    frame = str(payload.get("sampling_frame", ""))
    if exchange not in ALLOWED_EXCHANGES:
        return False, "SOURCE_NOT_ALLOWLISTED"
    if frame not in ALLOWED_SAMPLING_FRAMES:
        return False, "SAMPLING_FRAME_MISSING"
    if frame == "ANECDOTAL_ONLY":
        return False, "ANECDOTAL_WINNER_EXAMPLE"
    if not payload.get("source_url") or not payload.get("source_trader_id"):
        return False, "PROVENANCE_MISSING"
    parsed_url = urlparse(str(payload["source_url"]))
    if parsed_url.scheme != "https" or parsed_url.hostname not in ALLOWED_SOURCE_HOSTS[exchange]:
        return False, "SOURCE_URL_NOT_ALLOWLISTED"
    if frame == "FULL_LEADERBOARD":
        rank = payload.get("leaderboard_rank")
        size = payload.get("leaderboard_size")
        if not isinstance(rank, int) or not isinstance(size, int) or not (1 <= rank <= size):
            return False, "LEADERBOARD_COHORT_INCOMPLETE"
    return True, None


def ingest_snapshot(con, payload, observed_at=None, *, commit=True, ensure=True):
    """Store a public profile snapshot without comparing exchange ROI formulas."""
    if ensure:
        ensure_tables(con)
    observed_at = _iso(observed_at or datetime.now(timezone.utc))
    eligible, reason = _source_gate(payload)
    raw_hash = _canonical_hash(payload)
    con.execute("""
        INSERT OR IGNORE INTO external_trader_source_snapshots(
            exchange,source_url,source_trader_id,observed_at,source_updated_at,
            sampling_frame,leaderboard_scope,leaderboard_rank,leaderboard_size,
            metrics_json,raw_json,raw_sha256,research_eligible,exclusion_reason
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        str(payload.get("exchange", "")).upper(), str(payload.get("source_url", "")),
        str(payload.get("source_trader_id", "")), observed_at,
        _iso(payload["source_updated_at"]) if payload.get("source_updated_at") else None,
        str(payload.get("sampling_frame", "")), payload.get("leaderboard_scope"),
        payload.get("leaderboard_rank"), payload.get("leaderboard_size"),
        json.dumps(payload.get("metrics", {}), ensure_ascii=False, sort_keys=True),
        json.dumps(payload, ensure_ascii=False, sort_keys=True), raw_hash,
        int(eligible), reason,
    ))
    if commit:
        con.commit()
    return {"stored": True, "research_eligible": eligible, "exclusion_reason": reason}


def ingest_trade(con, payload, first_observed_at=None, *, commit=True, ensure=True):
    """Store a trade with an immutable first-knowledge timestamp.

    Closed trades first seen after they closed remain retrospective examples;
    missing costs and extreme leverage also prevent research eligibility.
    """
    if ensure:
        ensure_tables(con)
    first_seen = _iso(first_observed_at or datetime.now(timezone.utc))
    prior = con.execute("""
        SELECT min(first_observed_at)
        FROM external_trader_trade_observations
        WHERE exchange=? AND source_trader_id=? AND source_trade_id=?
    """, (
        str(payload.get("exchange", "")).upper(),
        str(payload.get("source_trader_id", "")),
        str(payload.get("source_trade_id", "")),
    )).fetchone()[0]
    if prior and _parse(prior) < _parse(first_seen):
        first_seen = _iso(prior)
    eligible, reason = _source_gate(payload)
    status = str(payload.get("status", "")).upper()
    direction = str(payload.get("direction", "")).upper()
    entry_time = _iso(payload["entry_time"]) if payload.get("entry_time") else None
    exit_time = _iso(payload["exit_time"]) if payload.get("exit_time") else None
    retrospective = bool(exit_time and _parse(first_seen) >= _parse(exit_time))
    if status not in {"OPEN", "CLOSED"} or direction not in {"LONG", "SHORT"}:
        eligible, reason = False, "TRADE_FIELDS_INVALID"
    elif not entry_time or not payload.get("symbol") or not payload.get("source_trade_id"):
        eligible, reason = False, "TRADE_PROVENANCE_INCOMPLETE"
    elif status == "CLOSED" and (not exit_time or payload.get("reported_pnl") is None):
        eligible, reason = False, "CLOSED_RESULT_INCOMPLETE"
    elif status == "CLOSED" and any(
        payload.get(field) is None for field in ("entry_price", "exit_price", "notional")
    ):
        eligible, reason = False, "EXECUTION_VALUES_INCOMPLETE"
    elif payload.get("leverage") is not None and float(payload["leverage"]) > 5.0:
        eligible, reason = False, "EXTREME_LEVERAGE_EXCLUDED"
    elif payload.get("fees") is None or payload.get("funding") is None:
        eligible, reason = False, "COSTS_NOT_DISCLOSED"
    raw_hash = _canonical_hash(payload)
    con.execute("""
        INSERT OR IGNORE INTO external_trader_trade_observations(
            exchange,source_trader_id,source_trade_id,symbol,direction,
            entry_time,exit_time,first_observed_at,source_updated_at,
            entry_price,exit_price,notional,leverage,reported_pnl,
            reported_roi_pct,fees,funding,status,sampling_frame,raw_sha256,raw_json,
            retrospective_only,research_eligible,exclusion_reason
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        str(payload.get("exchange", "")).upper(), str(payload.get("source_trader_id", "")),
        str(payload.get("source_trade_id", "")), str(payload.get("symbol", "")).upper(),
        direction, entry_time, exit_time, first_seen,
        _iso(payload["source_updated_at"]) if payload.get("source_updated_at") else None,
        payload.get("entry_price"), payload.get("exit_price"), payload.get("notional"),
        payload.get("leverage"), payload.get("reported_pnl"),
        payload.get("reported_roi_pct"), payload.get("fees"), payload.get("funding"),
        status, str(payload.get("sampling_frame", "")), raw_hash,
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        int(retrospective), int(eligible), reason,
    ))
    if commit:
        con.commit()
    return {
        "stored": True, "research_eligible": eligible,
        "retrospective_only": retrospective, "exclusion_reason": reason,
    }


def run_review(con, now=None):
    """Assess evidence sufficiency; never create a trade or mutate policy."""
    ensure_tables(con)
    now = now or datetime.now(timezone.utc)
    trader_count = con.execute("""
        SELECT count(DISTINCT exchange || ':' || source_trader_id)
        FROM external_trader_source_snapshots WHERE research_eligible=1
    """).fetchone()[0]
    closed_count = con.execute("""
        SELECT count(DISTINCT exchange || ':' || source_trader_id || ':' || source_trade_id)
        FROM external_trader_trade_observations
        WHERE research_eligible=1 AND status='CLOSED'
    """).fetchone()[0]
    bounds = con.execute("""
        SELECT min(observed_at),max(observed_at)
        FROM external_trader_source_snapshots WHERE research_eligible=1
    """).fetchone()
    days = 0
    if bounds[0] and bounds[1]:
        days = max(0, (_parse(bounds[1]) - _parse(bounds[0])).days)
    if trader_count < MIN_COHORT_TRADERS:
        status = "COLLECTING_UNBIASED_TRADER_COHORT"
    elif closed_count < MIN_CLOSED_TRADES:
        status = "COLLECTING_CLOSED_TRADE_EVIDENCE"
    elif days < MIN_OBSERVATION_DAYS:
        status = "COLLECTING_FORWARD_TIME_COVERAGE"
    else:
        status = "EXTERNAL_EVIDENCE_READY_FOR_HUMAN_RESEARCH_REVIEW"
    evidence = {
        "basis": "PUBLIC_COPY_TRADER_OBSERVATIONS_QUARANTINED",
        "traders": trader_count, "closed_trades": closed_count,
        "observation_days": days,
        "controls": {
            "winner_only_samples_eligible": False,
            "cross_exchange_roi_comparison": False,
            "retrospective_trade_is_forward_signal": False,
            "maximum_research_leverage": 5.0,
            "cost_disclosure_required": True,
            "automatic_copying": False,
            "automatic_policy_mutation": False,
            "automatic_promotion": False,
            "real_orders": False,
        },
    }
    con.execute("""
        INSERT OR IGNORE INTO external_trader_learning_reviews(
            created_at,research_version,status,trader_count,closed_trade_count,
            observation_days,evidence_json,policy_mutation_allowed,promotion_allowed
        ) VALUES(?,?,?,?,?,?,?,0,0)
    """, (
        _iso(now), RESEARCH_VERSION, status, trader_count, closed_count, days,
        json.dumps(evidence, ensure_ascii=False, sort_keys=True),
    ))
    con.commit()
    return {**evidence, "status": status, "promotion_allowed": False}
