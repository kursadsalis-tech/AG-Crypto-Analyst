"""Causal technical context prepared off the realtime execution hot path.

The risk/support cycle computes this context from closed Binance history and
stores only the latest compact snapshot per symbol.  The context itself does
not open trades; the authoritative technical trade-plan layer may consume it
before a PAPER candidate is selected.
"""
from __future__ import annotations

import calendar
import json
import math
import statistics
from datetime import datetime, timezone

from analysis import advanced_technical_features as advanced


VERSION = "REALTIME_TECHNICAL_CONTEXT_V2_MULTITIMEFRAME_SUPPORT"
MIN_HOURLY_CANDLES = advanced.LOOKBACK + 1
MIN_4H_BARS = 21
MIN_DAILY_CANDLES = 90
MAX_GENERATED_AGE_SECONDS = 30 * 60
MAX_CANDLE_AGE_SECONDS = 2 * 60 * 60
MAX_DAILY_CANDLE_AGE_SECONDS = 2 * 86_400


def _iso(value):
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_ratio(numerator, denominator):
    return float(numerator) / max(abs(float(denominator)), 1e-12)


def _normalize(candles, *, minimum, label):
    rows = []
    for raw in candles:
        if not isinstance(raw, (list, tuple)) or len(raw) < 8:
            raise ValueError(f"invalid closed {label} candle")
        opened_ms = int(raw[0])
        closed_ms = int(raw[6])
        opened = datetime.fromtimestamp(opened_ms / 1000.0, tz=timezone.utc)
        closed = datetime.fromtimestamp(closed_ms / 1000.0, tz=timezone.utc)
        values = [
            float(raw[1]), float(raw[2]), float(raw[3]), float(raw[4]),
            float(raw[7]),
        ]
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"non-finite closed {label} candle")
        if min(values[:4]) <= 0 or values[4] < 0:
            raise ValueError(f"invalid price/volume in closed {label} candle")
        rows.append({
            "open_time": opened,
            "close_time": closed,
            "open": values[0],
            "high": values[1],
            "low": values[2],
            "close": values[3],
            "quote_volume": values[4],
        })
    rows.sort(key=lambda row: row["open_time"])
    if len(rows) < minimum:
        raise ValueError(
            f"insufficient closed {label} candles ({len(rows)}/{minimum})"
        )
    for previous, current in zip(rows, rows[1:]):
        if current["open_time"] <= previous["open_time"]:
            raise ValueError(f"duplicate/non-increasing closed {label} candles")
    return rows


def _normalize_hourly(candles):
    return _normalize(
        candles, minimum=MIN_HOURLY_CANDLES, label="1h"
    )


def _normalize_daily(candles):
    return _normalize(
        candles, minimum=MIN_DAILY_CANDLES, label="1d"
    )


def _aggregate_completed_4h(hourly):
    grouped = {}
    for row in hourly:
        opened = row["open_time"]
        start = opened.replace(
            hour=(opened.hour // 4) * 4, minute=0, second=0, microsecond=0
        )
        grouped.setdefault(start, []).append(row)

    output = []
    for start, rows in sorted(grouped.items()):
        if len(rows) != 4:
            continue
        rows = sorted(rows, key=lambda row: row["open_time"])
        expected_opens = [
            start.replace(hour=start.hour + offset) for offset in range(4)
        ]
        if [row["open_time"] for row in rows] != expected_opens:
            continue
        expected_close = start.timestamp() + 4 * 3600 - 0.001
        if abs(rows[-1]["close_time"].timestamp() - expected_close) > 0.002:
            continue
        output.append({
            "open_time": start,
            "close_time": rows[-1]["close_time"],
            "open": rows[0]["open"],
            "high": max(row["high"] for row in rows),
            "low": min(row["low"] for row in rows),
            "close": rows[-1]["close"],
            "quote_volume": sum(row["quote_volume"] for row in rows),
        })
    return output


def _aggregate_3d(daily):
    grouped = {}
    for row in daily:
        ordinal = row["open_time"].date().toordinal()
        grouped.setdefault(ordinal // 3, []).append(row)
    output = []
    for _, rows in sorted(grouped.items()):
        rows = sorted(rows, key=lambda row: row["open_time"])
        if len(rows) != 3:
            continue
        ordinals = [row["open_time"].date().toordinal() for row in rows]
        if ordinals != list(range(ordinals[0], ordinals[0] + 3)):
            continue
        output.append(_combine(rows))
    return output


def _aggregate_weekly(daily):
    grouped = {}
    for row in daily:
        date = row["open_time"].date()
        grouped.setdefault((date.isocalendar().year, date.isocalendar().week), []).append(row)
    output = []
    for _, rows in sorted(grouped.items()):
        rows = sorted(rows, key=lambda row: row["open_time"])
        if len(rows) != 7:
            continue
        if rows[0]["open_time"].weekday() != 0:
            continue
        output.append(_combine(rows))
    return output


def _aggregate_monthly(daily):
    grouped = {}
    for row in daily:
        opened = row["open_time"]
        grouped.setdefault((opened.year, opened.month), []).append(row)
    output = []
    for (year, month), rows in sorted(grouped.items()):
        rows = sorted(rows, key=lambda row: row["open_time"])
        required = calendar.monthrange(year, month)[1]
        if len(rows) != required:
            continue
        if rows[0]["open_time"].day != 1 or rows[-1]["open_time"].day != required:
            continue
        output.append(_combine(rows))
    return output


def _combine(rows):
    return {
        "open_time": rows[0]["open_time"],
        "close_time": rows[-1]["close_time"],
        "open": rows[0]["open"],
        "high": max(row["high"] for row in rows),
        "low": min(row["low"] for row in rows),
        "close": rows[-1]["close"],
        "quote_volume": sum(row["quote_volume"] for row in rows),
    }


def _frame_context(bars, *, support_lookback=20, slow_lookback=10):
    if len(bars) < max(7, slow_lookback):
        raise ValueError("insufficient structural bars")
    closes = [row["close"] for row in bars]
    highs = [row["high"] for row in bars]
    lows = [row["low"] for row in bars]
    volumes = [row["quote_volume"] for row in bars]
    latest = closes[-1]
    support_window = min(len(bars), support_lookback)
    support = min(lows[-support_window:])
    resistance = max(highs[-support_window:])
    fast_n = min(3, len(closes))
    slow_n = min(slow_lookback, len(closes))
    fast = statistics.fmean(closes[-fast_n:])
    slow = statistics.fmean(closes[-slow_n:])
    true_ranges = []
    for index in range(1, len(bars)):
        true_ranges.append(max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        ))
    atr_n = min(10, len(true_ranges))
    atr = statistics.fmean(true_ranges[-atr_n:])
    momentum_n = min(6, len(closes) - 1)
    momentum = closes[-1] - closes[-1 - momentum_n]
    trend_votes = (fast - slow, momentum)
    if all(value > 0 for value in trend_votes):
        trend = "BULLISH"
    elif all(value < 0 for value in trend_votes):
        trend = "BEARISH"
    else:
        trend = "MIXED"
    volume_mean = statistics.fmean(volumes[-min(6, len(volumes)):])
    return {
        "bar_count": len(bars),
        "as_of": _iso(bars[-1]["close_time"]),
        "latest_close": latest,
        "support": support,
        "resistance": resistance,
        "atr": atr,
        "atr_pct": 100.0 * _safe_ratio(atr, latest),
        "fast_slow_spread_pct": 100.0 * _safe_ratio(fast - slow, latest),
        "momentum_pct": 100.0 * _safe_ratio(momentum, latest),
        "range_position": _safe_ratio(latest - support, resistance - support),
        "volume_ratio": _safe_ratio(volumes[-1], volume_mean),
        "trend": trend,
    }


def _four_hour_context(bars):
    return _frame_context(bars, support_lookback=20, slow_lookback=10)


def _long_context(daily):
    frames = {
        "1d": _frame_context(daily, support_lookback=60, slow_lookback=30),
    }
    for name, bars, support_lookback, slow in (
        ("3d", _aggregate_3d(daily), 30, 15),
        ("1w", _aggregate_weekly(daily), 20, 10),
        ("1M", _aggregate_monthly(daily), 12, 6),
    ):
        try:
            frames[name] = _frame_context(
                bars, support_lookback=support_lookback, slow_lookback=slow
            )
        except ValueError:
            continue
    return frames


def build(
    candles, *, symbol, generated_at=None,
    market_scope="RISK_MANAGER_CLOSED_1H", daily_candles=None,
):
    """Build off-hot-path 1h/4h and optional long-regime context."""
    generated_at = generated_at or datetime.now(timezone.utc)
    hourly = _normalize_hourly(candles)
    advanced_values = advanced.vector(hourly, len(hourly) - 1)
    advanced_map = dict(zip(advanced.FEATURE_NAMES, advanced_values))
    four_hour = _four_hour_context(_aggregate_completed_4h(hourly))

    latest_1h = hourly[-1]["close"]
    support_1h = latest_1h * (
        1.0 - float(advanced_map["support_distance72"])
    )
    resistance_1h = latest_1h * (
        1.0 + float(advanced_map["resistance_distance72"])
    )
    atr_1h = latest_1h * float(advanced_map["atr14_pct"])

    trend_score_1h = (
        float(advanced_map["ema8_21_spread"])
        + float(advanced_map["ema20_50_spread"])
    )
    if trend_score_1h > 0:
        trend_1h = "BULLISH"
    elif trend_score_1h < 0:
        trend_1h = "BEARISH"
    else:
        trend_1h = "MIXED"

    long_frames = {}
    daily_as_of = None
    if daily_candles is not None:
        daily = _normalize_daily(daily_candles)
        long_frames = _long_context(daily)
        daily_as_of = _iso(daily[-1]["close_time"])

    timeframes = ["1h", "4h", *long_frames.keys()]
    summary = {
        "trend_1h": trend_1h,
        "trend_4h": four_hour["trend"],
        "alignment": (
            "ALIGNED_BULLISH"
            if trend_1h == four_hour["trend"] == "BULLISH"
            else "ALIGNED_BEARISH"
            if trend_1h == four_hour["trend"] == "BEARISH"
            else "MIXED"
        ),
        "rsi14": 50.0 * (float(advanced_map["rsi14"]) + 1.0),
        "atr14_pct": 100.0 * float(advanced_map["atr14_pct"]),
        "breakout24": float(advanced_map["breakout24"]),
        "support_distance_72_pct": (
            100.0 * float(advanced_map["support_distance72"])
        ),
        "resistance_distance_72_pct": (
            100.0 * float(advanced_map["resistance_distance72"])
        ),
    }
    for name, frame in long_frames.items():
        summary[f"trend_{name}"] = frame["trend"]

    return {
        "version": VERSION,
        "symbol": str(symbol).upper(),
        "generated_at": _iso(generated_at),
        "as_of": _iso(hourly[-1]["close_time"]),
        "daily_as_of": daily_as_of,
        "market_scope": str(market_scope),
        "observation_only": True,
        "real_order_allowed": False,
        "timeframes": timeframes,
        "advanced_1h": {
            name: float(value) for name, value in advanced_map.items()
        },
        "structure_4h": four_hour,
        "long_frames": long_frames,
        "levels": {
            "latest_1h": latest_1h,
            "support_1h_72": support_1h,
            "resistance_1h_72": resistance_1h,
            "atr_1h": atr_1h,
            "support_4h_20": four_hour["support"],
            "resistance_4h_20": four_hour["resistance"],
            "atr_4h": four_hour["atr"],
        },
        "summary": summary,
        "authority": {
            "direction_input": False,
            "ranking_input": False,
            "confidence_input": False,
            "sizing_input": False,
            "stop_input": False,
            "purpose": "SUPPORT_CONTEXT_FOR_AUTHORITATIVE_TECHNICAL_TRADE_PLAN",
        },
    }


def ensure_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS paper_technical_context_latest (
            symbol TEXT PRIMARY KEY,
            context_version TEXT NOT NULL,
            as_of TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            market_scope TEXT NOT NULL,
            context_json TEXT NOT NULL,
            compute_ms REAL NOT NULL,
            observation_only INTEGER NOT NULL DEFAULT 1,
            real_order_allowed INTEGER NOT NULL DEFAULT 0
        )
    """)


def save_latest(con, context, compute_ms):
    ensure_table(con)
    con.execute("""
        INSERT INTO paper_technical_context_latest(
            symbol,context_version,as_of,generated_at,market_scope,
            context_json,compute_ms,observation_only,real_order_allowed
        ) VALUES(?,?,?,?,?,?,?,1,0)
        ON CONFLICT(symbol) DO UPDATE SET
            context_version=excluded.context_version,
            as_of=excluded.as_of,
            generated_at=excluded.generated_at,
            market_scope=excluded.market_scope,
            context_json=excluded.context_json,
            compute_ms=excluded.compute_ms,
            observation_only=1,
            real_order_allowed=0
    """, (
        context["symbol"], context["version"], context["as_of"],
        context["generated_at"], context["market_scope"],
        json.dumps(context, ensure_ascii=False, sort_keys=True),
        float(compute_ms),
    ))


def load_latest(con, symbol, *, now=None):
    """Load fresh local support context without any network access."""
    now = now or datetime.now(timezone.utc)
    exists = con.execute("""
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='paper_technical_context_latest'
    """).fetchone()
    if not exists:
        return None
    row = con.execute("""
        SELECT as_of,generated_at,context_json,observation_only,real_order_allowed
        FROM paper_technical_context_latest WHERE symbol=?
    """, (str(symbol).upper(),)).fetchone()
    if not row or not bool(row[3]) or bool(row[4]):
        return None
    try:
        as_of = _parse_time(row[0])
        generated = _parse_time(row[1])
        if (now - generated).total_seconds() > MAX_GENERATED_AGE_SECONDS:
            return None
        if (now - as_of).total_seconds() > MAX_CANDLE_AGE_SECONDS:
            return None
        if generated > now or as_of > now:
            return None
        payload = json.loads(row[2])
        daily_as_of = payload.get("daily_as_of")
        if daily_as_of is not None:
            daily_age = (now - _parse_time(daily_as_of)).total_seconds()
            if daily_age > MAX_DAILY_CANDLE_AGE_SECONDS:
                payload["long_frames"] = {}
                payload["timeframes"] = [
                    item for item in payload.get("timeframes", [])
                    if item not in {"1d", "3d", "1w", "1M"}
                ]
                for key in ("trend_1d", "trend_3d", "trend_1w", "trend_1M"):
                    (payload.get("summary") or {}).pop(key, None)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        payload.get("version") != VERSION
        or payload.get("symbol") != str(symbol).upper()
        or payload.get("observation_only") is not True
        or payload.get("real_order_allowed") is not False
    ):
        return None
    return payload
