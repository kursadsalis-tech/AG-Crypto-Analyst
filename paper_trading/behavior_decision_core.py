"""Point-in-time, cost-aware PAPER decisions from observed market behavior.

Unified Forecast is deliberately not an input. Rules are learned only from
closed behavior/event outcomes. Bounded PAPER learning experiments require a
testable cost-positive hypothesis; stronger independence/performance gates
govern trust and promotion rather than blocking experiential learning.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone


VERSION = "PAPER_BEHAVIOR_DECISION_CORE_V4"
POLICY_VERSION = "PAPER_BEHAVIOR_POLICY_V4_EXECUTION_CALIBRATED"
STRATEGY_ID = "BEHAVIOR_CORE_V4"
EXECUTION_CALIBRATION_VERSION = "PAPER_EXECUTION_CALIBRATION_V1"
HORIZON_MINUTES = 240
# Exploration gates only establish a testable, cost-positive PAPER hypothesis.
# They are deliberately separate from the evidence required to trust/promote it.
MIN_EXPLORATION_SAMPLES = 5
MIN_EXPLORATION_DAYS = 1
MIN_PATTERN_SAMPLES = 30
MIN_INDEPENDENT_DAYS = 3
MIN_COST_AFTER_WIN_RATE = 0.55
MIN_MEAN_NET_EDGE_PCT = 0.05
MIN_EXECUTION_TRADES_FOR_PROMOTION = 10
MIN_EXECUTION_DAYS_FOR_PROMOTION = 3
EXECUTION_PRIOR_STRENGTH = 10.0
MAX_EVIDENCE_AGE_SECONDS = 180
MAX_RISK_POLICY_AGE_SECONDS = 1800
MAX_ASSET_RISK_AGE_SECONDS = 7200
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
        CREATE TABLE IF NOT EXISTS paper_behavior_missed_opportunities (
            missed_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            core_version TEXT NOT NULL,
            event_id TEXT NOT NULL,
            event_kind TEXT NOT NULL,
            instrument TEXT NOT NULL,
            symbol TEXT NOT NULL,
            evidence_minute_end_ms INTEGER NOT NULL,
            direction TEXT NOT NULL,
            pattern_key TEXT NOT NULL,
            rejection_reason TEXT NOT NULL,
            expected_net_edge_pct REAL NOT NULL,
            sample_count INTEGER NOT NULL,
            independent_days INTEGER NOT NULL,
            outcome_available_at TEXT,
            counterfactual_net_return_pct REAL,
            review_status TEXT NOT NULL DEFAULT 'AWAITING_OUTCOME',
            real_order_allowed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(core_version,event_id,instrument,evidence_minute_end_ms,rejection_reason)
        );
        CREATE TABLE IF NOT EXISTS paper_behavior_execution_calibrations (
            calibration_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            calibration_version TEXT NOT NULL,
            event_kind TEXT NOT NULL,
            pattern_key TEXT NOT NULL,
            direction TEXT NOT NULL,
            closed_count INTEGER NOT NULL,
            independent_days INTEGER NOT NULL,
            cutoff_position_id INTEGER NOT NULL,
            mean_net_return_pct REAL NOT NULL,
            cost_after_win_rate REAL NOT NULL,
            evidence_json TEXT NOT NULL,
            real_order_allowed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(calibration_version,event_kind,pattern_key,direction,
                   closed_count,cutoff_position_id)
        );
    """)


def execution_feedback(con, now=None):
    """Summarize only matured, cost-after outcomes from this decision core."""
    now = now or datetime.now(timezone.utc)
    tables = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    required = {"paper_agent_positions", "paper_behavior_decisions"}
    if not required.issubset(tables):
        return {}
    rows = con.execute("""
        SELECT d.event_kind,d.pattern_key,p.direction,p.position_id,p.exit_time,
               100.0*p.net_pnl/p.notional
        FROM paper_agent_positions p
        JOIN paper_behavior_decisions d ON d.decision_id=p.decision_reference_id
        WHERE p.decision_source='MARKET_BEHAVIOR_CORE'
          AND p.status='CLOSED' AND p.evidence_eligible=1
          AND p.net_pnl IS NOT NULL AND p.notional>0 AND p.exit_time<=?
        ORDER BY p.position_id
    """, (iso(now),)).fetchall()
    grouped = defaultdict(list)
    for event_kind, pattern_key, direction, position_id, exit_time, net_return in rows:
        grouped[(str(event_kind), str(pattern_key), str(direction))].append({
            "position_id": int(position_id), "exit_time": str(exit_time),
            "net_return_pct": float(net_return),
        })
    feedback = {}
    for key, samples in grouped.items():
        returns = [sample["net_return_pct"] for sample in samples]
        days = {
            datetime.fromisoformat(sample["exit_time"].replace("Z", "+00:00")).date()
            for sample in samples
        }
        evidence = {
            "basis": "OWN_CLOSED_PAPER_TRADES_NET_AFTER_ALL_EXECUTION_COSTS",
            "event_kind": key[0], "pattern_key": key[1], "direction": key[2],
            "closed_count": len(samples), "independent_days": len(days),
            "mean_net_return_pct": statistics.fmean(returns),
            "cost_after_win_rate": statistics.fmean(value > 0 for value in returns),
            "cutoff_position_id": max(sample["position_id"] for sample in samples),
            "outcome_cutoff_at": iso(now), "real_orders": False,
        }
        con.execute("""INSERT OR IGNORE INTO paper_behavior_execution_calibrations(
            created_at,calibration_version,event_kind,pattern_key,direction,
            closed_count,independent_days,cutoff_position_id,mean_net_return_pct,
            cost_after_win_rate,evidence_json,real_order_allowed
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,0)""", (
            iso(now), EXECUTION_CALIBRATION_VERSION, key[0], key[1], key[2],
            evidence["closed_count"], evidence["independent_days"],
            evidence["cutoff_position_id"], evidence["mean_net_return_pct"],
            evidence["cost_after_win_rate"],
            json.dumps(evidence, ensure_ascii=False, sort_keys=True),
        ))
        feedback[key] = evidence
    return feedback


def calibrate_pattern(pattern, feedback):
    """Blend own execution evidence without allowing a tiny sample to veto learning."""
    calibrated = dict(pattern)
    calibrated["observational_promotion_ready"] = bool(pattern["promotion_ready"])
    if not feedback:
        calibrated.update({
            "execution_feedback": None,
            "calibrated_net_edge_pct": pattern["mean_net_edge_pct"],
            "calibrated_win_rate": pattern["cost_after_win_rate"],
            "execution_size_multiplier": 1.0,
        })
        calibrated["promotion_ready"] = False
        calibrated["ready"] = False
        return calibrated
    count = feedback["closed_count"]
    weight = count / (count + EXECUTION_PRIOR_STRENGTH)
    edge = ((1.0 - weight) * pattern["mean_net_edge_pct"]
            + weight * feedback["mean_net_return_pct"])
    win_rate = ((1.0 - weight) * pattern["cost_after_win_rate"]
                + weight * feedback["cost_after_win_rate"])
    win_signal = 2.0 * feedback["cost_after_win_rate"] - 1.0
    expectancy_signal = math.tanh(feedback["mean_net_return_pct"] / 0.5)
    performance_signal = 0.5 * win_signal + 0.5 * expectancy_signal
    size_multiplier = max(0.5, min(1.25, 1.0 + weight * performance_signal))
    execution_confirmed = (
        count >= MIN_EXECUTION_TRADES_FOR_PROMOTION
        and feedback["independent_days"] >= MIN_EXECUTION_DAYS_FOR_PROMOTION
        and edge >= MIN_MEAN_NET_EDGE_PCT
        and win_rate >= MIN_COST_AFTER_WIN_RATE
    )
    calibrated.update({
        "execution_feedback": feedback,
        "execution_feedback_weight": weight,
        "execution_performance_signal": performance_signal,
        "calibrated_net_edge_pct": edge,
        "calibrated_win_rate": win_rate,
        "execution_size_multiplier": size_multiplier,
        "promotion_ready": bool(pattern["promotion_ready"] and execution_confirmed),
    })
    calibrated["ready"] = calibrated["promotion_ready"]
    return calibrated


def settle_missed_opportunities(con):
    """Attach immutable 4h cost-after counterfactuals to coverage/risk misses."""
    if not con.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
        AND name='behavior_event_forward_outcomes'""").fetchone():
        return 0
    columns = {
        str(row[1])
        for row in con.execute("PRAGMA table_info(behavior_event_forward_outcomes)")
    }
    required = {
        "event_id", "instrument", "event_minute_end_timestamp_ms",
        "horizon_minutes", "outcome_available_at",
        "return_from_pre_event_pct", "spread_bps_mean",
    }
    if not required.issubset(columns):
        return 0
    rows = con.execute("""SELECT m.missed_id,m.direction,o.outcome_available_at,
        o.return_from_pre_event_pct,o.spread_bps_mean
        FROM paper_behavior_missed_opportunities m
        JOIN behavior_event_forward_outcomes o
          ON o.event_id=m.event_id AND o.instrument=m.instrument
         AND o.event_minute_end_timestamp_ms=m.evidence_minute_end_ms
         AND o.horizon_minutes=?
        WHERE m.review_status='AWAITING_OUTCOME'""", (HORIZON_MINUTES,)).fetchall()
    for missed_id, direction, available_at, raw_return, spread in rows:
        signed = float(raw_return) if direction == "LONG" else -float(raw_return)
        cost = ROUND_TRIP_FEE_PCT + EXTRA_COST_BUFFER_PCT + float(spread or 0) / 100.0
        con.execute("""UPDATE paper_behavior_missed_opportunities
            SET outcome_available_at=?,counterfactual_net_return_pct=?,
                review_status='COUNTERFACTUAL_SETTLED_NOT_TRADED'
            WHERE missed_id=?""", (available_at, signed - cost, missed_id))
    return len(rows)


def _flow_pattern(book, taker):
    if book > 0 and taker > 0:
        return "FLOW_POS"
    if book < 0 and taker < 0:
        return "FLOW_NEG"
    # Opposing depth and aggressive flow can represent absorption/exhaustion;
    # it is a learnable market structure, not absence of a setup.
    if book > 0 and taker < 0:
        return "BOOK_POS_TAKER_NEG"
    if book < 0 and taker > 0:
        return "BOOK_NEG_TAKER_POS"
    return None


def _price_pattern(move_pct, spread_bps):
    if move_pct is None:
        return None
    noise_floor_pct = max(0.0, float(spread_bps)) / 100.0
    if float(move_pct) > noise_floor_pct:
        return "PRICE_UP"
    if float(move_pct) < -noise_floor_pct:
        return "PRICE_DOWN"
    return None


def _symbol(instrument):
    return str(instrument).replace("-USDT-SWAP", "USDT").replace("-", "")


def _applies(symbols_json, instrument):
    try:
        symbols = [str(value).upper() for value in json.loads(symbols_json or "[]")]
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    value = str(instrument).upper()
    # For native Binance IDs only, use the literal base-asset spelling.
    # A "1000" prefix is a contract identity, never a guessed alias.
    base = value[:-4] if value.endswith("USDT") and "-" not in value else value.split("-")[0]
    return not symbols or base in symbols or _symbol(instrument) in symbols


def learned_patterns(con, now=None):
    """Learn continuation/reversal direction from deduplicated closed outcomes."""
    now = now or datetime.now(timezone.utc)
    outcome_columns = {row[1] for row in con.execute(
        "PRAGMA table_info(behavior_event_forward_outcomes)"
    )}
    price_move_expr = (
        "AVG(100.0*(event_minute_mid_price/pre_event_mid_price-1.0))"
        if {"event_minute_mid_price", "pre_event_mid_price"}.issubset(outcome_columns)
        else "NULL"
    )
    rows = con.execute(f"""
        SELECT MIN(event_minute_end_timestamp_ms),independence_unit,kind,
               AVG(return_from_pre_event_pct),AVG(book_imbalance_20_mean),
               AVG(taker_imbalance_snapshot_mean),AVG(spread_bps_mean),
               {price_move_expr}
        FROM behavior_event_forward_outcomes
        WHERE horizon_minutes=? AND outcome_available_at<=?
        GROUP BY independence_unit,kind
        ORDER BY MIN(event_minute_end_timestamp_ms),independence_unit,kind
    """, (HORIZON_MINUTES, iso(now))).fetchall()
    grouped = defaultdict(list)
    for (minute_ms, independence_unit, kind, future_return, book, taker,
         spread, price_move) in rows:
        pattern = _flow_pattern(float(book), float(taker))
        if not pattern:
            continue
        cost = ROUND_TRIP_FEE_PCT + EXTRA_COST_BUFFER_PCT + float(spread or 0) / 100.0
        sample = {
            "minute_ms": int(minute_ms), "independence_unit": str(independence_unit),
            "return_pct": float(future_return), "cost_pct": cost,
        }
        grouped[(str(kind), pattern)].append(sample)
        if str(kind) != "MARKET_STRUCTURE":
            # External/news hypotheses are also learned independently of the
            # contemporaneous book pattern. Confluence changes risk, not entry
            # permission for an otherwise cost-positive PAPER experiment.
            grouped[(str(kind), "ANY_FLOW")].append(sample)
        price_pattern = _price_pattern(price_move, spread)
        if price_pattern and str(kind) == "MARKET_STRUCTURE":
            grouped[("PRICE_ACTION", price_pattern)].append(sample)
    output = {}
    for key, samples in grouped.items():
        days = {datetime.fromtimestamp(x["minute_ms"] / 1000, timezone.utc).date() for x in samples}
        long_net = [x["return_pct"] - x["cost_pct"] for x in samples]
        short_net = [-x["return_pct"] - x["cost_pct"] for x in samples]
        candidates = (("LONG", long_net), ("SHORT", short_net))
        direction, returns = max(candidates, key=lambda item: sum(item[1]) / len(item[1]))
        mean_edge = sum(returns) / len(returns)
        win_rate = sum(value > 0 for value in returns) / len(returns)
        exploration_eligible = (
            len(samples) >= MIN_EXPLORATION_SAMPLES
            and len(days) >= MIN_EXPLORATION_DAYS
            and mean_edge > 0
        )
        promotion_ready = (
            len(samples) >= MIN_PATTERN_SAMPLES
            and len(days) >= MIN_INDEPENDENT_DAYS
            and win_rate >= MIN_COST_AFTER_WIN_RATE
            and mean_edge >= MIN_MEAN_NET_EDGE_PCT
        )
        output[key] = {
            "event_kind": key[0], "flow_pattern": key[1], "direction": direction,
            "sample_count": len(samples), "independent_days": len(days),
            "mean_net_edge_pct": mean_edge, "cost_after_win_rate": win_rate,
            "exploration_eligible": exploration_eligible,
            "promotion_ready": promotion_ready,
            # Backward-compatible report field; it means trusted/promotion-ready,
            # never whether a small PAPER learning experiment may be opened.
            "ready": promotion_ready,
        }
    own_results = execution_feedback(con, now)
    return {
        key: calibrate_pattern(
            pattern,
            own_results.get((key[0], key[1], pattern["direction"])),
        )
        for key, pattern in output.items()
    }


def _latest_risk(con, now):
    latest = con.execute("SELECT MAX(id) FROM risk_policy_snapshots").fetchone()[0]
    if latest is None:
        return {}
    policy = con.execute("""
        SELECT created_at,new_positions_allowed,risk_per_trade_pct,
               max_total_exposure_pct,max_alt_exposure_pct,
               max_single_position_pct,daily_loss_limit_pct,max_new_positions
        FROM risk_policy_snapshots WHERE id=?
    """, (latest,)).fetchone()
    if not policy or not bool(policy[1]):
        return {}
    # Unlike historical evidence, a past risk snapshot does not authorise
    # a current position. The existing signal gate uses a 30-minute policy
    # window; preserve the same limit in the PAPER behavior path.
    try:
        policy_at = datetime.fromisoformat(str(policy[0]).replace("Z", "+00:00"))
        if policy_at.tzinfo is None:
            return {}
        policy_age = (now - policy_at.astimezone(timezone.utc)).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return {}
    if not -60 <= policy_age <= MAX_RISK_POLICY_AGE_SECONDS:
        return {}
    # The global per-trade risk limit is the authoritative sizing input.
    # Missing, zero or invalid policy limits fail closed; never infer a
    # positive budget from the asset's maximum notional allocation.
    try:
        trade_risk_pct = float(policy[2])
        max_total_exposure_pct = float(policy[3])
        max_alt_exposure_pct = float(policy[4])
        max_single_position_pct = float(policy[5])
        daily_loss_limit_pct = float(policy[6])
        max_new_positions = int(policy[7])
    except (TypeError, ValueError, OverflowError):
        return {}
    numeric_limits = (
        trade_risk_pct, max_total_exposure_pct, max_alt_exposure_pct,
        max_single_position_pct, daily_loss_limit_pct,
    )
    if (any(not math.isfinite(value) or value <= 0 for value in numeric_limits)
            or max_new_positions <= 0):
        return {}
    global_limits = {
        "risk_per_trade_pct": trade_risk_pct,
        "max_total_exposure_pct": max_total_exposure_pct,
        "max_alt_exposure_pct": max_alt_exposure_pct,
        "max_single_position_pct": max_single_position_pct,
        "daily_loss_limit_pct": daily_loss_limit_pct,
        "max_new_positions": max_new_positions,
    }
    assets = {}
    for symbol, allowed, cap, price, as_of in con.execute("""
        SELECT symbol,new_position_allowed,max_position_pct,price,as_of
        FROM asset_risk_snapshots WHERE policy_snapshot_id=?
    """, (latest,)).fetchall():
        try:
            asset_time = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
            if asset_time.tzinfo is None:
                continue
            age = (now - asset_time.astimezone(timezone.utc)).total_seconds()
            cap_value, price_value = float(cap), float(price)
        except (ValueError, TypeError, OverflowError):
            continue
        # Closed 1h risk bars may be up to ~1 hour old; stale/future/missing
        # asset references cannot be used to size a new PAPER position.
        if (not -60 <= age <= MAX_ASSET_RISK_AGE_SECONDS
                or not math.isfinite(cap_value) or cap_value <= 0
                or not math.isfinite(price_value) or price_value <= 0):
            continue
        assets[symbol] = {
            "allowed": bool(allowed), "cap_pct": cap_value,
            "price": price_value, "risk_as_of": str(as_of),
            **global_limits,
            "policy_created_at": str(policy[0]),
        }
    return assets


def evaluate_live(con, now, context, patterns=None, risks=None, freshness_now=None):
    """Evaluate one fresh WebSocket state using causal rolling observations.

    Historical outcomes teach the existing core its patterns; a completed
    minute feature never authorises or times this live decision.  Freshness is
    checked against wall-clock decision time, not the event timestamp that
    started a potentially queued evaluation.
    """
    ensure_tables(con)
    observed_ms = int(context.get("observed_event_ms") or 0)
    quote_ms = int(context.get("quote_ms") or 0)
    # Callers on the live worker pass a real wall-clock function. Tests and
    # offline/replay callers remain deterministic against their supplied `now`.
    freshness_clock = freshness_now or (lambda: now)

    def live_is_fresh(at):
        wall_ms = int(at.timestamp() * 1000)
        return (
            -500 <= wall_ms - observed_ms <= 2000
            and -500 <= wall_ms - quote_ms <= 2000
        )

    if not live_is_fresh(freshness_clock()):
        return {"status": "LIVE_STATE_STALE", "candidates": []}
    symbol = str(context.get("symbol") or "")
    if (not symbol.endswith("USDT") or "-" in symbol
            or context.get("market_type") != "USD_M_PERPETUAL"):
        return {"status": "INVALID_LIVE_SYMBOL", "candidates": []}
    try:
        mid = float(context["mid"])
        book = float(context["book_imbalance"])
        taker = float(context["taker_imbalance"])
        spread = float(context["spread_bps"])
        move = float(context["move_5s_pct"])
    except (KeyError, ValueError, TypeError):
        return {"status": "LIVE_CONTEXT_INCOMPLETE", "candidates": []}
    if (not all(math.isfinite(x) for x in (mid, book, taker, spread, move))
            or mid <= 0 or spread < 0 or spread > 20):
        return {"status": "LIVE_CONTEXT_INVALID", "candidates": []}
    flow_pattern = _flow_pattern(book, taker)
    price_pattern = _price_pattern(move, spread)
    patterns = learned_patterns(con, now) if patterns is None else patterns
    risks = _latest_risk(con, now) if risks is None else risks
    # Pattern/risk loading may be expensive on a large runtime DB. Never allow
    # an event that aged out during that work to authorize a new position.
    if not live_is_fresh(freshness_clock()):
        return {"status": "LIVE_STATE_STALE", "candidates": []}
    risk = risks.get(symbol)
    candidates = []
    # A 5-second evidence bucket prevents repeated decisions on each quote.
    bucket_ms = observed_ms - observed_ms % 5000
    for kind, pattern in (("MARKET_STRUCTURE", flow_pattern),
                          ("PRICE_ACTION", price_pattern)):
        if not pattern:
            continue
        learned = patterns.get((kind, pattern))
        if not learned or not learned["exploration_eligible"]:
            continue
        candidates.extend(_candidate_for_hypothesis(
            con=con, now=now, latest=bucket_ms, instrument=symbol,
            symbol=symbol, pattern=pattern, kind=kind, symbols_json="[]",
            event_id=f"live:{kind}:{symbol}:{bucket_ms}",
            available_at=iso(datetime.fromtimestamp(observed_ms / 1000, timezone.utc)),
            event_direction="OBSERVED", learned=learned,
            event_only=False, risk=risk, mid=mid, book=book, taker=taker,
            spread=spread, oi_first=1.0, oi_change=0.0, funding=0.0,
            long_liq=0.0, short_liq=0.0, liq_count=0,
            quality_json=json.dumps({"source": "BINANCE_LIVE_WS",
                                     "observed_event_ms": observed_ms,
                                     "window_start_ms": context.get("window_start_ms"),
                                     "decision_at": iso(now)}),
            live_context=context,
        ))
    con.commit()
    candidates.sort(key=lambda x: (x["expected_net_edge_pct"], x["confidence"]), reverse=True)
    return {"status": "ACTIONABLE" if candidates else "NO_ACTIONABLE_LIVE_PATTERN",
            "candidates": candidates}


def evaluate(con, now=None):
    now = now or datetime.now(timezone.utc)
    ensure_tables(con)
    settle_missed_opportunities(con)
    patterns = learned_patterns(con, now)
    exploration_patterns = {
        key: value for key, value in patterns.items()
        if value["exploration_eligible"]
    }
    promoted_patterns = {
        key: value for key, value in patterns.items()
        if value["promotion_ready"]
    }
    review = {
        "basis": "POINT_IN_TIME_CLOSED_BEHAVIOR_EVENT_OUTCOMES_NET_AFTER_COST_MODEL",
        "patterns": list(sorted(patterns.values(), key=lambda x: (x["event_kind"], x["flow_pattern"]))),
        "exploration_gates": {
            "minimum_samples": MIN_EXPLORATION_SAMPLES,
            "minimum_independent_days": MIN_EXPLORATION_DAYS,
            "requires_positive_cost_after_mean_edge": True,
        },
        "promotion_gates": {"minimum_samples": MIN_PATTERN_SAMPLES,
                            "minimum_independent_days": MIN_INDEPENDENT_DAYS,
                            "minimum_cost_after_win_rate": MIN_COST_AFTER_WIN_RATE,
                            "minimum_mean_net_edge_pct": MIN_MEAN_NET_EDGE_PCT,
                            "minimum_own_execution_trades": MIN_EXECUTION_TRADES_FOR_PROMOTION,
                            "minimum_own_execution_days": MIN_EXECUTION_DAYS_FOR_PROMOTION},
        "unified_forecast_used": False, "real_orders": False,
    }
    review_json = json.dumps(review, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    con.execute("""INSERT OR IGNORE INTO paper_behavior_policy_reviews(
        created_at,core_version,horizon_minutes,status,evidence_json,execution_allowed
    ) VALUES(?,?,?,?,?,?)""", (
        iso(now), VERSION, HORIZON_MINUTES,
        ("PROMOTION_READY_PATTERNS_AVAILABLE" if promoted_patterns
         else "PAPER_EXPLORATION_AVAILABLE" if exploration_patterns
         else "COLLECTING_TESTABLE_HYPOTHESES"),
        review_json, int(bool(exploration_patterns)),
    ))
    latest_behavior = con.execute("""SELECT MAX(minute_end_timestamp_ms)
        FROM market_behavior_minute_features
        WHERE eligible_for_learning=1 AND minute_end_timestamp_ms<=?""",
        (int(now.timestamp() * 1000),)).fetchone()[0]
    latest_external = con.execute("""
        SELECT MAX(m.minute_end_timestamp_ms)
        FROM market_behavior_minute_features m
        WHERE m.eligible_for_learning=1 AND m.minute_end_timestamp_ms<=?
          AND EXISTS (SELECT 1 FROM causal_minute_external_evidence e
              WHERE e.minute_end_timestamp_ms=m.minute_end_timestamp_ms)
    """, (int(now.timestamp() * 1000),)).fetchone()[0]
    has_market_structure_hypothesis = any(
        kind == "MARKET_STRUCTURE" for kind, _ in exploration_patterns
    )
    latest = (
        latest_behavior if has_market_structure_hypothesis or latest_external is None
        else latest_external
    )
    if latest is None or (now.timestamp() * 1000 - int(latest)) / 1000 > MAX_EVIDENCE_AGE_SECONDS:
        con.commit()
        return {"status": "NO_FRESH_ELIGIBLE_BEHAVIOR", "candidates": [], "review": review}
    risks = _latest_risk(con, now)
    events = con.execute("""SELECT kind,symbols_json,event_id,available_at,direction
        FROM causal_minute_external_evidence WHERE minute_end_timestamp_ms=?
        ORDER BY kind,event_id""", (latest,)).fetchall()
    evidence_end = datetime.fromtimestamp(int(latest) / 1000, timezone.utc)
    events.append((
        "MARKET_STRUCTURE", "[]", f"market_structure:{latest}",
        iso(evidence_end), "OBSERVED",
    ))
    feature_columns = {row[1] for row in con.execute(
        "PRAGMA table_info(market_behavior_minute_features)"
    )}
    mid_first_expr = "mid_price_first" if "mid_price_first" in feature_columns else "NULL"
    rows = con.execute(f"""SELECT instrument,book_imbalance_20_mean,
        taker_imbalance_snapshot_mean,spread_bps_mean,mid_price_last,
        open_interest_usd_first,open_interest_usd_change,funding_rate_last,
        long_liquidation_notional_usd_estimate,
        short_liquidation_notional_usd_estimate,liquidation_event_count,
        quality_gates_json,{mid_first_expr} FROM market_behavior_minute_features
        WHERE minute_end_timestamp_ms=? AND eligible_for_learning=1 ORDER BY instrument
    """, (latest,)).fetchall()
    candidates = []
    for raw in rows:
        (instrument, book, taker, spread, mid, oi_first, oi_change, funding,
         long_liq, short_liq, liq_count, quality_json, mid_first) = raw
        # Historical OKX features remain available for audit/research,
        # but cannot authorise Binance PAPER candidates via guessed aliases.
        if not str(instrument).endswith("USDT") or "-" in str(instrument):
            continue
        pattern = _flow_pattern(float(book), float(taker))
        symbol = str(instrument).upper()
        risk = risks.get(symbol)
        if not pattern or mid is None:
            continue
        candidate_events = list(events)
        price_pattern = _price_pattern(
            (100.0 * (float(mid) / float(mid_first) - 1.0)
             if mid_first not in (None, 0) else None), spread,
        )
        if price_pattern:
            candidate_events.append((
                "PRICE_ACTION", "[]", f"price_action:{latest}:{price_pattern}",
                iso(evidence_end), "OBSERVED",
            ))
        for kind, symbols_json, event_id, available_at, event_direction in candidate_events:
            available = datetime.fromisoformat(str(available_at).replace("Z", "+00:00"))
            if available.astimezone(timezone.utc) > evidence_end:
                continue
            learned_options = []
            lookup_pattern = price_pattern if str(kind) == "PRICE_ACTION" else pattern
            specific = exploration_patterns.get((str(kind), lookup_pattern))
            event_only = (exploration_patterns.get((str(kind), "ANY_FLOW"))
                          if str(kind) != "MARKET_STRUCTURE" else None)
            if specific:
                learned_options.append((specific, False))
            if event_only:
                learned_options.append((event_only, True))
            if not learned_options or not _applies(symbols_json, instrument):
                continue
            for learned, event_only_hypothesis in learned_options:
                candidates.extend(_candidate_for_hypothesis(
                    con=con, now=now, latest=int(latest), instrument=instrument,
                    symbol=symbol, pattern=lookup_pattern, kind=str(kind),
                    symbols_json=symbols_json, event_id=event_id,
                    available_at=available_at, event_direction=event_direction,
                    learned=learned, event_only=event_only_hypothesis, risk=risk,
                    mid=mid, book=book, taker=taker, spread=spread,
                    oi_first=oi_first, oi_change=oi_change, funding=funding,
                    long_liq=long_liq, short_liq=short_liq, liq_count=liq_count,
                    quality_json=quality_json,
                ))
    con.commit()
    candidates.sort(key=lambda x: (x["expected_net_edge_pct"], x["confidence"]), reverse=True)
    return {"status": "ACTIONABLE" if candidates else "NO_ACTIONABLE_PATTERN",
            "candidates": candidates, "review": review}


def _candidate_for_hypothesis(
    *, con, now, latest, instrument, symbol, pattern, kind, symbols_json,
    event_id, available_at, event_direction, learned, event_only, risk, mid,
    book, taker, spread, oi_first, oi_change, funding, long_liq, short_liq,
    liq_count, quality_json, live_context=None,
):
    """Persist one immutable single-source or confluence PAPER hypothesis."""
    hypothesis_pattern = "ANY_FLOW" if event_only else pattern
    if not risk or not risk["allowed"]:
        con.execute("""INSERT OR IGNORE INTO paper_behavior_missed_opportunities(
            created_at,core_version,event_id,event_kind,instrument,symbol,
            evidence_minute_end_ms,direction,pattern_key,rejection_reason,
            expected_net_edge_pct,sample_count,independent_days,real_order_allowed
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0)""", (
            iso(now), VERSION, event_id, kind, instrument, symbol, latest,
            learned["direction"], hypothesis_pattern,
            "RISK_COVERAGE_MISSING" if not risk else "RISK_VETO",
            learned["calibrated_net_edge_pct"], learned["sample_count"],
            learned["independent_days"],
        ))
        return []
    price_only = kind == "PRICE_ACTION"
    primary_source = kind if event_only or price_only else "MARKET_BEHAVIOR"
    if price_only:
        capabilities = ["PRICE_ACTION", "SPREAD_COST", "RISK_GATE"]
    elif event_only:
        capabilities = [kind, "CAUSAL_EXTERNAL_EVENT", "SPREAD_COST", "RISK_GATE"]
    else:
        capabilities = ["ORDER_BOOK", "TAKER_FLOW", "SPREAD_COST",
                        "OPEN_INTEREST", "FUNDING", "LIQUIDATIONS", "RISK_GATE"]
    directional_signals = [{"source": primary_source,
                            "direction": learned["direction"]}]
    if not event_only and kind not in {"MARKET_STRUCTURE", "PRICE_ACTION"}:
        directional_signals.append({"source": kind, "direction": learned["direction"]})
        capabilities.append("CAUSAL_EXTERNAL_EVENT")
    evidence = {
        "decision_source": "MARKET_BEHAVIOR_CORE",
        "core_version": VERSION, "policy_version": POLICY_VERSION,
        "evidence_minute_end_ms": latest, "event": {
            "id": event_id, "kind": kind, "available_at": available_at,
            "reported_direction": event_direction,
        },
        "primary_signal_source": primary_source,
        "directional_signals": directional_signals,
        "hypothesis_scope": ("PRICE_ACTION_ONLY" if price_only else
            "EXTERNAL_EVENT_ONLY" if event_only else
            "MARKET_BEHAVIOR_ONLY" if kind == "MARKET_STRUCTURE" else "CONFLUENCE"),
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
        "execution_calibration": {
            "feedback": learned["execution_feedback"],
            "feedback_weight": learned.get("execution_feedback_weight", 0.0),
            "calibrated_net_edge_pct": learned["calibrated_net_edge_pct"],
            "calibrated_win_rate": learned["calibrated_win_rate"],
            "size_multiplier": learned["execution_size_multiplier"],
        },
        "experiment_tier": ("PROMOTION_READY" if learned["promotion_ready"]
                            else "EXPLORATORY_LEARNING"),
        "risk": risk, "capabilities_used": capabilities,
        "unified_forecast_used": False, "real_orders": False,
    }
    if live_context is not None:
        evidence["live_context"] = live_context
        evidence["capabilities_used"] = [
            "LIVE_QUOTE", "ORDER_BOOK", "TAKER_FLOW", "SPREAD_COST", "RISK_GATE"
        ]
        evidence["market_behavior"].update({
            "open_interest_change_pct": None, "funding_rate": None,
            "long_liquidation_usd": None, "short_liquidation_usd": None,
            "liquidation_events": None,
        })
    digest = hashlib.sha256(json.dumps(
        evidence, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    con.execute("""INSERT INTO paper_behavior_decisions(
        created_at,core_version,policy_version,instrument,symbol,
        evidence_minute_end_ms,horizon_minutes,direction,event_kind,pattern_key,
        expected_net_edge_pct,empirical_win_rate,sample_count,independent_days,
        evidence_json,evidence_sha256,status,real_order_allowed
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
    ON CONFLICT(core_version,instrument,evidence_minute_end_ms,event_kind,pattern_key)
    DO NOTHING""", (
        iso(now), VERSION, POLICY_VERSION, instrument, symbol, latest,
        HORIZON_MINUTES, learned["direction"], kind, hypothesis_pattern,
        learned["calibrated_net_edge_pct"], learned["calibrated_win_rate"],
        learned["sample_count"], learned["independent_days"],
        json.dumps(evidence, ensure_ascii=False, sort_keys=True), digest,
        ("PROMOTION_READY_PAPER_EXPERIMENT" if learned["promotion_ready"]
         else "EXPLORATORY_PAPER_LEARNING_EXPERIMENT"),
    ))
    existing = con.execute("""SELECT decision_id,evidence_json,evidence_sha256
        FROM paper_behavior_decisions WHERE core_version=? AND instrument=?
          AND evidence_minute_end_ms=? AND event_kind=? AND pattern_key=?""", (
        VERSION, instrument, latest, kind, hypothesis_pattern,
    )).fetchone()
    if not existing:
        raise RuntimeError("idempotent behavior decision could not be resolved")
    decision_id, stored_evidence_json, digest = existing
    evidence = json.loads(stored_evidence_json)
    learned = evidence["learned_pattern"]
    risk = evidence["risk"]
    return [{
        "decision_id": int(decision_id), "decision_source": "MARKET_BEHAVIOR_CORE",
        "symbol": symbol, "instrument": instrument,
        "market_type": "USD_M_PERPETUAL", "horizon": 4,
        "direction": learned["direction"], "confidence": learned["calibrated_win_rate"],
        "expected_net_edge_pct": learned["calibrated_net_edge_pct"],
        "execution_size_multiplier": learned["execution_size_multiplier"],
        "experiment_tier": evidence["experiment_tier"],
        "current_price": float(mid), "target_price": float(mid) * (
            1 + (1 if learned["direction"] == "LONG" else -1)
            * max(learned["calibrated_net_edge_pct"], 0.01) / 100.0),
        "risk_cap_pct": risk["cap_pct"],
        "risk_per_trade_pct": float(risk.get("risk_per_trade_pct") or 0.0),
        "risk_policy_limits": {
            key: risk[key] for key in (
                "max_total_exposure_pct", "max_alt_exposure_pct",
                "max_single_position_pct", "daily_loss_limit_pct",
                "max_new_positions",
            )
        },
        "evidence": evidence,
        "evidence_sha256": digest,
    }]
