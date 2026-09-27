"""Outcome-blind context hypotheses for quarantined external trader research."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone


VERSION = "EXTERNAL_TRADER_RATIONALE_V1"


def _iso(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _table_exists(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def ensure_tables(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS external_trader_entry_contexts (
            exchange TEXT NOT NULL,
            source_trader_id TEXT NOT NULL,
            source_trade_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_time TEXT NOT NULL,
            first_observed_at TEXT NOT NULL,
            context_cutoff_at TEXT NOT NULL,
            context_status TEXT NOT NULL,
            context_json TEXT NOT NULL,
            rationale_hypotheses_json TEXT NOT NULL,
            context_sha256 TEXT NOT NULL,
            retrospective_reconstruction INTEGER NOT NULL DEFAULT 1,
            outcome_known_at_capture INTEGER NOT NULL DEFAULT 0,
            intent_claimed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            PRIMARY KEY(exchange,source_trader_id,source_trade_id)
        );

        CREATE TABLE IF NOT EXISTS external_trader_rationale_reviews (
            exchange TEXT NOT NULL,
            source_trader_id TEXT NOT NULL,
            source_trade_id TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            outcome_class TEXT NOT NULL,
            reported_pnl REAL NOT NULL,
            assessment_json TEXT NOT NULL,
            context_sha256 TEXT NOT NULL,
            intent_claimed INTEGER NOT NULL DEFAULT 0,
            copy_allowed INTEGER NOT NULL DEFAULT 0,
            policy_mutation_allowed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(exchange,source_trader_id,source_trade_id)
        );
    """)


def _market_context(con, symbol, cutoff):
    if not _table_exists(con, "market_klines"):
        return None, "MARKET_KLINES_MISSING"
    rows = con.execute("""
        SELECT close,high,low,volume,quote_volume,taker_buy_quote_volume,close_time
        FROM market_klines
        WHERE symbol=? AND interval='1h' AND open_time<?
        ORDER BY open_time DESC LIMIT 26
    """, (symbol, cutoff)).fetchall()
    rows = [row for row in rows if row[6] < cutoff][:25]
    if len(rows) < 5:
        return None, "INSUFFICIENT_PRE_ENTRY_KLINES"
    rows = list(reversed(rows))
    closes = [float(row[0]) for row in rows]
    if any(not math.isfinite(value) or value <= 0 for value in closes):
        return None, "INVALID_PRE_ENTRY_PRICE"

    def change(hours):
        return ((closes[-1] / closes[-1 - hours]) - 1.0) * 100.0 \
            if len(closes) > hours else None

    recent_volume = float(rows[-1][3] or 0.0)
    prior_volumes = [float(row[3] or 0.0) for row in rows[:-1]]
    average_volume = sum(prior_volumes) / len(prior_volumes) if prior_volumes else 0.0
    high = max(float(row[1]) for row in rows)
    low = min(float(row[2]) for row in rows)
    quote_volume = rows[-1][4]
    taker_buy_quote = rows[-1][5]
    return {
        "last_closed_at": rows[-1][6],
        "last_close": closes[-1],
        "return_1h_pct": change(1),
        "return_4h_pct": change(4),
        "return_24h_pct": change(24),
        "range_pct": ((high / low) - 1.0) * 100.0 if low > 0 else None,
        "last_volume_vs_prior_mean": (
            recent_volume / average_volume if average_volume > 0 else None
        ),
        "last_taker_buy_share": (
            float(taker_buy_quote) / float(quote_volume)
            if quote_volume not in (None, 0) and taker_buy_quote is not None else None
        ),
        "closed_candle_count": len(rows),
    }, None


def _derivatives_context(con, symbol, cutoff):
    result = {}
    if _table_exists(con, "futures_positioning_hourly"):
        row = con.execute("""
            SELECT source_max_time,open_interest_value,top_account_ratio,
                   top_position_ratio,global_account_ratio,taker_ratio
            FROM futures_positioning_hourly
            WHERE symbol=? AND source_max_time<?
            ORDER BY source_max_time DESC LIMIT 1
        """, (symbol, cutoff)).fetchone()
        if row:
            result["positioning"] = {
                "source_max_time": row[0], "open_interest_value": row[1],
                "top_account_ratio": row[2], "top_position_ratio": row[3],
                "global_account_ratio": row[4], "taker_ratio": row[5],
            }
    if _table_exists(con, "funding_events"):
        row = con.execute("""
            SELECT funding_time,rate,mark_price FROM funding_events
            WHERE symbol=? AND funding_time<? ORDER BY funding_time DESC LIMIT 1
        """, (symbol, cutoff)).fetchone()
        if row:
            result["funding"] = {
                "funding_time": row[0], "rate": row[1], "mark_price": row[2],
            }
    return result


def _hypotheses(direction, market, derivatives):
    hypotheses = []
    four_hour = market.get("return_4h_pct")
    one_hour = market.get("return_1h_pct")
    if four_hour is not None and four_hour != 0:
        aligned = (direction == "LONG" and four_hour > 0) or (
            direction == "SHORT" and four_hour < 0
        )
        hypotheses.append({
            "kind": "MOMENTUM_CONTINUATION" if aligned else "MOMENTUM_REVERSAL",
            "basis": {"return_4h_pct": four_hour},
            "claim_strength": "POSSIBLE_NOT_PROVEN",
        })
    if one_hour is not None and one_hour != 0:
        aligned = (direction == "LONG" and one_hour > 0) or (
            direction == "SHORT" and one_hour < 0
        )
        hypotheses.append({
            "kind": "SHORT_TERM_DIRECTION_ALIGNMENT" if aligned
                    else "SHORT_TERM_COUNTERTREND",
            "basis": {"return_1h_pct": one_hour},
            "claim_strength": "POSSIBLE_NOT_PROVEN",
        })
    volume_ratio = market.get("last_volume_vs_prior_mean")
    if volume_ratio is not None:
        hypotheses.append({
            "kind": "ELEVATED_ACTIVITY" if volume_ratio > 1.0 else "NORMAL_OR_LOW_ACTIVITY",
            "basis": {"last_volume_vs_prior_mean": volume_ratio},
            "claim_strength": "CONTEXT_ONLY",
        })
    positioning = derivatives.get("positioning") or {}
    global_ratio = positioning.get("global_account_ratio")
    if global_ratio is not None and float(global_ratio) != 1.0:
        crowd_long = float(global_ratio) > 1.0
        aligned = (direction == "LONG" and crowd_long) or (
            direction == "SHORT" and not crowd_long
        )
        hypotheses.append({
            "kind": "CROWD_ALIGNMENT" if aligned else "CROWD_CONTRARIAN",
            "basis": {"global_account_ratio": global_ratio},
            "claim_strength": "POSSIBLE_NOT_PROVEN",
        })
    funding = derivatives.get("funding") or {}
    if funding.get("rate") is not None:
        rate = float(funding["rate"])
        receives = (direction == "SHORT" and rate > 0) or (
            direction == "LONG" and rate < 0
        )
        hypotheses.append({
            "kind": "FUNDING_RECEIVER" if receives else "FUNDING_PAYER",
            "basis": {"funding_rate": rate},
            "claim_strength": "CONTEXT_ONLY",
        })
    return hypotheses


def capture_open_trade_context(con, trade, observed_at):
    """Freeze pre-entry context while outcome is still unknown."""
    ensure_tables(con)
    exchange = str(trade["exchange"]).upper()
    trader = str(trade["source_trader_id"])
    trade_id = str(trade["source_trade_id"])
    existing = con.execute("""
        SELECT context_status FROM external_trader_entry_contexts
        WHERE exchange=? AND source_trader_id=? AND source_trade_id=?
    """, (exchange, trader, trade_id)).fetchone()
    if existing:
        return {"stored": False, "status": existing[0]}
    entry_time = _iso(trade["entry_time"])
    observed_at = _iso(observed_at)
    market, error = _market_context(con, str(trade["symbol"]).upper(), entry_time)
    derivatives = _derivatives_context(
        con, str(trade["symbol"]).upper(), entry_time
    ) if market else {}
    hypotheses = _hypotheses(str(trade["direction"]).upper(), market, derivatives) \
        if market else []
    status = "CONTEXT_CAPTURED" if hypotheses else (error or "NO_EXPLAINABLE_CONTEXT")
    context = {
        "version": VERSION,
        "basis": "PRE_ENTRY_CLOSED_MARKET_DATA_RECONSTRUCTED_WHILE_TRADE_OPEN",
        "market": market, "derivatives": derivatives,
        "outcome_used": False, "intent_claimed": False,
    }
    canonical = json.dumps(
        {"context": context, "hypotheses": hypotheses},
        sort_keys=True, separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    con.execute("""
        INSERT INTO external_trader_entry_contexts(
            exchange,source_trader_id,source_trade_id,symbol,direction,
            entry_time,first_observed_at,context_cutoff_at,context_status,
            context_json,rationale_hypotheses_json,context_sha256,
            retrospective_reconstruction,outcome_known_at_capture,intent_claimed,
            created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,0,0,?)
    """, (
        exchange, trader, trade_id, str(trade["symbol"]).upper(),
        str(trade["direction"]).upper(), entry_time, observed_at, entry_time,
        status, json.dumps(context, sort_keys=True),
        json.dumps(hypotheses, sort_keys=True), digest, observed_at,
    ))
    return {"stored": True, "status": status, "hypothesis_count": len(hypotheses)}


def review_available_closed_contexts(con, now=None):
    """Assess outcomes only against hypotheses frozen while positions were open."""
    ensure_tables(con)
    reviewed_at = _iso(now or datetime.now(timezone.utc))
    rows = con.execute("""
        SELECT c.exchange,c.source_trader_id,c.source_trade_id,c.direction,
               c.context_status,c.rationale_hypotheses_json,c.context_sha256,
               closed.reported_pnl
        FROM external_trader_entry_contexts c
        JOIN external_trader_trade_observations closed
          ON closed.observation_id=(
              SELECT MAX(candidate.observation_id)
              FROM external_trader_trade_observations candidate
              WHERE candidate.exchange=c.exchange
                AND candidate.source_trader_id=c.source_trader_id
                AND candidate.source_trade_id=c.source_trade_id
                AND candidate.status='CLOSED'
                AND candidate.reported_pnl IS NOT NULL
                AND candidate.retrospective_only=0
          )
        LEFT JOIN external_trader_rationale_reviews r
          ON r.exchange=c.exchange AND r.source_trader_id=c.source_trader_id
         AND r.source_trade_id=c.source_trade_id
        WHERE closed.status='CLOSED' AND closed.reported_pnl IS NOT NULL
          AND closed.retrospective_only=0 AND r.source_trade_id IS NULL
    """).fetchall()
    inserted = 0
    for exchange, trader, trade_id, direction, context_status, raw, digest, pnl in rows:
        pnl = float(pnl)
        outcome = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")
        hypotheses = json.loads(raw)
        if outcome == "LOSS":
            possible_errors = []
            kinds = {item["kind"] for item in hypotheses}
            if "MOMENTUM_CONTINUATION" in kinds:
                possible_errors.append("MOMENTUM_DID_NOT_PERSIST")
            if "MOMENTUM_REVERSAL" in kinds or "SHORT_TERM_COUNTERTREND" in kinds:
                possible_errors.append("REVERSAL_DID_NOT_MATERIALIZE")
            if "CROWD_ALIGNMENT" in kinds:
                possible_errors.append("CROWDING_RISK_POSSIBLE")
            if not possible_errors:
                possible_errors.append("CAUSE_NOT_IDENTIFIABLE_FROM_AVAILABLE_CONTEXT")
        else:
            possible_errors = []
        assessment = {
            "version": VERSION,
            "outcome": outcome,
            "reported_pnl": pnl,
            "frozen_hypotheses": hypotheses,
            "possible_error_hypotheses": possible_errors,
            "causal_claim": False,
            "trader_intent_claimed": False,
            "copy_instruction": False,
            "note": (
                "Observed pre-entry context is associated with this outcome; "
                "it does not prove why the trader acted or why the trade won/lost."
            ),
        }
        con.execute("""
            INSERT INTO external_trader_rationale_reviews(
                exchange,source_trader_id,source_trade_id,reviewed_at,
                outcome_class,reported_pnl,assessment_json,context_sha256,
                intent_claimed,copy_allowed,policy_mutation_allowed
            ) VALUES(?,?,?,?,?,?,?,?,0,0,0)
        """, (
            exchange, trader, trade_id, reviewed_at, outcome, pnl,
            json.dumps(assessment, sort_keys=True), digest,
        ))
        inserted += 1
    con.commit()
    totals = dict(con.execute("""
        SELECT outcome_class,COUNT(*) FROM external_trader_rationale_reviews
        GROUP BY outcome_class
    """).fetchall())
    contexts = con.execute(
        "SELECT COUNT(*) FROM external_trader_entry_contexts"
    ).fetchone()[0]
    return {
        "version": VERSION, "new_reviews": inserted,
        "entry_contexts": contexts, "reviews_by_outcome": totals,
        "intent_claimed": False, "copy_allowed": False,
        "policy_mutation_allowed": False,
    }
