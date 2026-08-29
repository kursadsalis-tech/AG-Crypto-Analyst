"""Causal advanced technical feature pack for closed hourly OHLCV candles."""
from __future__ import annotations

import math
import statistics


VERSION = "ADVANCED_TECHNICAL_FEATURES_V1_FIXED"
LOOKBACK = 168
FEATURE_NAMES = (
    "rsi14", "ema8_21_spread", "ema20_50_spread", "macd_hist_atr",
    "bollinger_z20", "bollinger_width20", "atr14_pct", "adx14",
    "stochastic14", "williams_r14", "cci20", "mfi14",
    "vwap24_deviation", "obv_slope24", "volume_z24",
    "support_distance72", "resistance_distance72", "fib_position72",
    "trend_efficiency24", "candle_body_atr", "upper_wick_atr",
    "lower_wick_atr", "breakout24", "range_position168",
)


def _ema(values, period):
    seed = sum(values[:period]) / period
    multiplier = 2.0 / (period + 1.0)
    output = [seed]
    for value in values[period:]:
        seed += (value - seed) * multiplier
        output.append(seed)
    return output


def _safe_ratio(numerator, denominator):
    return float(numerator) / max(abs(float(denominator)), 1e-12)


def vector(candles, index):
    """Return fixed features using candles at or before ``index`` only."""
    if index < LOOKBACK:
        raise ValueError("insufficient advanced technical history")
    window = candles[index - LOOKBACK:index + 1]
    closes = [float(row["close"]) for row in window]
    highs = [float(row["high"]) for row in window]
    lows = [float(row["low"]) for row in window]
    opens = [float(row["open"]) for row in window]
    volumes = [float(row["quote_volume"]) for row in window]
    price_values = opens + highs + lows + closes
    if (
        not all(math.isfinite(value) for value in price_values + volumes)
        or min(price_values) <= 0
        or min(volumes) < 0
    ):
        raise ValueError("invalid advanced technical candle")
    volume24 = sum(volumes[-24:])
    if volume24 <= 0:
        raise ValueError("insufficient advanced technical volume")

    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(value, 0.0) for value in changes[-14:]]
    losses = [max(-value, 0.0) for value in changes[-14:]]
    rs = _safe_ratio(sum(gains), sum(losses))
    rsi = 100.0 if not sum(losses) else 100.0 - 100.0 / (1.0 + rs)

    ema8, ema21 = _ema(closes, 8)[-1], _ema(closes, 21)[-1]
    ema20, ema50 = _ema(closes, 20)[-1], _ema(closes, 50)[-1]
    ema12s, ema26s = _ema(closes, 12), _ema(closes, 26)
    common = min(len(ema12s), len(ema26s))
    macd = [ema12s[-common + i] - ema26s[-common + i] for i in range(common)]
    macd_hist = macd[-1] - _ema(macd, 9)[-1]

    true_ranges = []
    plus_dm, minus_dm = [], []
    for i in range(1, len(window)):
        true_ranges.append(max(
            highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
        up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    atr = statistics.fmean(true_ranges[-14:])
    plus_di = 100.0 * _safe_ratio(sum(plus_dm[-14:]), sum(true_ranges[-14:]))
    minus_di = 100.0 * _safe_ratio(sum(minus_dm[-14:]), sum(true_ranges[-14:]))
    adx_proxy = 100.0 * _safe_ratio(abs(plus_di - minus_di), plus_di + minus_di)

    mean20 = statistics.fmean(closes[-20:])
    std20 = statistics.pstdev(closes[-20:])
    low14, high14 = min(lows[-14:]), max(highs[-14:])
    typical = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(len(window))]
    mean_typical = statistics.fmean(typical[-20:])
    mean_deviation = statistics.fmean(abs(value - mean_typical) for value in typical[-20:])
    cci = _safe_ratio(typical[-1] - mean_typical, 0.015 * mean_deviation)

    positive_flow = negative_flow = 0.0
    for i in range(len(window) - 13, len(window)):
        flow = typical[i] * volumes[i]
        if typical[i] >= typical[i - 1]:
            positive_flow += flow
        else:
            negative_flow += flow
    money_ratio = _safe_ratio(positive_flow, negative_flow)
    mfi = 100.0 if not negative_flow else 100.0 - 100.0 / (1.0 + money_ratio)
    vwap24 = sum(
        typical[i] * volumes[i]
        for i in range(len(window) - 24, len(window))
    ) / volume24

    obv = [0.0]
    for i in range(1, len(window)):
        direction = 1.0 if closes[i] > closes[i - 1] else (-1.0 if closes[i] < closes[i - 1] else 0.0)
        obv.append(obv[-1] + direction * volumes[i])
    volume_mean, volume_std = statistics.fmean(volumes[-24:]), statistics.pstdev(volumes[-24:])
    support72, resistance72 = min(lows[-72:]), max(highs[-72:])
    range72 = resistance72 - support72
    path = sum(abs(value) for value in changes[-24:])
    latest_range = max(highs[-1] - lows[-1], 1e-12)
    prior_high24, prior_low24 = max(highs[-25:-1]), min(lows[-25:-1])
    breakout = 1.0 if closes[-1] > prior_high24 else (-1.0 if closes[-1] < prior_low24 else 0.0)
    low168, high168 = min(lows), max(highs)

    values = (
        (rsi - 50.0) / 50.0,
        _safe_ratio(ema8 - ema21, closes[-1]),
        _safe_ratio(ema20 - ema50, closes[-1]),
        _safe_ratio(macd_hist, atr),
        _safe_ratio(closes[-1] - mean20, std20),
        _safe_ratio(4.0 * std20, mean20),
        _safe_ratio(atr, closes[-1]), adx_proxy / 100.0,
        _safe_ratio(closes[-1] - low14, high14 - low14),
        -_safe_ratio(high14 - closes[-1], high14 - low14),
        cci / 200.0, (mfi - 50.0) / 50.0,
        _safe_ratio(closes[-1] - vwap24, vwap24),
        _safe_ratio(obv[-1] - obv[-25], sum(volumes[-24:])),
        _safe_ratio(volumes[-1] - volume_mean, volume_std),
        _safe_ratio(closes[-1] - support72, closes[-1]),
        _safe_ratio(resistance72 - closes[-1], closes[-1]),
        _safe_ratio(closes[-1] - support72, range72),
        _safe_ratio(abs(closes[-1] - closes[-25]), path),
        _safe_ratio(closes[-1] - opens[-1], atr),
        _safe_ratio(highs[-1] - max(opens[-1], closes[-1]), atr),
        _safe_ratio(min(opens[-1], closes[-1]) - lows[-1], atr),
        breakout, _safe_ratio(closes[-1] - low168, high168 - low168),
    )
    if len(values) != len(FEATURE_NAMES) or not all(math.isfinite(value) for value in values):
        raise ValueError("non-finite advanced technical features")
    return list(values)
