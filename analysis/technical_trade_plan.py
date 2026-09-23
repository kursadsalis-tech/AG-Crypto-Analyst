"""Adaptive, network-free pre-trade technical execution plan for PAPER candidates."""
from __future__ import annotations

import math
import statistics
import time
from datetime import datetime, timezone

from analysis import realtime_technical_context
from database.technical_bar_store import load_recent


VERSION = "TECHNICAL_TRADE_PLAN_V2_ADAPTIVE_LOCAL"
MIN_ONE_MINUTE_BARS = 360
MAX_BAR_AGE_SECONDS = 180
MIN_STOP_PCT = 0.50
MAX_STOP_PCT = 5.00
MAX_ENTRY_PLAN_AGE_SECONDS = 180
MAX_SPOT_USDM_BASIS_DEVIATION_PCT = 5.0
_PLAN_CACHE = {}


def _iso(value):
    return value.astimezone(timezone.utc).isoformat()


def _clip(value, low, high):
    return max(low, min(high, float(value)))


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
    gain, loss = sum(gains), sum(losses)
    if loss <= 0:
        return 100.0
    ratio = gain / loss
    return 100.0 - 100.0 / (1.0 + ratio)


def _atr(rows, period=14):
    if len(rows) <= period:
        raise ValueError("insufficient ATR history")
    values = []
    for index in range(1, len(rows)):
        high, low = rows[index]["high"], rows[index]["low"]
        previous = rows[index - 1]["close"]
        values.append(max(high - low, abs(high - previous), abs(low - previous)))
    return statistics.fmean(values[-period:])


def aggregate(rows, minutes):
    """Aggregate exact contiguous 1m bars to a completed N-minute frame."""
    period_ms = int(minutes) * 60_000
    groups = {}
    for row in rows:
        bucket = int(row["open_time_ms"]) - (int(row["open_time_ms"]) % period_ms)
        groups.setdefault(bucket, []).append(row)
    output = []
    for bucket, members in sorted(groups.items()):
        members = sorted(members, key=lambda item: item["open_time_ms"])
        expected = [bucket + 60_000 * index for index in range(int(minutes))]
        if len(members) != int(minutes):
            continue
        if [int(item["open_time_ms"]) for item in members] != expected:
            continue
        output.append({
            "open_time_ms": bucket,
            "close_time_ms": int(members[-1]["close_time_ms"]),
            "open": float(members[0]["open"]),
            "high": max(float(item["high"]) for item in members),
            "low": min(float(item["low"]) for item in members),
            "close": float(members[-1]["close"]),
            "quote_volume": sum(float(item["quote_volume"]) for item in members),
        })
    return output


def frame_metrics(rows):
    if len(rows) < 24:
        raise ValueError("insufficient technical frame history")
    closes = [float(row["close"]) for row in rows]
    latest = closes[-1]
    atr14 = _atr(rows)
    atr_scale = max(atr14, latest * 1e-8)
    ema9, ema21 = _ema(closes, 9), _ema(closes, 21)
    rsi14 = _rsi(closes, 14)
    momentum5 = closes[-1] - closes[-6]
    support20 = min(float(row["low"]) for row in rows[-20:])
    resistance20 = max(float(row["high"]) for row in rows[-20:])
    support48 = min(float(row["low"]) for row in rows[-48:]) if len(rows) >= 48 else support20
    resistance48 = max(float(row["high"]) for row in rows[-48:]) if len(rows) >= 48 else resistance20
    raw_score = statistics.fmean((
        math.tanh((latest - ema21) / atr_scale),
        math.tanh((ema9 - ema21) / atr_scale),
        math.tanh(momentum5 / atr_scale),
        _clip((rsi14 - 50.0) / 25.0, -1.0, 1.0),
    ))
    return {
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
        "trend_score": _clip(raw_score, -1.0, 1.0),
    }


def _trend_value(value):
    text = str(value or "MIXED").upper()
    return 1.0 if text == "BULLISH" else (-1.0 if text == "BEARISH" else 0.0)


def choose_timeframes(candidate, frames, higher):
    """Select only the horizons relevant to this setup; never use all blindly."""
    selected = ["1m", "5m", "15m", "1h"]
    five, fifteen = frames["5m"], frames["15m"]
    if (
        int(candidate.get("horizon") or candidate.get("horizon_hours") or 4) >= 4
        or five["trend_score"] * fifteen["trend_score"] <= 0
        or max(five["atr14_pct"], fifteen["atr14_pct"]) >= 1.5
    ):
        selected.append("4h")

    event_kind = str(((candidate.get("evidence") or {}).get("event") or {}).get("kind") or "").upper()
    horizon = int(candidate.get("horizon") or candidate.get("horizon_hours") or 4)
    regime = (higher or {}).get("regime") or {}
    requested_long = []
    if horizon >= 24 or event_kind in {"NEWS", "ONCHAIN", "WHALE", "MACRO"}:
        requested_long.extend(["1d", "3d"])
    if horizon >= 72 or event_kind == "MACRO":
        requested_long.append("1w")
    if horizon >= 168 or event_kind == "MACRO":
        requested_long.append("1M")
    missing = [name for name in requested_long if name not in regime]
    if missing:
        raise ValueError("required long-regime technical context unavailable: " + ",".join(missing))
    selected.extend(requested_long)
    return list(dict.fromkeys(selected))


def _weighted_score(direction, selected, frames, higher):
    side = 1.0 if direction == "LONG" else -1.0
    scores = {
        "1m": frames["1m"]["trend_score"],
        "5m": frames["5m"]["trend_score"],
        "15m": frames["15m"]["trend_score"],
        "1h": _trend_value(((higher or {}).get("summary") or {}).get("trend_1h")),
        "4h": _trend_value(((higher or {}).get("summary") or {}).get("trend_4h")),
    }
    for name, payload in ((higher or {}).get("regime") or {}).items():
        scores[name] = _trend_value(payload.get("trend"))
    base_weights = {
        "1m": 0.10, "5m": 0.20, "15m": 0.25, "1h": 0.20, "4h": 0.15,
        "1d": 0.05, "3d": 0.025, "1w": 0.025, "1M": 0.025,
    }
    active = [(name, base_weights[name], scores.get(name, 0.0)) for name in selected]
    weight_sum = sum(weight for _, weight, _ in active) or 1.0
    return _clip(
        side * sum(weight * score for _, weight, score in active) / weight_sum,
        -1.0, 1.0,
    )


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


def build_for_candidate(con, candidate, *, now=None, market_type):
    now = now or datetime.now(timezone.utc)
    symbol = str(candidate["symbol"]).upper()
    direction = str(candidate["direction"]).upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("invalid technical-plan direction")

    latest = con.execute("""
        SELECT MAX(close_time_ms) FROM technical_minute_bars
        WHERE symbol=? AND market_type='USD_M_PERPETUAL' AND provider='BINANCE'
          AND close_time_ms<=?
    """, (symbol, int(now.timestamp() * 1000))).fetchone()[0]
    if latest is None:
        raise ValueError("technical minute bar cache unavailable")
    higher = realtime_technical_context.load_latest(con, symbol, now=now)
    if higher is None:
        raise ValueError("fresh higher-timeframe technical context unavailable")
    event_kind = str(((candidate.get("evidence") or {}).get("event") or {}).get("kind") or "")
    horizon = int(candidate.get("horizon") or candidate.get("horizon_hours") or 4)
    key = (
        symbol, direction, event_kind, horizon, str(market_type),
        int(latest), str(higher.get("generated_at") or ""),
    )
    cached = _PLAN_CACHE.get(key)
    if cached is not None:
        return cached

    rows = load_recent(
        con, symbol, limit=1440, before_ms=int(now.timestamp() * 1000)
    )
    if len(rows) < MIN_ONE_MINUTE_BARS:
        raise ValueError(
            f"insufficient exact USD-M technical bars ({len(rows)}/{MIN_ONE_MINUTE_BARS})"
        )
    latest_age = now.timestamp() - float(rows[-1]["close_time_ms"]) / 1000.0
    if latest_age < -1.0 or latest_age > MAX_BAR_AGE_SECONDS:
        raise ValueError("technical minute bar cache is stale")
    one = rows
    five = aggregate(one, 5)
    fifteen = aggregate(one, 15)
    frames = {
        "1m": frame_metrics(one),
        "5m": frame_metrics(five),
        "15m": frame_metrics(fifteen),
    }

    selected = choose_timeframes(candidate, frames, higher)
    score = _weighted_score(direction, selected, frames, higher)
    relation = (
        "SUPPORTS_DIRECTION" if score >= 0.25
        else "OPPOSES_DIRECTION" if score <= -0.25
        else "MIXED_OR_UNALIGNED"
    )
    quality_multiplier = _clip(1.0 + 0.20 * score, 0.80, 1.20)

    current = frames["1m"]["close"]
    atr_fast = max(frames["5m"]["atr14"], current * 0.0001)
    atr_setup = max(frames["15m"]["atr14"], atr_fast)
    levels = (higher or {}).get("levels") or {}

    supports = _finite_levels([
        frames["1m"]["support20"], frames["5m"]["support20"], frames["5m"]["support48"],
        frames["15m"]["support20"], frames["15m"]["support48"],
        frames["5m"]["ema21"], frames["15m"]["ema21"],
        levels.get("support_1h_72"), levels.get("support_4h_20"),
    ], below=current)
    resistances = _finite_levels([
        frames["1m"]["resistance20"], frames["5m"]["resistance20"], frames["5m"]["resistance48"],
        frames["15m"]["resistance20"], frames["15m"]["resistance48"],
        frames["5m"]["ema21"], frames["15m"]["ema21"],
        levels.get("resistance_1h_72"), levels.get("resistance_4h_20"),
    ], above=current)

    pullback = 0.15 if score >= 0.35 else (0.55 if score <= -0.25 else 0.30)
    zone_half = max(0.12 * atr_setup, 0.20 * atr_fast)

    if direction == "LONG":
        structural = max(supports) if supports else current - atr_setup
        anchor = max(structural, current - pullback * atr_setup)
        entry_low, entry_high = anchor - zone_half, anchor + zone_half
        stop_levels = _finite_levels(supports, below=entry_low + 1e-12)
        stop_structure = max(stop_levels) if stop_levels else entry_low - atr_setup
        stop_price = stop_structure - 0.25 * atr_setup
        worst_entry = entry_high
        stop_pct = 100.0 * (worst_entry - stop_price) / worst_entry
    else:
        structural = min(resistances) if resistances else current + atr_setup
        anchor = min(structural, current + pullback * atr_setup)
        entry_low, entry_high = anchor - zone_half, anchor + zone_half
        stop_levels = _finite_levels(resistances, above=entry_high - 1e-12)
        stop_structure = min(stop_levels) if stop_levels else entry_high + atr_setup
        stop_price = stop_structure + 0.25 * atr_setup
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
        targets = _finite_levels(resistances, above=entry_high)
        tp1 = next((x for x in targets if x >= anchor + 0.60 * atr_setup), anchor + atr_setup)
        tp2 = next((x for x in targets if x >= max(tp1 + 0.50 * atr_setup, anchor + 1.40 * atr_setup)), anchor + 2.0 * atr_setup)
    else:
        targets = sorted(_finite_levels(supports, below=entry_low), reverse=True)
        tp1 = next((x for x in targets if x <= anchor - 0.60 * atr_setup), anchor - atr_setup)
        tp2 = next((x for x in targets if x <= min(tp1 - 0.50 * atr_setup, anchor - 1.40 * atr_setup)), anchor - 2.0 * atr_setup)

    if not all(math.isfinite(x) and x > 0 for x in (anchor, entry_low, entry_high, stop_price, tp1, tp2)):
        raise ValueError("invalid technical plan geometry")

    def pct(level):
        return 100.0 * (float(level) / float(anchor) - 1.0)

    plan = {
        "version": VERSION,
        "symbol": symbol,
        "direction": direction,
        "execution_market_type": str(market_type),
        "technical_source_market": "BINANCE_USD_M_PERPETUAL",
        "source_reference_price": current,
        "generated_at": _iso(now),
        "data_cutoff_ms": int(rows[-1]["close_time_ms"]),
        "timeframes_used": selected,
        "technical_score": score,
        "relation_to_trigger_direction": relation,
        "quality_multiplier": quality_multiplier,
        "entry": {
            "anchor": anchor, "low": entry_low, "high": entry_high,
            "low_offset_pct": pct(entry_low), "high_offset_pct": pct(entry_high),
            "basis": "STRUCTURE_PLUS_ATR_PULLBACK_ZONE",
        },
        "risk": {
            "invalidation_price": stop_price,
            "invalidation_offset_pct": pct(stop_price),
            "stop_distance_pct": stop_pct,
            "risk_compatible": risk_compatible,
            "maximum_policy_stop_pct": MAX_STOP_PCT,
            "basis": "STRUCTURE_PLUS_ATR_BUFFER",
        },
        "targets": {
            "tp1_price": tp1, "tp2_price": tp2,
            "tp1_offset_pct": pct(tp1), "tp2_offset_pct": pct(tp2),
            "basis": "STRUCTURAL_LEVELS_WITH_ATR_PROJECTION_FALLBACK",
        },
        "frames": frames,
        "higher_context_version": higher.get("version"),
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
    _PLAN_CACHE.clear()
    _PLAN_CACHE[key] = plan
    return plan


def execution_levels(plan, execution_reference_price, *, basis_ratio=None):
    """Scale the frozen USD-M plan once to the actual execution market.

    USD-M uses ratio 1. SPOT freezes a single basis ratio at first fresh quote;
    later ticks must reuse that exact ratio so the thesis cannot drift.
    """
    anchor = float(plan["entry"]["anchor"])
    source_reference = float(plan.get("source_reference_price") or anchor)
    reference = float(execution_reference_price)
    if anchor <= 0 or source_reference <= 0 or reference <= 0:
        raise ValueError("invalid technical execution basis")
    market_type = str(plan.get("execution_market_type") or "USD_M_PERPETUAL")
    if basis_ratio is None:
        basis_ratio = (
            1.0 if market_type == "USD_M_PERPETUAL"
            else reference / source_reference
        )
    ratio = float(basis_ratio)
    if not math.isfinite(ratio) or ratio <= 0:
        raise ValueError("invalid frozen technical basis ratio")
    if (
        market_type == "SPOT"
        and abs(ratio - 1.0) * 100.0 > MAX_SPOT_USDM_BASIS_DEVIATION_PCT
    ):
        raise ValueError("spot/usdm technical basis outside policy envelope")
    return {
        "basis_ratio": ratio,
        "entry_low": float(plan["entry"]["low"]) * ratio,
        "entry_high": float(plan["entry"]["high"]) * ratio,
        "invalidation_price": float(plan["risk"]["invalidation_price"]) * ratio,
        "tp1_price": float(plan["targets"]["tp1_price"]) * ratio,
        "tp2_price": float(plan["targets"]["tp2_price"]) * ratio,
    }


def plan_is_fresh(plan, now=None):
    now = now or datetime.now(timezone.utc)
    generated = datetime.fromisoformat(
        str(plan["generated_at"]).replace("Z", "+00:00")
    ).astimezone(timezone.utc)
    age = (now - generated).total_seconds()
    return 0 <= age <= MAX_ENTRY_PLAN_AGE_SECONDS
