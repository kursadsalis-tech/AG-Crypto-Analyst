"""Causal top-down technical context from completed market timeframes."""
from __future__ import annotations

import calendar
import bisect
import math
import statistics
from datetime import datetime, timedelta, timezone


VERSION = "MULTI_TIMEFRAME_TECHNICAL_V1_FIXED"
TIMEFRAMES = (
    ("monthly", 5, 3),
    ("weekly", 4, 8),
    ("daily", 3, 20),
    ("4h", 2, 42),
    ("1h", 1, 168),
)
PER_TIMEFRAME_FEATURES = (
    "latest_return", "long_return", "mean_spread",
    "range_position", "return_volatility", "volume_ratio",
)
FEATURE_NAMES = tuple(
    f"{timeframe}_{name}"
    for timeframe, _, _ in TIMEFRAMES
    for name in PER_TIMEFRAME_FEATURES
) + (
    "top_down_trend_score", "top_down_agreement",
    "higher_timeframe_score", "lower_timeframe_score",
)
_SERIES_CACHE = {}


def _bucket_bounds(value, timeframe):
    value = value.astimezone(timezone.utc)
    if timeframe == "1h":
        start = value.replace(minute=0, second=0, microsecond=0)
        return start, start + timedelta(hours=1)
    if timeframe == "4h":
        start = value.replace(
            hour=(value.hour // 4) * 4, minute=0, second=0, microsecond=0
        )
        return start, start + timedelta(hours=4)
    if timeframe == "daily":
        start = value.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    if timeframe == "weekly":
        day = value.replace(hour=0, minute=0, second=0, microsecond=0)
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=7)
    if timeframe == "monthly":
        start = value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        days = calendar.monthrange(start.year, start.month)[1]
        return start, start + timedelta(days=days)
    raise ValueError(f"unknown timeframe: {timeframe}")


def _aggregate_completed(candles, index, timeframe):
    cutoff = candles[index]["close_time"]
    grouped = {}
    # Three completed calendar months plus the current partial month fit inside
    # 125 days.  Keep a small safety margin without rescanning the full 400-day
    # history for every forecast timestamp.
    first = max(0, index - 24 * 130)
    for candle in candles[first:index + 1]:
        start, end = _bucket_bounds(candle["open_time"], timeframe)
        if end - timedelta(milliseconds=1) > cutoff:
            continue
        grouped.setdefault((start, end), []).append(candle)
    bars = []
    for (start, end), rows in sorted(grouped.items()):
        expected = int((end - start).total_seconds() // 3600)
        if len(rows) != expected:
            continue
        rows = sorted(rows, key=lambda row: row["open_time"])
        if rows[0]["open_time"] != start:
            continue
        if rows[-1]["close_time"] != end - timedelta(milliseconds=1):
            continue
        values = [
            float(value)
            for row in rows
            for value in (
                row["open"], row["high"], row["low"], row["close"],
                row["quote_volume"],
            )
        ]
        if not all(math.isfinite(value) for value in values):
            continue
        if min(float(row["low"]) for row in rows) <= 0:
            continue
        if min(float(row["quote_volume"]) for row in rows) < 0:
            continue
        bars.append({
            "open": float(rows[0]["open"]),
            "high": max(float(row["high"]) for row in rows),
            "low": min(float(row["low"]) for row in rows),
            "close": float(rows[-1]["close"]),
            "quote_volume": sum(float(row["quote_volume"]) for row in rows),
        })
    return bars


def _all_completed(candles, timeframe):
    grouped = {}
    for candle in candles:
        start, end = _bucket_bounds(candle["open_time"], timeframe)
        grouped.setdefault((start, end), []).append(candle)
    output = []
    for (start, end), rows in sorted(grouped.items()):
        expected = int((end - start).total_seconds() // 3600)
        if len(rows) != expected:
            continue
        rows = sorted(rows, key=lambda row: row["open_time"])
        if (
            rows[0]["open_time"] != start
            or rows[-1]["close_time"] != end - timedelta(milliseconds=1)
        ):
            continue
        values = [
            float(value)
            for row in rows
            for value in (
                row["open"], row["high"], row["low"], row["close"],
                row["quote_volume"],
            )
        ]
        if not all(math.isfinite(value) for value in values):
            continue
        if min(float(row["low"]) for row in rows) <= 0:
            continue
        if min(float(row["quote_volume"]) for row in rows) < 0:
            continue
        output.append((end - timedelta(milliseconds=1), {
            "open": float(rows[0]["open"]),
            "high": max(float(row["high"]) for row in rows),
            "low": min(float(row["low"]) for row in rows),
            "close": float(rows[-1]["close"]),
            "quote_volume": sum(float(row["quote_volume"]) for row in rows),
        }))
    return output


def _completed_by(candles, timeframe, cutoff, limit):
    key = id(candles)
    cached = _SERIES_CACHE.get(key)
    if cached is None or cached[0] is not candles:
        prepared = {}
        for name, _, _ in TIMEFRAMES:
            entries = _all_completed(candles, name)
            prepared[name] = (
                [entry[0] for entry in entries],
                [entry[1] for entry in entries],
            )
        cached = (candles, prepared)
        _SERIES_CACHE[key] = cached
    ends, bars = cached[1][timeframe]
    stop = bisect.bisect_right(ends, cutoff)
    return bars[max(0, stop - limit):stop]


def _safe_ratio(numerator, denominator):
    return float(numerator) / max(abs(float(denominator)), 1e-12)


def _timeframe_vector(bars, lookback):
    if len(bars) < lookback + 1:
        raise ValueError("insufficient completed multi-timeframe history")
    selected = bars[-(lookback + 1):]
    closes = [row["close"] for row in selected]
    volumes = [row["quote_volume"] for row in selected[1:]]
    returns = [
        _safe_ratio(closes[i] - closes[i - 1], closes[i - 1])
        for i in range(1, len(closes))
    ]
    fast_count = max(1, lookback // 3)
    fast = statistics.fmean(closes[-fast_count:])
    slow = statistics.fmean(closes[-lookback:])
    low = min(row["low"] for row in selected[-lookback:])
    high = max(row["high"] for row in selected[-lookback:])
    volume_mean = statistics.fmean(volumes)
    vector = (
        returns[-1],
        _safe_ratio(closes[-1] - closes[0], closes[0]),
        _safe_ratio(fast - slow, closes[-1]),
        2.0 * _safe_ratio(closes[-1] - low, high - low) - 1.0,
        statistics.pstdev(returns),
        _safe_ratio(volumes[-1] - volume_mean, volume_mean),
    )
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("non-finite multi-timeframe features")
    return vector


def vector(candles, index):
    """Build top-down context using only timeframes closed by ``index``."""
    values = []
    directions = []
    cutoff = candles[index]["close_time"]
    for timeframe, weight, lookback in TIMEFRAMES:
        bars = _completed_by(candles, timeframe, cutoff, lookback + 1)
        current = _timeframe_vector(bars, lookback)
        values.extend(current)
        trend = current[1] + current[2]
        directions.append((weight, 1.0 if trend > 0 else (-1.0 if trend < 0 else 0.0)))
    total_weight = sum(weight for weight, _ in directions)
    weighted = sum(weight * direction for weight, direction in directions)
    higher_weight = sum(weight for weight, _ in directions[:3])
    lower_weight = sum(weight for weight, _ in directions[3:])
    values.extend((
        weighted / total_weight,
        abs(weighted) / total_weight,
        sum(weight * direction for weight, direction in directions[:3]) / higher_weight,
        sum(weight * direction for weight, direction in directions[3:]) / lower_weight,
    ))
    if len(values) != len(FEATURE_NAMES):
        raise ValueError("invalid multi-timeframe feature count")
    return list(values)
