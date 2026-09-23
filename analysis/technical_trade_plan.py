"""Adaptive pre-trade technical plan built only from local canonical cache.

Opportunity sources (MARKET_BEHAVIOR, PRICE_ACTION and future triggers) are
triggers, not entry authority.  This module turns the already-collected Binance
USD-M closed bars plus the off-hot-path higher-timeframe context into a bounded
PAPER execution plan.  It performs no network I/O.

The plan can affect ranking, sizing, entry, stop and targets.  It cannot override
trade direction by itself and no single indicator is a hard veto.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone

from analysis import realtime_technical_context
from database import technical_bar_store


VERSION = "TECHNICAL_TRADE_PLAN_V2_LOCAL_ADAPTIVE"
PLAN_TTL_SECONDS = 180
MAX_BAR_AGE_SECONDS = 180
MAX_CACHE_ROWS = 2160
MIN_FRAME_BARS = 22
MIN_STOP_PCT = 0.50
MAX_STOP_PCT = 5.00
LOCAL_FRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15}


def _iso(value):
    return value.astimezone(timezone.utc).isoformat()


def _parse(value):
    parsed = (
        value if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clip(value, low, high):
    return max(float(low), min(float(high), float(value)))


def _ema(values, period):
    if len(values) < period:
        raise ValueError(f"insufficient EMA{period} history")
    seed = statistics.fmean(values[:period])
    alpha = 2.0 / (period + 1.0)
    for value in values[period:]:
        seed += alpha * (value - seed)
    return seed


def _rsi(values, period=14):
    if len(values) <= period:
        raise ValueError("insufficient RSI history")
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(value, 0.0) for value in changes[-period:]]
    losses = [max(-value, 0.0) for value in changes[-period:]]
    gain = sum(gains)
    loss = sum(losses)
    if loss <= 0:
        return 100.0
    rs = gain / loss
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(rows, period=14):
    if len(rows) <= period:
        raise ValueError("insufficient ATR history")
    ranges = []
    for index in range(1, len(rows)):
        high = rows[index]["high"]
        low = rows[index]["low"]
        previous = rows[index - 1]["close"]
        ranges.append(max(high - low, abs(high - previous), abs(low - previous)))
    return statistics.fmean(ranges[-period:])


def _contiguous_suffix(rows, now):
    """Return the newest exact 1m run; never bridge a missing minute."""
    now_ms = int(now.timestamp() * 1000)
    valid = []
    for raw in rows:
        try:
            opened = int(raw["open_time_ms"])
            closed = int(raw["close_time_ms"])
            values = tuple(float(raw[key]) for key in ("open", "high", "low", "close"))
            quote_volume = float(raw.get("quote_volume") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if (
            closed > now_ms
            or closed - opened != 59_999
            or not all(math.isfinite(value) for value in values)
            or min(values) <= 0
            or quote_volume < 0
        ):
            continue
        valid.append({
            "open_ms": opened, "close_ms": closed,
            "open": values[0], "high": values[1], "low": values[2],
            "close": values[3], "quote_volume": quote_volume,
        })
    valid.sort(key=lambda row: row["open_ms"])
    if not valid:
        return []
    start = len(valid) - 1
    while start > 0 and valid[start]["open_ms"] - valid[start - 1]["open_ms"] == 60_000:
        start -= 1
    return valid[start:]


def aggregate(rows, minutes):
    period_ms = int(minutes) * 60_000
    groups = {}
    for row in rows:
        bucket = row["open_ms"] - (row["open_ms"] % period_ms)
        groups.setdefault(bucket, []).append(row)
    output = []
    for bucket, members in sorted(groups.items()):
        members = sorted(members, key=lambda row: row["open_ms"])
        expected = [bucket + 60_000 * index for index in range(int(minutes))]
        if (
            len(members) != int(minutes)
            or [row["open_ms"] for row in members] != expected
        ):
            continue
        output.append({
            "open_ms": bucket,
            "close_ms": members[-1]["close_ms"],
            "open": members[0]["open"],
            "high": max(row["high"] for row in members),
            "low": min(row["low"] for row in members),
            "close": members[-1]["close"],
            "quote_volume": sum(row["quote_volume"] for row in members),
        })
    return output


def frame_metrics(rows):
    if len(rows) < MIN_FRAME_BARS:
        raise ValueError("insufficient technical frame history")
    closes = [row["close"] for row in rows]
    latest = closes[-1]
    atr14 = _atr(rows)
    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    rsi14 = _rsi(closes, 14)
    momentum5 = closes[-1] - closes[-6]
    support20 = min(row["low"] for row in rows[-20:])
    resistance20 = max(row["high"] for row in rows[-20:])
    support48 = (
        min(row["low"] for row in rows[-48:]) if len(rows) >= 48 else support20
    )
    resistance48 = (
        max(row["high"] for row in rows[-48:]) if len(rows) >= 48 else resistance20
    )
    atr_scale = max(atr14, latest * 1e-8)
    volume_window = [row["quote_volume"] for row in rows[-20:]]
    volume_mean = statistics.fmean(volume_window)
    trend_score = statistics.fmean([
        math.tanh((latest - ema21) / atr_scale),
        math.tanh((ema9 - ema21) / atr_scale),
        math.tanh(momentum5 / atr_scale),
        _clip((rsi14 - 50.0) / 25.0, -1.0, 1.0),
    ])
    return {
        "bar_count": len(rows),
        "as_of_ms": int(rows[-1]["close_ms"]),
        "close": latest,
        "ema9": ema9,
        "ema21": ema21,
        "rsi14": rsi14,
        "atr14": atr14,
        "atr14_pct": 100.0 * atr14 / latest,
        "support20": support20,
        "resistance20": resistance20,
        "support48": support48,
        "resistance48": resistance48,
        "momentum5": momentum5,
        "volume_ratio20": (
            float(rows[-1]["quote_volume"]) / volume_mean if volume_mean > 0 else 1.0
        ),
        "trend_score": _clip(trend_score, -1.0, 1.0),
    }


def adaptive_timeframes(candidate):
    """Choose only frames relevant to the current opportunity type/horizon."""
    evidence = candidate.get("evidence") or {}
    event = evidence.get("event") or {}
    kind = str(event.get("kind") or "MARKET_STRUCTURE").upper()
    try:
        horizon = int(candidate.get("horizon") or 4)
    except (TypeError, ValueError):
        horizon = 4

    if horizon >= 24 * 7:
        requested = ["15m", "1h", "4h", "1d", "3d", "1w", "1M"]
        profile = "REGIME_LONG_HORIZON"
    elif horizon >= 24:
        requested = ["15m", "1h", "4h", "1d", "3d"]
        profile = "SWING_STRUCTURE"
    elif kind == "PRICE_ACTION":
        requested = ["1m", "5m", "15m", "1h"]
        profile = "FAST_PRICE_ACTION"
    elif kind == "MARKET_STRUCTURE":
        requested = ["5m", "15m", "1h", "4h"]
        profile = "STRUCTURE_BREAKOUT"
    else:
        requested = ["1m", "5m", "15m", "1h", "4h"]
        profile = "EVENT_SHOCK_CONFIRMATION"
    return {"profile": profile, "requested": requested, "event_kind": kind,
            "horizon_hours": horizon}


def _higher_trend(context, frame):
    summary = (context or {}).get("summary") or {}
    value = str(summary.get(f"trend_{frame}") or "MIXED").upper()
    return 1.0 if value == "BULLISH" else (-1.0 if value == "BEARISH" else 0.0)


def _finite_levels(values, *, below=None, above=None):
    output = []
    for value in values:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(parsed) or parsed <= 0:
            continue
        if below is not None and parsed >= below:
            continue
        if above is not None and parsed <= above:
            continue
        output.append(parsed)
    return sorted(set(output))


def _frame_weights(profile):
    if profile == "FAST_PRICE_ACTION":
        return {"1m": .30, "5m": .30, "15m": .25, "1h": .15}
    if profile == "STRUCTURE_BREAKOUT":
        return {"5m": .25, "15m": .35, "1h": .25, "4h": .15}
    if profile == "EVENT_SHOCK_CONFIRMATION":
        return {"1m": .20, "5m": .25, "15m": .25, "1h": .20, "4h": .10}
    return {"15m": .20, "1h": .30, "4h": .30, "1d": .20}


def _relation(score):
    if score >= .25:
        return "SUPPORTS_DIRECTION"
    if score <= -.25:
        return "OPPOSES_DIRECTION"
    return "MIXED_OR_UNALIGNED"


def build_plan(
    minute_rows, *, candidate, execution_market_type, higher_context,
    generated_at=None,
):
    """Build one causal plan from local rows; network access is impossible here."""
    generated_at = generated_at or datetime.now(timezone.utc)
    symbol = str(candidate["symbol"]).upper()
    direction = str(candidate["direction"]).upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("invalid technical-plan direction")

    profile = adaptive_timeframes(candidate)
    contiguous = _contiguous_suffix(minute_rows, generated_at)
    if len(contiguous) < MIN_FRAME_BARS:
        raise ValueError(
            f"insufficient fresh contiguous 1m technical bars ({len(contiguous)}/{MIN_FRAME_BARS})"
        )
    as_of = datetime.fromtimestamp(contiguous[-1]["close_ms"] / 1000, timezone.utc)
    bar_age = (generated_at - as_of).total_seconds()
    if bar_age < -1 or bar_age > MAX_BAR_AGE_SECONDS:
        raise ValueError("stale local technical bars")

    metrics = {}
    unavailable = []
    for frame in profile["requested"]:
        if frame in LOCAL_FRAME_MINUTES:
            minutes = LOCAL_FRAME_MINUTES[frame]
            rows = contiguous if minutes == 1 else aggregate(contiguous, minutes)
            if len(rows) >= MIN_FRAME_BARS:
                metrics[frame] = frame_metrics(rows)
            else:
                unavailable.append(frame)
        elif frame in {"1h", "4h"}:
            if higher_context is not None:
                metrics[frame] = {"trend_score": _higher_trend(higher_context, frame)}
            else:
                unavailable.append(frame)
        else:
            # Long-regime frames deliberately fail visible until an
            # authoritative off-hot-path context exists. Never synthesize them.
            unavailable.append(frame)

    required_lower = {
        "FAST_PRICE_ACTION": "1m",
        "EVENT_SHOCK_CONFIRMATION": "1m",
        "STRUCTURE_BREAKOUT": "5m",
        "SWING_STRUCTURE": "15m",
        "REGIME_LONG_HORIZON": "15m",
    }[profile["profile"]]
    if required_lower not in metrics:
        raise ValueError(f"required adaptive frame unavailable: {required_lower}")
    if "1h" in profile["requested"] and "1h" not in metrics:
        raise ValueError("fresh 1h context unavailable")
    if profile["profile"] == "SWING_STRUCTURE" and "1d" not in metrics:
        raise ValueError("required 1d regime context unavailable")
    if profile["profile"] == "REGIME_LONG_HORIZON" and (
        "1d" not in metrics or "1w" not in metrics
    ):
        raise ValueError("required long-regime context unavailable")

    side = 1.0 if direction == "LONG" else -1.0
    weights = _frame_weights(profile["profile"])
    weighted = [
        (float(weights.get(frame, 0.0)), side * float(values["trend_score"]))
        for frame, values in metrics.items()
        if frame in weights and float(weights.get(frame, 0.0)) > 0
    ]
    weight_sum = sum(weight for weight, _ in weighted)
    if weight_sum <= 0:
        raise ValueError("no weighted adaptive technical frames")
    score = _clip(
        sum(weight * value for weight, value in weighted) / weight_sum,
        -1.0, 1.0,
    )
    quality_multiplier = _clip(1.0 + .15 * score, .85, 1.15)

    lower_order = [frame for frame in ("1m", "5m", "15m") if frame in metrics]
    fastest = metrics[lower_order[0]]
    setup = metrics[lower_order[-1]]
    current = float(fastest["close"])
    atr_fast = max(float(fastest["atr14"]), current * .0001)
    atr_setup = max(float(setup["atr14"]), atr_fast)
    levels = (higher_context or {}).get("levels") or {}

    supports = _finite_levels([
        *(metrics[frame].get("support20") for frame in lower_order),
        *(metrics[frame].get("support48") for frame in lower_order),
        *(metrics[frame].get("ema21") for frame in lower_order),
        levels.get("support_1h_72"), levels.get("support_4h_20"),
    ], below=current)
    resistances = _finite_levels([
        *(metrics[frame].get("resistance20") for frame in lower_order),
        *(metrics[frame].get("resistance48") for frame in lower_order),
        *(metrics[frame].get("ema21") for frame in lower_order),
        levels.get("resistance_1h_72"), levels.get("resistance_4h_20"),
    ], above=current)

    pullback_factor = .15 if score >= .35 else (.55 if score <= -.25 else .30)
    zone_half = max(.12 * atr_setup, .20 * atr_fast)

    if direction == "LONG":
        nearest = max(supports) if supports else current - atr_setup
        anchor = max(nearest, current - pullback_factor * atr_setup)
        entry_low, entry_high = anchor - zone_half, anchor + zone_half
        if score >= .35:
            entry_high = max(entry_high, current + .03 * atr_fast)
        stop_levels = _finite_levels(supports, below=entry_low + 1e-12)
        structural = max(stop_levels) if stop_levels else entry_low - atr_setup
        stop_price = structural - .25 * atr_setup
        worst_entry = entry_high
        stop_pct = 100.0 * (worst_entry - stop_price) / worst_entry
    else:
        nearest = min(resistances) if resistances else current + atr_setup
        anchor = min(nearest, current + pullback_factor * atr_setup)
        entry_low, entry_high = anchor - zone_half, anchor + zone_half
        if score >= .35:
            entry_low = min(entry_low, current - .03 * atr_fast)
        stop_levels = _finite_levels(resistances, above=entry_high - 1e-12)
        structural = min(stop_levels) if stop_levels else entry_high + atr_setup
        stop_price = structural + .25 * atr_setup
        worst_entry = entry_low
        stop_pct = 100.0 * (stop_price - worst_entry) / worst_entry

    if stop_pct < MIN_STOP_PCT:
        stop_pct = MIN_STOP_PCT
        stop_price = (
            worst_entry * (1.0 - stop_pct / 100.0)
            if direction == "LONG"
            else worst_entry * (1.0 + stop_pct / 100.0)
        )
    risk_compatible = bool(stop_pct <= MAX_STOP_PCT)

    if direction == "LONG":
        structural_targets = _finite_levels([
            *(metrics[frame].get("resistance20") for frame in lower_order),
            *(metrics[frame].get("resistance48") for frame in lower_order),
            levels.get("resistance_1h_72"), levels.get("resistance_4h_20"),
        ], above=entry_high)
        minimum_tp1 = anchor + .60 * atr_setup
        tp1_pool = [value for value in structural_targets if value >= minimum_tp1]
        tp1 = tp1_pool[0] if tp1_pool else anchor + atr_setup
        tp2_pool = [
            value for value in structural_targets
            if value >= max(tp1 + .50 * atr_setup, anchor + 1.40 * atr_setup)
        ]
        tp2 = tp2_pool[0] if tp2_pool else anchor + 2.0 * atr_setup
        reward1 = 100.0 * (tp1 - anchor) / anchor
        reward2 = 100.0 * (tp2 - anchor) / anchor
    else:
        structural_targets = sorted(_finite_levels([
            *(metrics[frame].get("support20") for frame in lower_order),
            *(metrics[frame].get("support48") for frame in lower_order),
            levels.get("support_1h_72"), levels.get("support_4h_20"),
        ], below=entry_low), reverse=True)
        maximum_tp1 = anchor - .60 * atr_setup
        tp1_pool = [value for value in structural_targets if value <= maximum_tp1]
        tp1 = tp1_pool[0] if tp1_pool else anchor - atr_setup
        tp2_pool = [
            value for value in structural_targets
            if value <= min(tp1 - .50 * atr_setup, anchor - 1.40 * atr_setup)
        ]
        tp2 = tp2_pool[0] if tp2_pool else anchor - 2.0 * atr_setup
        reward1 = 100.0 * (anchor - tp1) / anchor
        reward2 = 100.0 * (anchor - tp2) / anchor

    numeric_geometry = (entry_low, entry_high, anchor, stop_price, tp1, tp2)
    if (
        not all(math.isfinite(value) and value > 0 for value in numeric_geometry)
        or entry_low > entry_high or reward1 <= 0 or reward2 <= reward1
    ):
        raise ValueError("invalid technical trade geometry")

    move_threshold_pct = _clip(
        max(.15, .50 * float(setup["atr14_pct"])), .15, 1.25
    )
    evidence = candidate.get("evidence") or {}
    event = evidence.get("event") or {}
    expires = generated_at + timedelta(seconds=PLAN_TTL_SECONDS)
    return {
        "version": VERSION,
        "symbol": symbol,
        "direction": direction,
        "analysis_market_type": "USD_M_PERPETUAL",
        "execution_market_type": str(execution_market_type),
        "source": "BINANCE_CANONICAL_LOCAL_CACHE",
        "network_io": False,
        "generated_at": _iso(generated_at),
        "as_of": _iso(as_of),
        "freshness": {
            "ttl_seconds": PLAN_TTL_SECONDS,
            "expires_at": _iso(expires),
            "maximum_bar_age_seconds": MAX_BAR_AGE_SECONDS,
            "replan_price_move_pct": move_threshold_pct,
        },
        "trigger": {
            "event_kind": profile["event_kind"],
            "horizon_hours": profile["horizon_hours"],
            "primary_signal_source": evidence.get("primary_signal_source"),
            "evidence_minute_end_ms": evidence.get("evidence_minute_end_ms"),
        },
        "profile": profile["profile"],
        "timeframes_requested": profile["requested"],
        "timeframes_used": [frame for frame in profile["requested"] if frame in metrics],
        "timeframes_unavailable": unavailable,
        "technical_score": score,
        "relation_to_trigger_direction": _relation(score),
        "quality_multiplier": quality_multiplier,
        "reference_price": current,
        "entry": {
            "low": entry_low, "high": entry_high, "anchor": anchor,
            "chase_allowed": False,
            "timing": "WAIT_FOR_FRESH_EXECUTABLE_QUOTE_INSIDE_ZONE",
            "basis": "ADAPTIVE_STRUCTURE_ATR_AND_HIGHER_TIMEFRAME_LEVELS",
        },
        "risk": {
            "invalidation_price": stop_price,
            "stop_distance_pct": stop_pct,
            "risk_compatible": risk_compatible,
            "minimum_policy_stop_pct": MIN_STOP_PCT,
            "maximum_policy_stop_pct": MAX_STOP_PCT,
            "basis": "TECHNICAL_STRUCTURE_PLUS_ATR_BUFFER",
        },
        "targets": {
            "tp1_price": tp1, "tp2_price": tp2,
            "tp1_reward_pct": reward1, "tp2_reward_pct": reward2,
            "basis": "STRUCTURAL_LEVELS_WITH_ATR_PROJECTION_FALLBACK",
        },
        "frames": {
            frame: values for frame, values in metrics.items()
            if frame in LOCAL_FRAME_MINUTES
        },
        "higher_context": {
            "version": (higher_context or {}).get("version"),
            "as_of": (higher_context or {}).get("as_of"),
            "trend_1h": ((higher_context or {}).get("summary") or {}).get("trend_1h"),
            "trend_4h": ((higher_context or {}).get("summary") or {}).get("trend_4h"),
        },
        "authority": {
            "direction_override": False,
            "ranking_input": True,
            "sizing_input": True,
            "entry_input": True,
            "stop_input": True,
            "take_profit_input": True,
            "single_indicator_hard_veto": False,
        },
        "real_order_allowed": False,
    }


def build_for_candidate(con, candidate, execution_market_type, now=None):
    """Load only local/off-hot-path data and build a pre-trade plan."""
    now = now or datetime.now(timezone.utc)
    profile = adaptive_timeframes(candidate)
    higher = None
    if any(frame in {"1h", "4h"} for frame in profile["requested"]):
        higher = realtime_technical_context.load_latest(
            con, candidate["symbol"], now=now
        )
        if higher is None:
            raise ValueError("fresh higher-timeframe technical context unavailable")
    rows = technical_bar_store.load_recent(
        con, candidate["symbol"], limit=MAX_CACHE_ROWS,
        before_ms=int(now.timestamp() * 1000),
    )
    return build_plan(
        rows, candidate=candidate, execution_market_type=execution_market_type,
        higher_context=higher, generated_at=now,
    )


def replan_reason(plan, live_price, now=None):
    """Return why a frozen plan must be rebuilt before entry, else None."""
    now = now or datetime.now(timezone.utc)
    try:
        generated = _parse(plan["generated_at"])
        as_of = _parse(plan["as_of"])
        reference = float(plan["reference_price"])
        threshold = float(plan["freshness"]["replan_price_move_pct"])
        live_price = float(live_price)
    except (KeyError, TypeError, ValueError):
        return "MALFORMED_PLAN"
    if now < generated - timedelta(seconds=1) or now < as_of - timedelta(seconds=1):
        return "PLAN_FROM_FUTURE"
    if (now - generated).total_seconds() > float(
        plan.get("freshness", {}).get("ttl_seconds", PLAN_TTL_SECONDS)
    ):
        return "PLAN_TTL_EXPIRED"
    if (now - as_of).total_seconds() > float(
        plan.get("freshness", {}).get(
            "maximum_bar_age_seconds", MAX_BAR_AGE_SECONDS
        )
    ):
        return "PLAN_MARKET_DATA_STALE"
    if not math.isfinite(live_price) or live_price <= 0 or reference <= 0:
        return "INVALID_LIVE_PRICE"
    move_pct = abs(live_price / reference - 1.0) * 100.0
    if move_pct > threshold:
        return "PRICE_MOVED_MATERIALLY"
    return None


def rebuild_for_position(con, plan, live_price, now=None):
    """Rebuild a stale pending-entry plan from current local data only."""
    now = now or datetime.now(timezone.utc)
    trigger = plan.get("trigger") or {}
    candidate = {
        "symbol": plan["symbol"],
        "direction": plan["direction"],
        "horizon": int(trigger.get("horizon_hours") or 4),
        "current_price": float(live_price),
        "evidence": {
            "evidence_minute_end_ms": trigger.get("evidence_minute_end_ms"),
            "primary_signal_source": trigger.get("primary_signal_source"),
            "event": {"kind": trigger.get("event_kind") or "MARKET_STRUCTURE"},
        },
    }
    return build_for_candidate(
        con, candidate, plan.get("execution_market_type") or "USD_M_PERPETUAL",
        now=now,
    )
