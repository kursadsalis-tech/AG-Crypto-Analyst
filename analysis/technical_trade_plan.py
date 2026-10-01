"""Adaptive, network-free pre-trade technical execution plan for PAPER candidates."""
from __future__ import annotations

import math
import os
import sqlite3
import statistics
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from analysis import realtime_technical_context
from database.technical_bar_store import load_recent


VERSION = "TECHNICAL_TRADE_PLAN_V3_ORDERED_TARGETS"
MIN_ONE_MINUTE_BARS = 24
MAX_BAR_AGE_SECONDS = 180
MIN_STOP_PCT = 0.50
MAX_STOP_PCT = 5.00
MAX_ENTRY_PLAN_AGE_SECONDS = 180
MAX_SPOT_USDM_BASIS_DEVIATION_PCT = 5.0
STRUCTURE_REFRESH_SECONDS = 120
_PLAN_CACHE = {}


@contextmanager
def _technical_reader(canonical_connection):
    """Read technical bars from the dedicated local cache when configured."""
    path = str(os.getenv("AGCA_TECHNICAL_DATABASE_PATH") or "").strip()
    if not path:
        yield canonical_connection
        return
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.25)
    connection.execute("PRAGMA query_only=ON")
    try:
        yield connection
    finally:
        connection.close()


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


def contiguous_tail(rows):
    """Return only the newest gap-free 1m sequence.

    Archive/bootstrap history may be older than the live stream. Technical
    indicators must never bridge a missing interval as if those bars were
    adjacent.
    """
    ordered = sorted(rows, key=lambda item: int(item["open_time_ms"]))
    if not ordered:
        return []
    start = 0
    for index in range(1, len(ordered)):
        delta = (
            int(ordered[index]["open_time_ms"])
            - int(ordered[index - 1]["open_time_ms"])
        )
        if delta != 60_000:
            start = index
    return ordered[start:]


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
    """Select only horizons that are relevant *and* actually available."""
    selected = [
        name for name in ("1m", "5m", "15m") if name in frames
    ]
    selected.append("1h")

    lower = [frames[name] for name in ("5m", "15m") if name in frames]
    conflict = (
        len(lower) >= 2
        and lower[0]["trend_score"] * lower[1]["trend_score"] <= 0
    )
    elevated_lower_volatility = bool(
        lower and max(item["atr14_pct"] for item in lower) >= 1.5
    )
    horizon = int(
        candidate.get("horizon") or candidate.get("horizon_hours") or 4
    )
    if horizon >= 4 or conflict or elevated_lower_volatility:
        selected.append("4h")

    event_kind = str(
        ((candidate.get("evidence") or {}).get("event") or {}).get("kind") or ""
    ).upper()
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
        raise ValueError(
            "required long-regime technical context unavailable: "
            + ",".join(missing)
        )
    selected.extend(requested_long)
    return list(dict.fromkeys(selected))


def _weighted_score(direction, selected, frames, higher):
    side = 1.0 if direction == "LONG" else -1.0
    scores = {
        name: payload["trend_score"] for name, payload in frames.items()
    }
    scores.update({
        "1h": _trend_value(
            ((higher or {}).get("summary") or {}).get("trend_1h")
        ),
        "4h": _trend_value(
            ((higher or {}).get("summary") or {}).get("trend_4h")
        ),
    })
    for name, payload in ((higher or {}).get("regime") or {}).items():
        scores[name] = _trend_value(payload.get("trend"))
    base_weights = {
        "1m": 0.10, "5m": 0.20, "15m": 0.25,
        "1h": 0.20, "4h": 0.15,
        "1d": 0.05, "3d": 0.025, "1w": 0.025, "1M": 0.025,
    }
    active = [
        (name, base_weights[name], scores.get(name, 0.0))
        for name in selected
    ]
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

    now_ms = int(now.timestamp() * 1000)
    with _technical_reader(con) as technical_con:
        latest = technical_con.execute("""
            SELECT MAX(close_time_ms) FROM technical_minute_bars
            WHERE symbol=? AND market_type='USD_M_PERPETUAL' AND provider='BINANCE'
              AND close_time_ms<=?
        """, (symbol, now_ms)).fetchone()[0]
        if latest is None:
            raise ValueError("technical minute bar cache unavailable")
        latest_age = now.timestamp() - float(latest) / 1000.0
        if latest_age < -1.0 or latest_age > MAX_BAR_AGE_SECONDS:
            raise ValueError("technical minute bar cache is stale")

        # Reject an incomplete warm-up from the required tail before reading a
        # whole day of history on every candidate scan. The eligibility
        # condition is identical; only eligible candidates incur deeper reads.
        required_tail = contiguous_tail(load_recent(
            technical_con, symbol, limit=MIN_ONE_MINUTE_BARS,
            before_ms=now_ms,
        ))
        if len(required_tail) < MIN_ONE_MINUTE_BARS:
            raise ValueError(
                "insufficient contiguous exact USD-M technical bars "
                f"({len(required_tail)}/{MIN_ONE_MINUTE_BARS})"
            )
        rows = load_recent(
            technical_con, symbol, limit=1440, before_ms=now_ms
        )

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

    one = contiguous_tail(rows)
    if len(one) < MIN_ONE_MINUTE_BARS:
        raise ValueError("technical bar tail changed during plan construction")
    frames = {"1m": frame_metrics(one)}
    five = aggregate(one, 5)
    if len(five) >= 24:
        frames["5m"] = frame_metrics(five)
    fifteen = aggregate(one, 15)
    if len(fifteen) >= 24:
        frames["15m"] = frame_metrics(fifteen)

    selected = choose_timeframes(candidate, frames, higher)
    score = _weighted_score(direction, selected, frames, higher)
    relation = (
        "SUPPORTS_DIRECTION" if score >= 0.25
        else "OPPOSES_DIRECTION" if score <= -0.25
        else "MIXED_OR_UNALIGNED"
    )
    quality_multiplier = _clip(1.0 + 0.20 * score, 0.80, 1.20)

    current = frames["1m"]["close"]
    fast_name = "5m" if "5m" in frames else "1m"
    setup_name = (
        "15m" if "15m" in frames
        else "5m" if "5m" in frames
        else "1m"
    )
    atr_fast = max(frames[fast_name]["atr14"], current * 0.0001)
    atr_setup = max(frames[setup_name]["atr14"], atr_fast)
    levels = (higher or {}).get("levels") or {}

    support_values = [
        levels.get("support_1h_72"), levels.get("support_4h_20")
    ]
    resistance_values = [
        levels.get("resistance_1h_72"), levels.get("resistance_4h_20")
    ]
    for frame in frames.values():
        support_values.extend([
            frame["support20"], frame["support48"], frame["ema21"],
        ])
        resistance_values.extend([
            frame["resistance20"], frame["resistance48"], frame["ema21"],
        ])
    supports = _finite_levels(support_values, below=current)
    resistances = _finite_levels(resistance_values, above=current)

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
        tp1_floor = max(
            entry_high + 0.50 * atr_setup,
            anchor + 0.60 * atr_setup,
        )
        tp1 = next(
            (x for x in targets if x >= tp1_floor),
            max(anchor + atr_setup, tp1_floor),
        )
        tp2_floor = max(
            tp1 + 0.50 * atr_setup,
            anchor + 1.40 * atr_setup,
        )
        tp2 = next(
            (x for x in targets if x >= tp2_floor),
            max(anchor + 2.0 * atr_setup, tp2_floor),
        )
    else:
        targets = sorted(_finite_levels(supports, below=entry_low), reverse=True)
        tp1_ceiling = min(
            entry_low - 0.50 * atr_setup,
            anchor - 0.60 * atr_setup,
        )
        tp1 = next(
            (x for x in targets if x <= tp1_ceiling),
            min(anchor - atr_setup, tp1_ceiling),
        )
        tp2_ceiling = min(
            tp1 - 0.50 * atr_setup,
            anchor - 1.40 * atr_setup,
        )
        tp2 = next(
            (x for x in targets if x <= tp2_ceiling),
            min(anchor - 2.0 * atr_setup, tp2_ceiling),
        )

    if not all(
        math.isfinite(x) and x > 0
        for x in (anchor, entry_low, entry_high, stop_price, tp1, tp2)
    ):
        raise ValueError("invalid technical plan geometry")
    ordered_geometry = (
        stop_price < entry_low <= entry_high < tp1 < tp2
        if direction == "LONG"
        else tp2 < tp1 < entry_low <= entry_high < stop_price
    )
    if not ordered_geometry:
        raise ValueError("unordered technical plan geometry")

    def pct(level):
        return 100.0 * (float(level) / float(anchor) - 1.0)

    data_cutoff_ms = int(one[-1]["close_time_ms"])
    as_of = datetime.fromtimestamp(data_cutoff_ms / 1000.0, tz=timezone.utc)
    setup_atr_pct = float(frames[setup_name]["atr14_pct"])
    replan_move_pct = _clip(max(0.15, 0.50 * setup_atr_pct), 0.15, 1.25)
    evidence = candidate.get("evidence") or {}
    event = evidence.get("event") or {}

    plan = {
        "version": VERSION,
        "symbol": symbol,
        "direction": direction,
        "execution_market_type": str(market_type),
        "technical_source_market": "BINANCE_USD_M_PERPETUAL",
        "source_reference_price": current,
        "generated_at": _iso(now),
        "as_of": _iso(as_of),
        "data_cutoff_ms": data_cutoff_ms,
        "freshness": {
            "ttl_seconds": MAX_ENTRY_PLAN_AGE_SECONDS,
            "maximum_market_data_age_seconds": MAX_BAR_AGE_SECONDS,
            "structure_refresh_seconds": STRUCTURE_REFRESH_SECONDS,
            "replan_price_move_pct": replan_move_pct,
        },
        "trigger": {
            "event_kind": str(event.get("kind") or ""),
            "horizon_hours": int(
                candidate.get("horizon") or candidate.get("horizon_hours") or 4
            ),
            "primary_signal_source": evidence.get("primary_signal_source"),
            "evidence_minute_end_ms": evidence.get("evidence_minute_end_ms"),
        },
        "contiguous_one_minute_bar_count": len(one),
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
        "higher_context_generated_at": higher.get("generated_at"),
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


def _parse_time(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def plan_is_fresh(plan, now=None):
    """Validate both plan age and the market-data snapshot it was built from."""
    now = now or datetime.now(timezone.utc)
    try:
        generated = _parse_time(plan["generated_at"])
        as_of_value = plan.get("as_of")
        if as_of_value is None:
            as_of_value = _iso(datetime.fromtimestamp(
                int(plan["data_cutoff_ms"]) / 1000.0, tz=timezone.utc
            ))
        as_of = _parse_time(as_of_value)
        freshness = plan.get("freshness") or {}
        ttl = float(freshness.get("ttl_seconds", MAX_ENTRY_PLAN_AGE_SECONDS))
        max_data_age = float(
            freshness.get("maximum_market_data_age_seconds", MAX_BAR_AGE_SECONDS)
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    generated_age = (now - generated).total_seconds()
    data_age = (now - as_of).total_seconds()
    return (
        0 <= generated_age <= ttl
        and 0 <= data_age <= max_data_age
    )


def replan_reason(con, plan, now=None):
    """Return why a pending entry needs a fresh local plan, else None.

    This function is deliberately local-only. It never performs network I/O.
    """
    now = now or datetime.now(timezone.utc)
    try:
        symbol = str(plan["symbol"]).upper()
        reference = float(plan["source_reference_price"])
        previous_cutoff = int(plan["data_cutoff_ms"])
        freshness = plan.get("freshness") or {}
        move_threshold = float(
            freshness.get("replan_price_move_pct", 0.50)
        )
        structure_refresh = int(
            freshness.get("structure_refresh_seconds", STRUCTURE_REFRESH_SECONDS)
        )
    except (KeyError, TypeError, ValueError):
        return "MALFORMED_TECHNICAL_PLAN"

    if not plan_is_fresh(plan, now):
        return "PLAN_OR_MARKET_DATA_STALE"

    with _technical_reader(con) as technical_con:
        row = technical_con.execute("""
            SELECT close_time_ms,close FROM technical_minute_bars
            WHERE symbol=? AND market_type='USD_M_PERPETUAL' AND provider='BINANCE'
              AND close_time_ms<=?
            ORDER BY close_time_ms DESC LIMIT 1
        """, (symbol, int(now.timestamp() * 1000))).fetchone()
    if not row:
        return "LOCAL_TECHNICAL_DATA_UNAVAILABLE"
    latest_cutoff, latest_close = int(row[0]), float(row[1])
    latest_age = now.timestamp() - latest_cutoff / 1000.0
    if latest_age < -1.0 or latest_age > MAX_BAR_AGE_SECONDS:
        return "LOCAL_TECHNICAL_DATA_STALE"
    if not math.isfinite(reference) or reference <= 0:
        return "MALFORMED_TECHNICAL_PLAN"
    if not math.isfinite(latest_close) or latest_close <= 0:
        return "LOCAL_TECHNICAL_DATA_INVALID"

    move_pct = abs(latest_close / reference - 1.0) * 100.0
    if move_pct > move_threshold:
        return "SOURCE_PRICE_MOVED_MATERIALLY"
    if latest_cutoff - previous_cutoff >= structure_refresh * 1000:
        return "STRUCTURE_REFRESH_DUE"

    current_higher = realtime_technical_context.load_latest(con, symbol, now=now)
    if current_higher is None:
        return "HIGHER_TECHNICAL_CONTEXT_UNAVAILABLE"
    previous_higher_at = plan.get("higher_context_generated_at")
    current_higher_at = current_higher.get("generated_at")
    if (
        previous_higher_at
        and current_higher_at
        and str(current_higher_at) != str(previous_higher_at)
    ):
        return "HIGHER_CONTEXT_REFRESHED"
    return None


def rebuild_for_pending(con, plan, *, now=None, market_type=None):
    """Rebuild a pending plan from local technical and canonical context."""
    now = now or datetime.now(timezone.utc)
    trigger = plan.get("trigger") or {}
    candidate = {
        "symbol": plan["symbol"],
        "direction": plan["direction"],
        "horizon": int(trigger.get("horizon_hours") or 4),
        "evidence": {
            "event": {"kind": trigger.get("event_kind") or "MARKET_STRUCTURE"},
            "primary_signal_source": trigger.get("primary_signal_source"),
            "evidence_minute_end_ms": trigger.get("evidence_minute_end_ms"),
        },
    }
    return build_for_candidate(
        con,
        candidate,
        now=now,
        market_type=market_type or plan.get("execution_market_type")
        or "USD_M_PERPETUAL",
    )
