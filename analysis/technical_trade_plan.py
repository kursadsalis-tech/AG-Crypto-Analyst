"""Authoritative pre-trade technical plan built entirely from local evidence.

A behavior / price-action / external-event candidate is an opportunity trigger,
not an automatic entry.  This module combines:
- compact closed USD-M minute bars already persisted by the Binance observer,
- off-hot-path 1h/4h and long-regime Binance support context,
- structure, momentum and volatility across only the timeframes that are useful.

No network call is permitted here.  No single indicator can veto a direction.
Execution can still fail closed when the resulting stop geometry is not
compatible with the risk envelope or when support context is unavailable.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone


VERSION = "TECHNICAL_TRADE_PLAN_V2_LOCAL_ADAPTIVE"
MIN_STOP_PCT = 0.50
MAX_STOP_PCT = 5.00
MAX_MINUTE_AGE_SECONDS = 180
MAX_MINUTE_ROWS = 720
MIN_FRAME_BARS = 24

_CACHE = {}
_CACHE_LIMIT = 256


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
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes[-period:]]
    losses = [max(-change, 0.0) for change in changes[-period:]]
    gain, loss = sum(gains), sum(losses)
    if loss <= 0:
        return 100.0
    ratio = gain / loss
    return 100.0 - 100.0 / (1.0 + ratio)


def _atr(rows, period=14):
    if len(rows) <= period:
        raise ValueError("insufficient ATR history")
    ranges = []
    for index in range(1, len(rows)):
        previous = rows[index - 1]["close"]
        ranges.append(max(
            rows[index]["high"] - rows[index]["low"],
            abs(rows[index]["high"] - previous),
            abs(rows[index]["low"] - previous),
        ))
    return statistics.fmean(ranges[-period:])


def _trend_value(label):
    value = str(label or "MIXED").upper()
    return 1.0 if value == "BULLISH" else (-1.0 if value == "BEARISH" else 0.0)


def _finite_levels(values, *, below=None, above=None):
    result = []
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
        result.append(parsed)
    return sorted(set(result))


def _instrument(symbol):
    value = str(symbol).upper()
    if not value.endswith("USDT"):
        raise ValueError("technical plan requires USDT symbol")
    return f"{value[:-4]}-USDT-SWAP"


def _load_minute_bars(con, candidate, now):
    columns = {row[1] for row in con.execute(
        "PRAGMA table_info(market_behavior_minute_features)"
    ).fetchall()}
    required = {
        "market_data_provider", "mid_price_first", "mid_price_high",
        "mid_price_low", "mid_price_last",
        "aggressive_buy_notional_usd", "aggressive_sell_notional_usd",
    }
    if not required.issubset(columns):
        return []

    cutoff = int(
        (candidate.get("evidence") or {}).get(
            "evidence_minute_end_ms", int(now.timestamp() * 1000)
        )
    )
    rows = con.execute("""
        SELECT minute_start_timestamp_ms,minute_end_timestamp_ms,
               mid_price_first,mid_price_high,mid_price_low,mid_price_last,
               aggressive_buy_notional_usd,aggressive_sell_notional_usd
        FROM market_behavior_minute_features
        WHERE instrument=? AND market_data_provider='BINANCE'
          AND eligible_for_learning=1 AND minute_end_timestamp_ms<=?
          AND mid_price_first IS NOT NULL AND mid_price_high IS NOT NULL
          AND mid_price_low IS NOT NULL AND mid_price_last IS NOT NULL
        ORDER BY minute_end_timestamp_ms DESC LIMIT ?
    """, (_instrument(candidate["symbol"]), cutoff, MAX_MINUTE_ROWS)).fetchall()
    if not rows:
        return []

    normalized = []
    for raw in reversed(rows):
        start_ms, end_ms, opened, high, low, close, buy, sell = raw
        values = [float(opened), float(high), float(low), float(close)]
        if (
            not all(math.isfinite(value) and value > 0 for value in values)
            or values[1] < max(values[0], values[3])
            or values[2] > min(values[0], values[3])
            or values[1] < values[2]
        ):
            continue
        buy, sell = float(buy or 0.0), float(sell or 0.0)
        total = max(0.0, buy) + max(0.0, sell)
        normalized.append({
            "open_ms": int(start_ms),
            "close_ms": int(end_ms),
            "open": values[0], "high": values[1],
            "low": values[2], "close": values[3],
            "aggressive_notional": total,
            "flow_imbalance": (
                (buy - sell) / total if total > 0 else 0.0
            ),
        })

    if not normalized:
        return []
    latest_age = (
        now.timestamp() * 1000 - normalized[-1]["close_ms"]
    ) / 1000.0
    if latest_age < -1.0 or latest_age > MAX_MINUTE_AGE_SECONDS:
        return []

    # Only the trailing contiguous sequence may feed the execution plan.
    contiguous = [normalized[-1]]
    for row in reversed(normalized[:-1]):
        if contiguous[-1]["open_ms"] - row["open_ms"] != 60_000:
            break
        contiguous.append(row)
    return list(reversed(contiguous))


def _aggregate(rows, minutes):
    period_ms = int(minutes) * 60_000
    grouped = {}
    for row in rows:
        bucket = row["open_ms"] - row["open_ms"] % period_ms
        grouped.setdefault(bucket, []).append(row)
    result = []
    for bucket, members in sorted(grouped.items()):
        members = sorted(members, key=lambda item: item["open_ms"])
        expected = [
            bucket + 60_000 * index for index in range(int(minutes))
        ]
        if (
            len(members) != int(minutes)
            or [row["open_ms"] for row in members] != expected
        ):
            continue
        total_flow = sum(row["aggressive_notional"] for row in members)
        weighted_flow = sum(
            row["flow_imbalance"] * row["aggressive_notional"]
            for row in members
        )
        result.append({
            "open_ms": bucket,
            "close_ms": members[-1]["close_ms"],
            "open": members[0]["open"],
            "high": max(row["high"] for row in members),
            "low": min(row["low"] for row in members),
            "close": members[-1]["close"],
            "aggressive_notional": total_flow,
            "flow_imbalance": (
                weighted_flow / total_flow if total_flow > 0 else 0.0
            ),
        })
    return result


def _frame_metrics(rows):
    if len(rows) < MIN_FRAME_BARS:
        raise ValueError("insufficient technical frame history")
    closes = [row["close"] for row in rows]
    latest = closes[-1]
    atr14 = _atr(rows)
    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    rsi14 = _rsi(closes)
    momentum5 = closes[-1] - closes[-6]
    support20 = min(row["low"] for row in rows[-20:])
    resistance20 = max(row["high"] for row in rows[-20:])
    support48 = (
        min(row["low"] for row in rows[-48:])
        if len(rows) >= 48 else support20
    )
    resistance48 = (
        max(row["high"] for row in rows[-48:])
        if len(rows) >= 48 else resistance20
    )
    atr_scale = max(atr14, latest * 1e-8)
    trend_score = statistics.fmean([
        math.tanh((latest - ema21) / atr_scale),
        math.tanh((ema9 - ema21) / atr_scale),
        math.tanh(momentum5 / atr_scale),
        _clip((rsi14 - 50.0) / 25.0, -1.0, 1.0),
        _clip(statistics.fmean(
            row["flow_imbalance"] for row in rows[-min(6, len(rows)):]
        ), -1.0, 1.0),
    ])
    return {
        "bar_count": len(rows),
        "as_of_ms": rows[-1]["close_ms"],
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
        "flow_imbalance": statistics.fmean(
            row["flow_imbalance"] for row in rows[-min(6, len(rows)):]
        ),
        "trend_score": _clip(trend_score, -1.0, 1.0),
    }


def _lower_frames(minute_bars):
    result = {}
    candidates = {
        "1m": minute_bars,
        "5m": _aggregate(minute_bars, 5),
        "15m": _aggregate(minute_bars, 15),
    }
    for name, rows in candidates.items():
        try:
            result[name] = _frame_metrics(rows)
        except ValueError:
            continue
    return result


def _select_timeframes(candidate, lower, higher):
    horizon = int(candidate.get("horizon") or 4)
    evidence = candidate.get("evidence") or {}
    primary = str(evidence.get("primary_signal_source") or "").upper()
    summary = higher.get("summary") or {}
    available = set(lower) | set(higher.get("timeframes") or [])

    selected = []
    def add(name):
        if name in available and name not in selected:
            selected.append(name)

    if primary in {"MARKET_BEHAVIOR", "PRICE_ACTION", "MARKET_STRUCTURE"}:
        add("1m")
        add("5m")
        add("15m")
    else:
        add("5m")
        add("15m")
    add("1h")
    add("4h")

    conflict = (
        _trend_value(summary.get("trend_1h"))
        * _trend_value(summary.get("trend_4h")) <= 0
    )
    if horizon >= 24 or conflict:
        add("1d")
        add("3d")

    if horizon >= 24:
        add("1w")
    elif (
        "1d" in selected and "3d" in selected
        and _trend_value(summary.get("trend_1d"))
        * _trend_value(summary.get("trend_3d")) <= 0
    ):
        add("1w")

    if "1w" in selected and (
        horizon >= 24
        or _trend_value(summary.get("trend_1w"))
        * _trend_value(summary.get("trend_1d")) <= 0
    ):
        add("1M")
    return selected


def _score_frames(direction, selected, lower, higher):
    side = 1.0 if direction == "LONG" else -1.0
    base_weights = {
        "1m": 0.10, "5m": 0.16, "15m": 0.20,
        "1h": 0.24, "4h": 0.24,
        "1d": 0.16, "3d": 0.12, "1w": 0.08, "1M": 0.05,
    }
    summary = higher.get("summary") or {}
    raw = {}
    for name in selected:
        if name in lower:
            raw[name] = side * float(lower[name]["trend_score"])
        else:
            raw[name] = side * _trend_value(summary.get(f"trend_{name}"))
    total_weight = sum(base_weights[name] for name in raw) or 1.0
    score = sum(
        raw[name] * base_weights[name] for name in raw
    ) / total_weight
    return _clip(score, -1.0, 1.0), raw


def _structure_levels(current, lower, higher):
    supports, resistances = [], []
    for frame in lower.values():
        supports.extend([
            frame.get("support20"), frame.get("support48"), frame.get("ema21")
        ])
        resistances.extend([
            frame.get("resistance20"), frame.get("resistance48"), frame.get("ema21")
        ])
    levels = higher.get("levels") or {}
    supports.extend([
        levels.get("support_1h_72"), levels.get("support_4h_20")
    ])
    resistances.extend([
        levels.get("resistance_1h_72"), levels.get("resistance_4h_20")
    ])
    for frame in (higher.get("long_frames") or {}).values():
        supports.append(frame.get("support"))
        resistances.append(frame.get("resistance"))
    return (
        _finite_levels(supports, below=current),
        _finite_levels(resistances, above=current),
    )


def _setup_atr(current, lower, higher):
    for name in ("15m", "5m", "1m"):
        frame = lower.get(name)
        if frame and float(frame.get("atr14") or 0) > 0:
            return max(float(frame["atr14"]), current * 0.0001), name
    levels = higher.get("levels") or {}
    for key, name in (("atr_1h", "1h"), ("atr_4h", "4h")):
        value = float(levels.get(key) or 0.0)
        if value > 0:
            return value, name
    raise ValueError("technical volatility context unavailable")


def _build_geometry(direction, current, score, supports, resistances, atr):
    zone_half = max(0.15 * atr, current * 0.0005)
    pullback = 0.15 if score >= 0.35 else (0.55 if score <= -0.25 else 0.30)

    if direction == "LONG":
        nearest = max(supports) if supports else current - atr
        anchor = max(nearest, current - pullback * atr)
        entry_low, entry_high = anchor - zone_half, anchor + zone_half
        if score >= 0.35:
            entry_high = max(entry_high, current + 0.03 * atr)
        deeper = [value for value in supports if value < entry_low]
        structural = max(deeper) if deeper else entry_low - atr
        stop = structural - 0.25 * atr
        stop_pct = 100.0 * (entry_high - stop) / entry_high
        if stop_pct < MIN_STOP_PCT:
            stop_pct = MIN_STOP_PCT
            stop = entry_high * (1.0 - stop_pct / 100.0)

        targets = [value for value in resistances if value > entry_high]
        minimum_tp1 = anchor + 0.60 * atr
        tp1s = [value for value in targets if value >= minimum_tp1]
        tp1 = tp1s[0] if tp1s else anchor + atr
        tp2s = [
            value for value in targets
            if value >= max(tp1 + 0.50 * atr, anchor + 1.40 * atr)
        ]
        tp2 = tp2s[0] if tp2s else anchor + 2.0 * atr
        reward1 = 100.0 * (tp1 - anchor) / anchor
        reward2 = 100.0 * (tp2 - anchor) / anchor
    else:
        nearest = min(resistances) if resistances else current + atr
        anchor = min(nearest, current + pullback * atr)
        entry_low, entry_high = anchor - zone_half, anchor + zone_half
        if score >= 0.35:
            entry_low = min(entry_low, current - 0.03 * atr)
        higher = [value for value in resistances if value > entry_high]
        structural = min(higher) if higher else entry_high + atr
        stop = structural + 0.25 * atr
        stop_pct = 100.0 * (stop - entry_low) / entry_low
        if stop_pct < MIN_STOP_PCT:
            stop_pct = MIN_STOP_PCT
            stop = entry_low * (1.0 + stop_pct / 100.0)

        targets = sorted(
            [value for value in supports if value < entry_low],
            reverse=True,
        )
        maximum_tp1 = anchor - 0.60 * atr
        tp1s = [value for value in targets if value <= maximum_tp1]
        tp1 = tp1s[0] if tp1s else anchor - atr
        tp2s = [
            value for value in targets
            if value <= min(tp1 - 0.50 * atr, anchor - 1.40 * atr)
        ]
        tp2 = tp2s[0] if tp2s else anchor - 2.0 * atr
        reward1 = 100.0 * (anchor - tp1) / anchor
        reward2 = 100.0 * (anchor - tp2) / anchor

    values = (entry_low, entry_high, anchor, stop, tp1, tp2)
    if (
        not all(math.isfinite(value) and value > 0 for value in values)
        or entry_low > entry_high
        or reward1 <= 0
        or reward2 <= reward1
    ):
        raise ValueError("invalid technical trade geometry")

    return {
        "entry": {
            "low": entry_low,
            "high": entry_high,
            "anchor": anchor,
            "basis": "ADAPTIVE_STRUCTURE_AND_VOLATILITY",
        },
        "risk": {
            "invalidation_price": stop,
            "stop_distance_pct": stop_pct,
            "risk_compatible": bool(stop_pct <= MAX_STOP_PCT),
            "minimum_policy_stop_pct": MIN_STOP_PCT,
            "maximum_policy_stop_pct": MAX_STOP_PCT,
            "basis": "STRUCTURE_PLUS_VOLATILITY_BUFFER",
        },
        "targets": {
            "tp1_price": tp1,
            "tp2_price": tp2,
            "tp1_reward_pct": reward1,
            "tp2_reward_pct": reward2,
            "basis": "MEANINGFUL_STRUCTURE_WITH_VOLATILITY_FALLBACK",
        },
    }


def build_for_candidate(con, candidate, *, now=None):
    """Build a frozen pre-trade plan using local evidence only."""
    from analysis import realtime_technical_context

    now = now or datetime.now(timezone.utc)
    higher = realtime_technical_context.load_latest(
        con, candidate["symbol"], now=now
    )
    if higher is None:
        raise ValueError("fresh higher-timeframe technical context unavailable")

    minute_bars = _load_minute_bars(con, candidate, now)
    lower = _lower_frames(minute_bars)
    selected = _select_timeframes(candidate, lower, higher)
    if not {"1h", "4h"}.issubset(set(selected)):
        raise ValueError("mandatory 1h/4h technical context unavailable")

    cache_key = (
        int(candidate.get("decision_id") or 0),
        str(candidate.get("direction")),
        str(higher.get("generated_at")),
        tuple(selected),
        minute_bars[-1]["close_ms"] if minute_bars else None,
    )
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    direction = str(candidate["direction"]).upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("invalid technical-plan direction")
    current = float(candidate["current_price"])
    if not math.isfinite(current) or current <= 0:
        raise ValueError("invalid technical-plan current price")

    score, frame_scores = _score_frames(
        direction, selected, lower, higher
    )
    supports, resistances = _structure_levels(current, lower, higher)
    atr, atr_timeframe = _setup_atr(current, lower, higher)
    geometry = _build_geometry(
        direction, current, score, supports, resistances, atr
    )

    reward_risk = (
        float(geometry["targets"]["tp1_reward_pct"])
        / max(float(geometry["risk"]["stop_distance_pct"]), 1e-9)
    )
    quality_multiplier = _clip(
        1.0 + 0.12 * score + 0.03 * _clip(reward_risk - 1.0, -1.0, 1.0),
        0.85, 1.15,
    )
    relation = (
        "SUPPORTS_DIRECTION" if score >= 0.25
        else "OPPOSES_DIRECTION" if score <= -0.25
        else "MIXED_OR_UNALIGNED"
    )

    plan = {
        "version": VERSION,
        "symbol": str(candidate["symbol"]).upper(),
        "direction": direction,
        "generated_at": _iso(now),
        "source": "LOCAL_BINANCE_CANONICAL_EVIDENCE",
        "candidate_decision_id": int(candidate.get("decision_id") or 0),
        "candidate_evidence_minute_end_ms": int(
            (candidate.get("evidence") or {}).get(
                "evidence_minute_end_ms", 0
            )
        ),
        "timeframes_available": sorted(
            set(lower) | set(higher.get("timeframes") or [])
        ),
        "timeframes_used": selected,
        "timeframe_scores": frame_scores,
        "technical_score": score,
        "relation_to_trigger_direction": relation,
        "quality_multiplier": quality_multiplier,
        "setup_atr": atr,
        "setup_atr_timeframe": atr_timeframe,
        "lower_frames": {
            key: lower[key] for key in selected if key in lower
        },
        "higher_summary": {
            key: value for key, value in (higher.get("summary") or {}).items()
            if key.startswith("trend_") or key in {
                "alignment", "rsi14", "atr14_pct", "breakout24"
            }
        },
        **geometry,
        "authority": {
            "direction_override": False,
            "ranking_input": True,
            "sizing_input": True,
            "entry_input": True,
            "stop_input": True,
            "take_profit_input": True,
            "single_indicator_hard_veto": False,
            "network_call_in_decision_path": False,
        },
        "real_order_allowed": False,
    }

    _CACHE[cache_key] = plan
    if len(_CACHE) > _CACHE_LIMIT:
        for key in list(_CACHE)[: len(_CACHE) - _CACHE_LIMIT]:
            _CACHE.pop(key, None)
    return plan
