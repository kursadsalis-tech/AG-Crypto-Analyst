"""Point-in-time, cost-aware PAPER decisions from observed market behavior.

Unified Forecast is deliberately not an input.  Rules are learned only from
closed behavior/event outcomes and may create PAPER experiments after minimum
independence gates pass.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone


VERSION = "PAPER_BEHAVIOR_DECISION_CORE_V1"
POLICY_VERSION = "PAPER_BEHAVIOR_POLICY_V1_EXPERIMENTAL"
STRATEGY_ID = "BEHAVIOR_CORE_V1"
HORIZON_MINUTES = 240
MIN_PATTERN_SAMPLES = 30
MIN_INDEPENDENT_DAYS = 3
MIN_COST_AFTER_WIN_RATE = 0.55
MIN_MEAN_NET_EDGE_PCT = 0.05
MAX_EVIDENCE_AGE_SECONDS = 180
ROUND_TRIP_FEE_PCT = 0.20
EXTRA_COST_BUFFER_PCT = 0.05


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def ensure_tables(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS paper_behavior_policy_reviews (
            review_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            core_version TEXT NOT NULL,
            horizon_minutes INTEGER NOT NULL,
            status TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            execution_allowed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(core_version,horizon_minutes,evidence_json)
        );
        CREATE TABLE IF NOT EXISTS paper_behavior_decisions (
            decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            core_version TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            instrument TEXT NOT NULL,
            symbol TEXT NOT NULL,
            evidence_minute_end_ms INTEGER NOT NULL,
            horizon_minutes INTEGER NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('LONG','SHORT')),
            event_kind TEXT NOT NULL,
            pattern_key TEXT NOT NULL,
            expected_net_edge_pct REAL NOT NULL,
            empirical_win_rate REAL NOT NULL,
            sample_count INTEGER NOT NULL,
            independent_days INTEGER NOT NULL,
            evidence_json TEXT NOT NULL,
            evidence_sha256 TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'ACTIONABLE_PAPER_EXPERIMENT',
            real_order_allowed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(core_version,instrument,evidence_minute_end_ms,event_kind,pattern_key)
        );
    """)


def _flow_pattern(book, taker):
    if book > 0 and taker > 0:
        return "FLOW_POS"
    if book < 0 and taker < 0:
        return "FLOW_NEG"
    return None


def _symbol(instrument):
    return str(instrument).replace("-USDT-SWAP", "USDT").replace("-", "")


def _applies(symbols_json, instrument):
    try:
        symbols = [str(value).upper() for value in json.loads(symbols_json or "[]")]
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    base = str(instrument).split("-")[0].upper()
    return not symbols or base in symbols or _symbol(instrument) in symbols


def learned_patterns(con, now=None):
    """Learn continuation/reversal direction from deduplicated closed outcomes."""
    now = now or datetime.now(timezone.utc)
    rows = con.execute("""
        SELECT MIN(event_minute_end_timestamp_ms),independence_unit,kind,
               AVG(return_from_pre_event_pct),AVG(book_imbalance_20_mean),
               AVG(taker_imbalance_snapshot_mean),AVG(spread_bps_mean)
        FROM behavior_event_forward_outcomes
        WHERE horizon_minutes=? AND outcome_available_at<=?
        GROUP BY independence_unit,kind
        ORDER BY MIN(event_minute_end_timestamp_ms),independence_unit,kind
    """, (HORIZON_MINUTES, iso(now))).fetchall()
    grouped = defaultdict(list)
    for minute_ms, independence_unit, kind, future_return, book, taker, spread in rows:
        pattern = _flow_pattern(float(book), float(taker))
        if not pattern:
            continue
        cost = ROUND_TRIP_FEE_PCT + EXTRA_COST_BUFFER_PCT + float(spread or 0) / 100.0
        grouped[(str(kind), pattern)].append({
            "minute_ms": int(minute_ms), "independence_unit": str(independence_unit),
            "return_pct": float(future_return), "cost_pct": cost,
        })
    output = {}
    for key, samples in grouped.items():
        days = {datetime.fromtimestamp(x["minute_ms"] / 1000, timezone.utc).date() for x in samples}
        long_net = [x["return_pct"] - x["cost_pct"] for x in samples]
        short_net = [-x["return_pct"] - x["cost_pct"] for x in samples]
        candidates = (("LONG", long_net), ("SHORT", short_net))
        direction, returns = max(candidates, key=lambda item: sum(item[1]) / len(item[1]))
        mean_edge = sum(returns) / len(returns)
        win_rate = sum(value > 0 for value in returns) / len(returns)
        ready = (
            len(samples) >= MIN_PATTERN_SAMPLES
            and len(days) >= MIN_INDEPENDENT_DAYS
            and win_rate >= MIN_COST_AFTER_WIN_RATE
            and mean_edge >= MIN_MEAN_NET_EDGE_PCT
        )
        output[key] = {
            "event_kind": key[0], "flow_pattern": key[1], "direction": direction,
            "sample_count": len(samples), "independent_days": len(days),
            "mean_net_edge_pct": mean_edge, "cost_after_win_rate": win_rate,
            "ready": ready,
        }
    return output


def _latest_risk(con):
    latest = con.execute("SELECT MAX(id) FROM risk_policy_snapshots").fetchone()[0]
    if latest is None:
        return {}
    policy = con.execute("""
        SELECT created_at,new_positions_allowed FROM risk_policy_snapshots WHERE id=?
    """, (latest,)).fetchone()
    if not policy or not bool(policy[1]):
        return {}
    return {row[0]: {"allowed": bool(row[1]), "cap_pct": float(row[2]),
                     "price": float(row[3]), "risk_as_of": str(row[4]),
                     "policy_created_at": str(policy[0])}
            for row in con.execute("""
                SELECT symbol,new_position_allowed,max_position_pct,price,as_of
                FROM asset_risk_snapshots WHERE policy_snapshot_id=?
            """, (latest,)).fetchall()}


def evaluate(con, now=None):
    now = now or datetime.now(timezone.utc)
    ensure_tables(con)
    patterns = learned_patterns(con, now)
    ready_patterns = {key: value for key, value in patterns.items() if value["ready"]}
    review = {
        "basis": "POINT_IN_TIME_CLOSED_BEHAVIOR_EVENT_OUTCOMES_NET_AFTER_COST_MODEL",
        "patterns": list(sorted(patterns.values(), key=lambda x: (x["event_kind"], x["flow_pattern"]))),
        "gates": {"minimum_samples": MIN_PATTERN_SAMPLES,
                  "minimum_independent_days": MIN_INDEPENDENT_DAYS,
                  "minimum_cost_after_win_rate": MIN_COST_AFTER_WIN_RATE,
                  "minimum_mean_net_edge_pct": MIN_MEAN_NET_EDGE_PCT},
        "unified_forecast_used": False, "real_orders": False,
    }
    review_json = json.dumps(review, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    con.execute("""INSERT OR IGNORE INTO paper_behavior_policy_reviews(
        created_at,core_version,horizon_minutes,status,evidence_json,execution_allowed
    ) VALUES(?,?,?,?,?,?)""", (
        iso(now), VERSION, HORIZON_MINUTES,
        "ACTIONABLE_PATTERNS_AVAILABLE" if ready_patterns else "COLLECTING_INDEPENDENT_EVIDENCE",
        review_json, int(bool(ready_patterns)),
    ))
    latest = con.execute("""
        SELECT MAX(minute_end_timestamp_ms) FROM market_behavior_minute_features
        WHERE eligible_for_learning=1 AND minute_end_timestamp_ms<=?
    """, (int(now.timestamp() * 1000),)).fetchone()[0]
    if latest is None or (now.timestamp() * 1000 - int(latest)) / 1000 > MAX_EVIDENCE_AGE_SECONDS:
        con.commit()
        return {"status": "NO_FRESH_ELIGIBLE_BEHAVIOR", "candidates": [], "review": review}
    risks = _latest_risk(con)
    events = con.execute("""SELECT kind,symbols_json,event_id,available_at,direction
        FROM causal_minute_external_evidence WHERE minute_end_timestamp_ms=?
        ORDER BY kind,event_id""", (latest,)).fetchall()
    rows = con.execute("""SELECT instrument,book_imbalance_20_mean,
        taker_imbalance_snapshot_mean,spread_bps_mean,mid_price_last,
        open_interest_usd_first,open_interest_usd_change,funding_rate_last,
        long_liquidation_notional_usd_estimate,
        short_liquidation_notional_usd_estimate,liquidation_event_count,
        quality_gates_json FROM market_behavior_minute_features
        WHERE minute_end_timestamp_ms=? AND eligible_for_learning=1 ORDER BY instrument
    """, (latest,)).fetchall()
    candidates = []
    for raw in rows:
        (instrument, book, taker, spread, mid, oi_first, oi_change, funding,
         long_liq, short_liq, liq_count, quality_json) = raw
        pattern = _flow_pattern(float(book), float(taker))
        symbol = _symbol(instrument)
        risk = risks.get(symbol)
        if not pattern or not risk or not risk["allowed"] or mid is None:
            continue
        for kind, symbols_json, event_id, available_at, event_direction in events:
            available = datetime.fromisoformat(str(available_at).replace("Z", "+00:00"))
            evidence_end = datetime.fromtimestamp(int(latest) / 1000, timezone.utc)
            if available.astimezone(timezone.utc) > evidence_end:
                continue
            learned = ready_patterns.get((str(kind), pattern))
            if not learned or not _applies(symbols_json, instrument):
                continue
            evidence = {
                "decision_source": "MARKET_BEHAVIOR_CORE",
                "core_version": VERSION, "policy_version": POLICY_VERSION,
                "evidence_minute_end_ms": int(latest), "event": {
                    "id": event_id, "kind": kind, "available_at": available_at,
                    "reported_direction": event_direction,
                },
                "market_behavior": {
                    "instrument": instrument, "book_imbalance": float(book),
                    "taker_imbalance": float(taker), "spread_bps": float(spread),
                    "mid_price": float(mid),
                    "open_interest_change_pct": 100.0 * float(oi_change) / max(abs(float(oi_first)), 1e-9),
                    "funding_rate": float(funding), "long_liquidation_usd": float(long_liq),
                    "short_liquidation_usd": float(short_liq),
                    "liquidation_events": int(liq_count),
                    "quality_gates": json.loads(quality_json),
                },
                "learned_pattern": learned,
                "risk": risk,
                "capabilities_used": ["ORDER_BOOK", "TAKER_FLOW", "SPREAD_COST",
                    "OPEN_INTEREST", "FUNDING", "LIQUIDATIONS", "CAUSAL_EXTERNAL_EVENT",
                    "RISK_GATE"],
                "unified_forecast_used": False, "real_orders": False,
            }
            digest = hashlib.sha256(json.dumps(
                evidence, sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest()
            cursor = con.execute("""INSERT OR IGNORE INTO paper_behavior_decisions(
                created_at,core_version,policy_version,instrument,symbol,
                evidence_minute_end_ms,horizon_minutes,direction,event_kind,pattern_key,
                expected_net_edge_pct,empirical_win_rate,sample_count,independent_days,
                evidence_json,evidence_sha256,status,real_order_allowed
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ACTIONABLE_PAPER_EXPERIMENT',0)""", (
                iso(now), VERSION, POLICY_VERSION, instrument, symbol, int(latest),
                HORIZON_MINUTES, learned["direction"], kind, pattern,
                learned["mean_net_edge_pct"], learned["cost_after_win_rate"],
                learned["sample_count"], learned["independent_days"],
                json.dumps(evidence, ensure_ascii=False, sort_keys=True), digest,
            ))
            decision_id = cursor.lastrowid if cursor.rowcount == 1 else con.execute(
                "SELECT decision_id FROM paper_behavior_decisions WHERE evidence_sha256=?",
                (digest,),
            ).fetchone()[0]
            candidates.append({
                "decision_id": int(decision_id), "decision_source": "MARKET_BEHAVIOR_CORE",
                "symbol": symbol, "instrument": instrument, "horizon": 4,
                "direction": learned["direction"], "confidence": learned["cost_after_win_rate"],
                "expected_net_edge_pct": learned["mean_net_edge_pct"],
                "current_price": float(mid), "target_price": float(mid) * (
                    1 + (1 if learned["direction"] == "LONG" else -1)
                    * max(learned["mean_net_edge_pct"], 0.01) / 100.0),
                "risk_cap_pct": risk["cap_pct"], "evidence": evidence,
                "evidence_sha256": digest,
            })
    con.commit()
    candidates.sort(key=lambda x: (x["expected_net_edge_pct"], x["confidence"]), reverse=True)
    return {"status": "ACTIONABLE" if candidates else "NO_ACTIONABLE_PATTERN",
            "candidates": candidates, "review": review}
