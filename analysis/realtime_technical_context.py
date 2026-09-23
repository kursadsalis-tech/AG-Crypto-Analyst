"""Causal shadow technical context for live PAPER candidates.

This module is deliberately observation-only. It reuses the already-fetched
closed 1h risk candles, computes the existing advanced 1h feature pack, derives
a small completed-4h structural context, and persists only the latest snapshot
per symbol in local SQLite. It never selects direction, sizes a trade, or grants
execution authority.
"""
from __future__ import annotations

import calendar
import json
import math
import statistics
from datetime import datetime, timezone

from analysis import advanced_technical_features as advanced


VERSION = "REALTIME_TECHNICAL_CONTEXT_V2_ADAPTIVE_SOURCE"
MIN_HOURLY_CANDLES = advanced.LOOKBACK + 1
MIN_4H_BARS = 21
MAX_GENERATED_AGE_SECONDS = 30 * 60
MAX_CANDLE_AGE_SECONDS = 2 * 60 * 60


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


def _normalize_hourly(candles):
    rows = []
    for raw in candles:
        if not isinstance(raw, (list, tuple)) or len(raw) < 8:
            raise ValueError("invalid closed 1h candle")
        opened_ms = int(raw[0])
        closed_ms = int(raw[6])
        opened = datetime.fromtimestamp(opened_ms / 1000.0, tz=timezone.utc)
        closed = datetime.fromtimestamp(closed_ms / 1000.0, tz=timezone.utc)
        values = [
            float(raw[1]), float(raw[2]), float(raw[3]), float(raw[4]),
            float(raw[7]),
        ]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("non-finite closed 1h candle")
        if min(values[:4]) <= 0 or values[4] < 0:
            raise ValueError("invalid price/volume in closed 1h candle")
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
    if len(rows) < MIN_HOURLY_CANDLES:
        raise ValueError(
            f"insufficient closed 1h candles ({len(rows)}/{MIN_HOURLY_CANDLES})"
        )
    for previous, current in zip(rows, rows[1:]):
        if current["open_time"] <= previous["open_time"]:
            raise ValueError("duplicate/non-increasing closed 1h candles")
    return rows


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


def _four_hour_context(bars):
    if len(bars) < MIN_4H_BARS:
        raise ValueError(f"insufficient completed 4h bars ({len(bars)}/{MIN_4H_BARS})")
    closes = [row["close"] for row in bars]
    highs = [row["high"] for row in bars]
    lows = [row["low"] for row in bars]
    volumes = [row["quote_volume"] for row in bars]

    latest = closes[-1]
    support = min(lows[-20:])
    resistance = max(highs[-20:])
    range_width = resistance - support
    fast = statistics.fmean(closes[-3:])
    slow = statistics.fmean(closes[-10:])

    true_ranges = []
    for index in range(1, len(bars)):
        true_ranges.append(max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        ))
    atr10 = statistics.fmean(true_ranges[-10:])
    volume_mean6 = statistics.fmean(volumes[-6:])
    path6 = sum(
        abs(closes[index] - closes[index - 1])
        for index in range(len(closes) - 6, len(closes))
    )

    result = {
        "bar_count": len(bars),
        "as_of": _iso(bars[-1]["close_time"]),
        "latest_close": latest,
        "support_price_20": support,
        "resistance_price_20": resistance,
        "latest_return_pct": 100.0 * _safe_ratio(closes[-1] - closes[-2], closes[-2]),
        "return_24h_pct": 100.0 * _safe_ratio(closes[-1] - closes[-7], closes[-7]),
        "fast_slow_spread_pct": 100.0 * _safe_ratio(fast - slow, latest),
        "atr10_pct": 100.0 * _safe_ratio(atr10, latest),
        "support_distance_20_pct": 100.0 * _safe_ratio(latest - support, latest),
        "resistance_distance_20_pct": 100.0 * _safe_ratio(resistance - latest, latest),
        "range_position_20": _safe_ratio(latest - support, range_width),
        "volume_ratio_6": _safe_ratio(volumes[-1], volume_mean6),
        "trend_efficiency_6": _safe_ratio(abs(closes[-1] - closes[-7]), path6),
    }
    trend_votes = (
        result["fast_slow_spread_pct"],
        result["return_24h_pct"],
    )
    if all(value > 0 for value in trend_votes):
        result["trend"] = "BULLISH"
    elif all(value < 0 for value in trend_votes):
        result["trend"] = "BEARISH"
    else:
        result["trend"] = "MIXED"
    numeric = [value for value in result.values() if isinstance(value, (int, float))]
    if not all(math.isfinite(float(value)) for value in numeric):
        raise ValueError("non-finite 4h technical context")
    return result



def _normalize_daily(candles):
    rows = []
    for raw in candles or []:
        if not isinstance(raw, (list, tuple)) or len(raw) < 8:
            continue
        opened_ms, closed_ms = int(raw[0]), int(raw[6])
        values = [float(raw[1]), float(raw[2]), float(raw[3]), float(raw[4]), float(raw[7])]
        if not all(math.isfinite(value) for value in values):
            continue
        if min(values[:4]) <= 0 or values[4] < 0:
            continue
        rows.append({
            "open_time": datetime.fromtimestamp(opened_ms / 1000.0, tz=timezone.utc),
            "close_time": datetime.fromtimestamp(closed_ms / 1000.0, tz=timezone.utc),
            "open": values[0], "high": values[1], "low": values[2],
            "close": values[3], "quote_volume": values[4],
        })
    rows.sort(key=lambda row: row["open_time"])
    return rows


def _aggregate_calendar(rows, frame):
    groups = {}
    for row in rows:
        opened = row["open_time"]
        if frame == "3d":
            epoch_day = int(opened.timestamp() // 86400)
            key = ("3d", epoch_day // 3)
            expected = 3
        elif frame == "1w":
            start = (opened - __import__("datetime").timedelta(days=opened.weekday())).date()
            key = ("1w", start.isoformat())
            expected = 7
        elif frame == "1M":
            key = ("1M", opened.year, opened.month)
            expected = calendar.monthrange(opened.year, opened.month)[1]
        else:
            raise ValueError("unsupported regime frame")
        groups.setdefault((key, expected), []).append(row)
    output = []
    for (_, expected), members in sorted(groups.items(), key=lambda item: item[1][0]["open_time"]):
        members = sorted(members, key=lambda row: row["open_time"])
        if len(members) != expected:
            continue
        if any(
            (members[index]["open_time"] - members[index - 1]["open_time"]).days != 1
            for index in range(1, len(members))
        ):
            continue
        output.append({
            "open_time": members[0]["open_time"],
            "close_time": members[-1]["close_time"],
            "open": members[0]["open"],
            "high": max(row["high"] for row in members),
            "low": min(row["low"] for row in members),
            "close": members[-1]["close"],
            "quote_volume": sum(row["quote_volume"] for row in members),
        })
    return output


def _regime_frame(rows, minimum):
    if len(rows) < minimum:
        raise ValueError("insufficient completed regime bars")
    closes = [row["close"] for row in rows]
    highs = [row["high"] for row in rows]
    lows = [row["low"] for row in rows]
    latest = closes[-1]
    fast = statistics.fmean(closes[-3:])
    slow_window = min(10, len(closes))
    slow = statistics.fmean(closes[-slow_window:])
    support = min(lows[-min(20, len(lows)):])
    resistance = max(highs[-min(20, len(highs)):])
    spread = _safe_ratio(fast - slow, latest)
    if spread > 0:
        trend = "BULLISH"
    elif spread < 0:
        trend = "BEARISH"
    else:
        trend = "MIXED"
    return {
        "bar_count": len(rows),
        "as_of": _iso(rows[-1]["close_time"]),
        "trend": trend,
        "fast_slow_spread_pct": 100.0 * spread,
        "support_price": support,
        "resistance_price": resistance,
        "latest_close": latest,
    }


def _regime_context(daily_candles):
    daily = _normalize_daily(daily_candles)
    if len(daily) < 20:
        return {}
    frames = {"1d": _regime_frame(daily, 20)}
    for name, minimum in (("3d", 8), ("1w", 6), ("1M", 4)):
        try:
            frames[name] = _regime_frame(_aggregate_calendar(daily, name), minimum)
        except ValueError:
            continue
    return frames


def build(candles, *, symbol, generated_at=None, market_scope="RISK_MANAGER_CLOSED_1H", daily_candles=None):
    """Build observation-only 1h + completed-4h context from closed risk candles."""
    generated_at = generated_at or datetime.now(timezone.utc)
    hourly = _normalize_hourly(candles)
    advanced_values = advanced.vector(hourly, len(hourly) - 1)
    advanced_map = dict(zip(advanced.FEATURE_NAMES, advanced_values))
    four_hour = _four_hour_context(_aggregate_completed_4h(hourly))

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

    if trend_1h == four_hour["trend"] == "BULLISH":
        alignment = "ALIGNED_BULLISH"
    elif trend_1h == four_hour["trend"] == "BEARISH":
        alignment = "ALIGNED_BEARISH"
    else:
        alignment = "MIXED"

    regime = _regime_context(daily_candles)
    support_1h = min(row["low"] for row in hourly[-72:])
    resistance_1h = max(row["high"] for row in hourly[-72:])
    return {
        "version": VERSION,
        "symbol": str(symbol).upper(),
        "generated_at": _iso(generated_at),
        "as_of": _iso(hourly[-1]["close_time"]),
        "market_scope": str(market_scope),
        "observation_only": True,
        "real_order_allowed": False,
        "timeframes": ["1h", "4h", *regime.keys()],
        "levels": {
            "support_1h_72": support_1h,
            "resistance_1h_72": resistance_1h,
            "support_4h_20": four_hour["support_price_20"],
            "resistance_4h_20": four_hour["resistance_price_20"],
        },
        "regime": regime,
        "advanced_1h": {
            name: float(value) for name, value in advanced_map.items()
        },
        "structure_4h": four_hour,
        "summary": {
            "trend_1h": trend_1h,
            "trend_4h": four_hour["trend"],
            "alignment": alignment,
            "rsi14": 50.0 * (float(advanced_map["rsi14"]) + 1.0),
            "atr14_pct": 100.0 * float(advanced_map["atr14_pct"]),
            "breakout24": float(advanced_map["breakout24"]),
            "support_distance_72_pct": (
                100.0 * float(advanced_map["support_distance72"])
            ),
            "resistance_distance_72_pct": (
                100.0 * float(advanced_map["resistance_distance72"])
            ),
        },
        "authority": {
            "direction_input": False,
            "ranking_input": False,
            "confidence_input": False,
            "sizing_input": False,
            "stop_input": False,
            "purpose": "FORWARD_OBSERVATION_AND_LATER_CHALLENGER_EVIDENCE",
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
    """Load fresh local shadow context; missing/stale context never blocks trading."""
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
