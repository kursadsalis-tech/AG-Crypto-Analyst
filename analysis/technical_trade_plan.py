"""Adaptive pre-trade technical plan for PAPER candidates.

A market-behavior candidate is an opportunity trigger, not an automatic entry.
Before selection, this module builds a technical execution plan from canonical
Binance data plus the already-produced higher-timeframe context.

The technical plan is a REAL decision input for entry geometry, stop geometry,
profit targets and bounded ranking/sizing weight.  It never overrides direction
by itself and no single indicator is a hard veto.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone

import requests


VERSION = "TECHNICAL_TRADE_PLAN_V1_MULTISKILL"
SPOT_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
USDM_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
REQUEST_TIMEOUT_SECONDS = 3
ONE_MINUTE_LIMIT = 720
MIN_ONE_MINUTE_CANDLES = 360
MIN_STOP_PCT = 0.50
MAX_STOP_PCT = 5.00


def _iso(value):
    return value.astimezone(timezone.utc).isoformat()


def _clip(value, low, high):
    return max(low, min(high, float(value)))


def _sign(value):
    return 1.0 if value > 0 else (-1.0 if value < 0 else 0.0)


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


def _normalize_1m(rows, now):
    now_ms = int(now.timestamp() * 1000)
    output = []
    for raw in rows:
        if not isinstance(raw, (list, tuple)) or len(raw) < 8:
            continue
        opened_ms = int(raw[0])
        closed_ms = int(raw[6])
        if closed_ms > now_ms:
            continue
        values = [float(raw[1]), float(raw[2]), float(raw[3]), float(raw[4]), float(raw[7])]
        if (
            not all(math.isfinite(value) for value in values)
            or min(values[:4]) <= 0
            or values[4] < 0
        ):
            continue
        output.append({
            "open_ms": opened_ms,
            "close_ms": closed_ms,
            "open": values[0],
            "high": values[1],
            "low": values[2],
            "close": values[3],
            "quote_volume": values[4],
        })
    output.sort(key=lambda row: row["open_ms"])
    if len(output) < MIN_ONE_MINUTE_CANDLES:
        raise ValueError(
            f"insufficient closed 1m candles ({len(output)}/{MIN_ONE_MINUTE_CANDLES})"
        )
    for previous, current in zip(output, output[1:]):
        if current["open_ms"] - previous["open_ms"] != 60_000:
            raise ValueError("non-contiguous closed 1m candles")
    return output


def aggregate(rows, minutes):
    period_ms = int(minutes) * 60_000
    groups = {}
    for row in rows:
        bucket = row["open_ms"] - (row["open_ms"] % period_ms)
        groups.setdefault(bucket, []).append(row)
    output = []
    for bucket, members in sorted(groups.items()):
        members = sorted(members, key=lambda row: row["open_ms"])
        if len(members) != minutes:
            continue
        expected = [bucket + 60_000 * index for index in range(minutes)]
        if [row["open_ms"] for row in members] != expected:
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
    if len(rows) < 24:
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
    support48 = min(row["low"] for row in rows[-48:]) if len(rows) >= 48 else support20
    resistance48 = max(row["high"] for row in rows[-48:]) if len(rows) >= 48 else resistance20
    atr_scale = max(atr14, latest * 1e-8)
    raw_score = statistics.fmean([
        math.tanh((latest - ema21) / atr_scale),
        math.tanh((ema9 - ema21) / atr_scale),
        math.tanh(momentum5 / atr_scale),
        _clip((rsi14 - 50.0) / 25.0, -1.0, 1.0),
    ])
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
        "momentum5": momentum5,
        "trend_score": _clip(raw_score, -1.0, 1.0),
    }


def _higher_score(context, key):
    if not isinstance(context, dict):
        return 0.0
    summary = context.get("summary") or {}
    trend = str(summary.get(key) or "MIXED").upper()
    return 1.0 if trend == "BULLISH" else (-1.0 if trend == "BEARISH" else 0.0)


def _relation(score):
    if score >= 0.25:
        return "SUPPORTS_DIRECTION"
    if score <= -0.25:
        return "OPPOSES_DIRECTION"
    return "MIXED_OR_UNALIGNED"


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


def build_plan(
    one_minute_rows, *, symbol, direction, market_type, higher_context,
    generated_at=None,
):
    generated_at = generated_at or datetime.now(timezone.utc)
    direction = str(direction).upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("invalid technical-plan direction")
    one = _normalize_1m(one_minute_rows, generated_at)
    five = aggregate(one, 5)
    fifteen = aggregate(one, 15)
    if len(five) < 48 or len(fifteen) < 24:
        raise ValueError("insufficient completed lower-timeframe bars")

    m1 = frame_metrics(one)
    m5 = frame_metrics(five)
    m15 = frame_metrics(fifteen)
    side = 1.0 if direction == "LONG" else -1.0
    score = (
        0.15 * side * m1["trend_score"]
        + 0.25 * side * m5["trend_score"]
        + 0.30 * side * m15["trend_score"]
        + 0.20 * side * _higher_score(higher_context, "trend_1h")
        + 0.10 * side * _higher_score(higher_context, "trend_4h")
    )
    score = _clip(score, -1.0, 1.0)
    relation = _relation(score)
    quality_multiplier = _clip(1.0 + 0.15 * score, 0.85, 1.15)

    current = float(m1["close"])
    atr_fast = max(float(m5["atr14"]), current * 0.0001)
    atr_setup = max(float(m15["atr14"]), atr_fast)
    levels = (higher_context or {}).get("levels") or {}

    support_candidates = _finite_levels([
        m1["support20"], m5["support20"], m5["support48"],
        m15["support20"], m15["support48"],
        m5["ema21"], m15["ema21"],
        levels.get("support_1h_72"), levels.get("support_4h_20"),
    ], below=current)
    resistance_candidates = _finite_levels([
        m1["resistance20"], m5["resistance20"], m5["resistance48"],
        m15["resistance20"], m15["resistance48"],
        m5["ema21"], m15["ema21"],
        levels.get("resistance_1h_72"), levels.get("resistance_4h_20"),
    ], above=current)

    pullback_factor = 0.15 if score >= 0.35 else (0.55 if score <= -0.25 else 0.30)
    zone_half = max(0.12 * atr_setup, 0.20 * atr_fast)

    if direction == "LONG":
        nearest_support = max(support_candidates) if support_candidates else current - atr_setup
        anchor = max(nearest_support, current - pullback_factor * atr_setup)
        entry_low = anchor - zone_half
        entry_high = anchor + zone_half
        if score >= 0.35:
            entry_high = max(entry_high, current + 0.03 * atr_fast)
        stop_supports = _finite_levels(
            support_candidates, below=entry_low + 1e-12
        )
        structural_support = max(stop_supports) if stop_supports else entry_low - atr_setup
        stop_price = structural_support - 0.25 * atr_setup
        worst_entry = entry_high
        stop_pct = 100.0 * (worst_entry - stop_price) / worst_entry
    else:
        nearest_resistance = min(resistance_candidates) if resistance_candidates else current + atr_setup
        anchor = min(nearest_resistance, current + pullback_factor * atr_setup)
        entry_low = anchor - zone_half
        entry_high = anchor + zone_half
        if score >= 0.35:
            entry_low = min(entry_low, current - 0.03 * atr_fast)
        stop_resistances = _finite_levels(
            resistance_candidates, above=entry_high - 1e-12
        )
        structural_resistance = min(stop_resistances) if stop_resistances else entry_high + atr_setup
        stop_price = structural_resistance + 0.25 * atr_setup
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
        targets = _finite_levels([
            m1["resistance20"], m5["resistance20"], m5["resistance48"],
            m15["resistance20"], m15["resistance48"],
            levels.get("resistance_1h_72"), levels.get("resistance_4h_20"),
        ], above=entry_high)
        minimum_tp1 = anchor + 0.60 * atr_setup
        tp1_candidates = [value for value in targets if value >= minimum_tp1]
        tp1 = tp1_candidates[0] if tp1_candidates else anchor + atr_setup
        tp2_candidates = [
            value for value in targets
            if value >= max(tp1 + 0.50 * atr_setup, anchor + 1.40 * atr_setup)
        ]
        tp2 = tp2_candidates[0] if tp2_candidates else anchor + 2.0 * atr_setup
        reward1_pct = 100.0 * (tp1 - anchor) / anchor
        reward2_pct = 100.0 * (tp2 - anchor) / anchor
    else:
        targets = sorted(_finite_levels([
            m1["support20"], m5["support20"], m5["support48"],
            m15["support20"], m15["support48"],
            levels.get("support_1h_72"), levels.get("support_4h_20"),
        ], below=entry_low), reverse=True)
        maximum_tp1 = anchor - 0.60 * atr_setup
        tp1_candidates = [value for value in targets if value <= maximum_tp1]
        tp1 = tp1_candidates[0] if tp1_candidates else anchor - atr_setup
        tp2_candidates = [
            value for value in targets
            if value <= min(tp1 - 0.50 * atr_setup, anchor - 1.40 * atr_setup)
        ]
        tp2 = tp2_candidates[0] if tp2_candidates else anchor - 2.0 * atr_setup
        reward1_pct = 100.0 * (anchor - tp1) / anchor
        reward2_pct = 100.0 * (anchor - tp2) / anchor

    if (
        not all(math.isfinite(value) and value > 0 for value in (
            entry_low, entry_high, anchor, stop_price, tp1, tp2
        ))
        or entry_low > entry_high
        or reward1_pct <= 0
        or reward2_pct <= reward1_pct
    ):
        raise ValueError("invalid technical trade geometry")

    return {
        "version": VERSION,
        "symbol": str(symbol).upper(),
        "direction": direction,
        "market_type": str(market_type),
        "generated_at": _iso(generated_at),
        "source": "BINANCE_CANONICAL",
        "timeframes_used": ["1m", "5m", "15m", "1h", "4h"],
        "technical_score": score,
        "relation_to_behavior_direction": relation,
        "quality_multiplier": quality_multiplier,
        "entry": {
            "low": entry_low,
            "high": entry_high,
            "anchor": anchor,
            "basis": "LOWER_TIMEFRAME_STRUCTURE_ATR_AND_HIGHER_TIMEFRAME_LEVELS",
        },
        "risk": {
            "invalidation_price": stop_price,
            "stop_distance_pct": stop_pct,
            "risk_compatible": risk_compatible,
            "maximum_policy_stop_pct": MAX_STOP_PCT,
            "basis": "STRUCTURE_PLUS_ATR_BUFFER",
        },
        "targets": {
            "tp1_price": tp1,
            "tp2_price": tp2,
            "tp1_reward_pct": reward1_pct,
            "tp2_reward_pct": reward2_pct,
            "basis": "STRUCTURAL_LEVELS_WITH_ATR_PROJECTION_FALLBACK",
        },
        "frames": {"1m": m1, "5m": m5, "15m": m15},
        "higher_context": {
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


def fetch_closed_1m(symbol, market_type, now=None, session=requests):
    now = now or datetime.now(timezone.utc)
    url = SPOT_KLINES_URL if market_type == "SPOT" else USDM_KLINES_URL
    response = session.get(
        url,
        params={"symbol": str(symbol).upper(), "interval": "1m", "limit": ONE_MINUTE_LIMIT},
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": "AG-Crypto-Analyst/technical-trade-plan-v1",
                 "Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("invalid Binance kline payload")
    return _normalize_1m(payload, now)


def build_for_candidate(con, candidate, market_type, now=None, session=requests):
    from analysis import realtime_technical_context

    now = now or datetime.now(timezone.utc)
    higher = realtime_technical_context.load_latest(
        con, candidate["symbol"], now=now
    )
    if higher is None:
        raise ValueError("fresh higher-timeframe technical context unavailable")
    rows = fetch_closed_1m(
        candidate["symbol"], market_type, now=now, session=session
    )
    # build_plan accepts the original kline shape too. Re-expand normalized
    # rows so the same pure validation path is used in production and tests.
    raw = [[
        row["open_ms"], row["open"], row["high"], row["low"], row["close"], 0,
        row["close_ms"], row["quote_volume"], 0, 0, 0, 0,
    ] for row in rows]
    return build_plan(
        raw, symbol=candidate["symbol"], direction=candidate["direction"],
        market_type=market_type, higher_context=higher, generated_at=now,
    )
