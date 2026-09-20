"""Leakage-safe probabilistic Forecast Core research lab.

The lab evaluates one pre-registered model family against fixed baselines for
1h, 4h and 24h horizons.  It never changes the live Decision Engine, never
publishes product alerts and never treats the already-observed historical EXAM
period as production evidence.

Only exact, closed Binance one-hour OHLCV candles are used in V1.  Historical
news/social/on-chain/macro/whale features are excluded until their point-in-
time availability can be reconstructed without hindsight.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import os
import random
import sqlite3
from database.runtime_store import connect as runtime_connect
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from decision_engine.forecast_contract import (
    CONTRACT_VERSION,
    ENTRY_RULE,
    HORIZONS,
    MODEL_FAMILY,
    PROBABILITY_EVENT,
    REFERENCE_PRICE_BASIS,
    build_forecast_record,
)


DB_NAME = "crypto_data.db"
LAB_VERSION = "FORECAST_CORE_LAB_V1_2_VALIDATOR_DIAGNOSTICS"
MODEL_VERSION = "POOLED_RIDGE_DISTRIBUTION_V1_ALPHA_0_05_FIXED"
SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "ARKMUSDT", "FIDAUSDT", "WLDUSDT",
    "TIAUSDT", "ETHFIUSDT", "BIOUSDT", "LQTYUSDT", "ILVUSDT",
    "SLPUSDT", "UMAUSDT", "IOUSDT",
)
RISK_CANDLE_COUNT = 169
LOOKBACK_DAYS = int(os.getenv("AGCA_FORECAST_LAB_DAYS", "400"))
FETCH_HISTORY = os.getenv("AGCA_FORECAST_LAB_FETCH", "1").strip() != "0"
ROUND_TRIP_COST_BPS = 10.0
MINIMUM_MOVE_BPS = 5.0
ACTION_PROBABILITY = 0.70
RIDGE_ALPHA = 0.05
INTERVAL_PROBABILITY = 0.80
WARMUP_HOURS = 193
FEATURE_NAMES = (
    "ret_1h",
    "ret_4h",
    "ret_24h",
    "ret_168h",
    "vol_24h",
    "vol_168h",
    "range_24h",
    "log_quote_volume_ratio",
    "btc_ret_1h",
    "btc_ret_4h",
    "btc_ret_24h",
    "btc_vol_24h",
) + tuple(f"symbol_{symbol}" for symbol in SYMBOLS[1:])
DETAIL_PATH = Path("reports/forecast_core_lab.csv")
SELECTION_PATH = Path("reports/forecast_core_selection.csv")
CANDIDATE_PATH = Path("reports/forecast_core_candidate.json")


def utcnow():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def parse_time(value):
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def pct_change(new_value, old_value):
    if float(old_value) <= 0:
        raise ValueError("percentage change requires a positive base")
    return (float(new_value) / float(old_value) - 1.0) * 100.0


def requested_range(now=None):
    now = (now or utcnow()).astimezone(timezone.utc)
    # Binance 1h kline close timestamps are HH:59:59.999.  The REST endpoint
    # may not expose the just-closed candle at the exact HH:00 scheduler tick,
    # so give ingestion a small bounded publication grace period instead of
    # demanding a candle that may not be queryable yet.  Forecast freshness is
    # still enforced independently by validate_forecast_freshness().
    publication_grace = timedelta(
        seconds=max(0.0, float(os.getenv("AGCA_CANDLE_PUBLICATION_GRACE_SECONDS", "5")))
    )
    effective_now = now - publication_grace
    end = effective_now.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=LOOKBACK_DAYS)
    fetch_start = start - timedelta(hours=WARMUP_HOURS)
    return start, fetch_start, end


def split_boundaries(start, end):
    span = end - start
    return {
        "start": start,
        "fit_end": start + span * 0.45,
        "learn_end": start + span * 0.60,
        "validation_end": start + span * 0.80,
        "end": end,
    }


def strict_segment(as_of, due_time, boundaries):
    """Assign a row only if its complete label stays inside one segment."""
    segments = (
        ("FIT", boundaries["start"], boundaries["fit_end"]),
        ("CALIBRATION", boundaries["fit_end"], boundaries["learn_end"]),
        ("VALIDATION", boundaries["learn_end"], boundaries["validation_end"]),
        ("EXAM", boundaries["validation_end"], boundaries["end"]),
    )
    for name, segment_start, segment_end in segments:
        if segment_start <= as_of < segment_end and due_time < segment_end:
            return name
    return None


def require_hourly_continuity(candles, first, last):
    if first < 0 or last >= len(candles):
        raise ValueError("candle window outside stored history")
    for position in range(first + 1, last + 1):
        gap = (
            candles[position]["open_time"]
            - candles[position - 1]["open_time"]
        ).total_seconds()
        if not 3599.0 <= gap <= 3601.0:
            raise ValueError("non-contiguous hourly candles")


def stored_coverage(con, symbol, start, end):
    row = con.execute("""
        SELECT MIN(open_time), MAX(close_time), COUNT(*),
               SUM(CASE WHEN quote_volume IS NULL THEN 1 ELSE 0 END)
        FROM market_klines
        WHERE symbol=? AND interval='1h'
          AND close_time>=? AND close_time<=?
    """, (symbol, iso(start), iso(end))).fetchone()
    if not row or not row[0] or not row[1]:
        return None
    return {
        "start": parse_time(row[0]),
        "end": parse_time(row[1]),
        "rows": int(row[2]),
        "missing_quote_volume": int(row[3] or 0),
    }


def coverage_ready(coverage, fetch_start, end):
    """Require the complete last closed candle while tolerating listing starts."""
    expected_last_close = end - timedelta(milliseconds=1)
    return bool(
        coverage
        and coverage["start"] <= fetch_start + timedelta(hours=2)
        and coverage["end"] >= expected_last_close
        and coverage["missing_quote_volume"] == 0
    )


def ensure_historical_coverage(con, fetch_start, end):
    from data import historical_market_data

    historical_market_data.DB_NAME = DB_NAME
    historical_market_data.create_market_klines_table()
    result = {}
    for symbol in SYMBOLS:
        coverage = stored_coverage(con, symbol, fetch_start, end)
        ready = coverage_ready(coverage, fetch_start, end)
        if ready:
            result[symbol] = {"status": "READY", "fetched": 0}
            continue
        if not FETCH_HISTORY:
            result[symbol] = {"status": "FETCH_DISABLED", "fetched": 0}
            continue
        try:
            ranges = []
            if coverage is None:
                ranges.append((fetch_start, end - timedelta(milliseconds=1)))
            else:
                if coverage["start"] > fetch_start + timedelta(hours=2):
                    ranges.append((fetch_start, coverage["start"] - timedelta(milliseconds=1)))
                expected_last_close = end - timedelta(milliseconds=1)
                if coverage["end"] < expected_last_close:
                    ranges.append((
                        coverage["end"] + timedelta(milliseconds=1),
                        expected_last_close,
                    ))
                if coverage["missing_quote_volume"]:
                    missing = con.execute("""
                        SELECT MIN(open_time), MAX(close_time)
                        FROM market_klines
                        WHERE symbol=? AND interval='1h'
                          AND close_time>=? AND close_time<=?
                          AND quote_volume IS NULL
                    """, (symbol, iso(fetch_start), iso(end))).fetchone()
                    if missing and missing[0] and missing[1]:
                        ranges.append((parse_time(missing[0]), parse_time(missing[1])))
            fetched = 0
            for range_start, range_end in ranges:
                if range_start >= range_end:
                    continue
                fetched += historical_market_data.fetch_symbol_history(
                    symbol,
                    int(range_start.timestamp() * 1000),
                    int(range_end.timestamp() * 1000),
                )
            con.commit()
            refreshed = stored_coverage(con, symbol, fetch_start, end)
            refreshed_ready = coverage_ready(refreshed, fetch_start, end)
            result[symbol] = {
                "status": "FETCHED_READY" if refreshed_ready else "INCOMPLETE",
                "fetched": fetched,
            }
        except Exception as exc:
            result[symbol] = {
                "status": "ERROR",
                "fetched": 0,
                "error": str(exc),
            }
    return result


def load_series(con, start, end):
    from data.truth_engine import validate_stored_candles

    output = {}
    columns = {row[1] for row in con.execute("PRAGMA table_info(market_klines)")}
    taker_expression = (
        "taker_buy_quote_volume" if "taker_buy_quote_volume" in columns else "NULL"
    )
    for symbol in SYMBOLS:
        rows = con.execute(f"""
            SELECT open_time, close_time, open, high, low, close,
                   volume, quote_volume, {taker_expression}
            FROM market_klines
            WHERE symbol=? AND interval='1h'
              AND close_time>=? AND close_time<=?
            ORDER BY open_time
        """, (symbol, iso(start), iso(end))).fetchall()
        candles = []
        for row in rows:
            values = [float(value) for value in row[2:7]]
            quote_volume = None if row[7] is None else float(row[7])
            taker_buy_quote_volume = None if row[8] is None else float(row[8])
            candles.append({
                "open_time": parse_time(row[0]),
                "close_time": parse_time(row[1]),
                "open": values[0],
                "high": values[1],
                "low": values[2],
                "close": values[3],
                "volume": values[4],
                "quote_volume": quote_volume,
                "taker_buy_quote_volume": taker_buy_quote_volume,
            })
        validate_stored_candles(
            candles,
            source=f"MARKET_KLINES:{symbol}",
            require_contiguous=True,
            maximum_gap_ratio=0.025,
        )
        output[symbol] = {
            "candles": candles,
            "by_close": {row["close_time"]: index for index, row in enumerate(candles)},
        }
    return output


def returns(closes, first, last):
    return [pct_change(closes[index], closes[index - 1])
            for index in range(first + 1, last + 1)]


def feature_values(symbol, candles, index, btc_candles, btc_index):
    first = index - 168
    btc_first = btc_index - 24
    require_hourly_continuity(candles, first, index)
    require_hourly_continuity(btc_candles, btc_first, btc_index)
    window = candles[first:index + 1]
    closes = [row["close"] for row in window]
    hourly_returns = returns(closes, 0, len(closes) - 1)
    recent = window[-24:]
    qv_recent = statistics.fmean(row["quote_volume"] for row in recent)
    qv_long = statistics.fmean(row["quote_volume"] for row in window[:-24])
    if qv_recent <= 0 or qv_long <= 0:
        raise ValueError("exact quote volume unavailable")

    btc_window = btc_candles[btc_first:btc_index + 1]
    btc_closes = [row["close"] for row in btc_window]
    btc_returns = returns(btc_closes, 0, len(btc_closes) - 1)
    values = [
        pct_change(closes[-1], closes[-2]),
        pct_change(closes[-1], closes[-5]),
        pct_change(closes[-1], closes[-25]),
        pct_change(closes[-1], closes[-169]),
        statistics.pstdev(hourly_returns[-24:]),
        statistics.pstdev(hourly_returns[-168:]),
        pct_change(max(row["high"] for row in recent),
                   min(row["low"] for row in recent)),
        math.log(qv_recent / qv_long),
        pct_change(btc_closes[-1], btc_closes[-2]),
        pct_change(btc_closes[-1], btc_closes[-5]),
        pct_change(btc_closes[-1], btc_closes[-25]),
        statistics.pstdev(btc_returns[-24:]),
    ]
    values.extend(1.0 if symbol == item else 0.0 for item in SYMBOLS[1:])
    return values


def historical_risk_metrics(symbol, candles, index):
    first = index - RISK_CANDLE_COUNT + 1
    require_hourly_continuity(candles, first, index)
    window = candles[first:index + 1]
    closes = [row["close"] for row in window]
    recent = window[-24:]
    hourly_returns = returns(closes, len(closes) - 25, len(closes) - 1)
    return {
        "symbol": symbol,
        "as_of": iso(candles[index]["close_time"]),
        "price": closes[-1],
        "return_24h_pct": pct_change(closes[-1], closes[-25]),
        "range_24h_pct": pct_change(
            max(row["high"] for row in recent),
            min(row["low"] for row in recent),
        ),
        "hourly_volatility_pct": statistics.pstdev(hourly_returns),
        "drawdown_7d_pct": pct_change(closes[-1], max(closes[-168:])),
        "quote_volume_24h": sum(row["quote_volume"] for row in recent),
        "quote_volume_method": "BINANCE_QUOTE_VOLUME",
    }


def risk_context(series, decision_time):
    from risk_manager import risk_manager

    assets = []
    indexes = {}
    errors = {}
    for symbol in SYMBOLS:
        item = series[symbol]
        index = item["by_close"].get(decision_time)
        indexes[symbol] = index
        if index is None:
            errors[symbol] = "NO_EXACT_CLOSED_CANDLE"
            continue
        try:
            metrics = historical_risk_metrics(symbol, item["candles"], index)
            assets.append(risk_manager.classify_asset_risk(metrics))
        except Exception as exc:
            errors[symbol] = str(exc)
    policy = risk_manager.build_global_policy(
        assets,
        errors,
        created_at=iso(decision_time),
    )
    return policy, {row["symbol"]: row for row in assets}, indexes, errors


def build_rows(series, start, end):
    btc = series["BTCUSDT"]["candles"]
    rows = []
    gaps = 0
    risk_blocks = 0
    max_horizon = max(HORIZONS)
    for btc_index in range(WARMUP_HOURS, len(btc) - max_horizon):
        decision_time = btc[btc_index]["close_time"]
        if decision_time < start or decision_time >= end:
            continue
        policy, assets, indexes, errors = risk_context(series, decision_time)
        gaps += len(errors)
        for symbol in SYMBOLS:
            asset = assets.get(symbol)
            index = indexes.get(symbol)
            if asset is None or index is None:
                continue
            risk_allowed = bool(
                policy["new_positions_allowed"]
                and asset["new_position_allowed"]
                and float(asset["max_position_pct"]) > 0
            )
            if not risk_allowed:
                risk_blocks += 1
            candles = series[symbol]["candles"]
            try:
                features = feature_values(symbol, candles, index, btc, btc_index)
                from analysis import advanced_technical_features
                technical_features = advanced_technical_features.vector(candles, index)
                require_hourly_continuity(candles, index, index + max_horizon)
            except ValueError:
                gaps += 1
                continue
            current = candles[index]["close"]
            entry = candles[index + 1]["open"]
            vol24 = max(float(features[4]), 0.02)
            btc_ret24 = float(features[10])
            btc_threshold = max(0.50, float(features[11]) * math.sqrt(24.0))
            regime = "UP" if btc_ret24 > btc_threshold else (
                "DOWN" if btc_ret24 < -btc_threshold else "SIDEWAYS"
            )
            for horizon in HORIZONS:
                due = candles[index + horizon]["close_time"]
                exit_price = candles[index + horizon]["close"]
                scale = max(vol24 * math.sqrt(float(horizon)), 0.10)
                price_return = pct_change(exit_price, current)
                trade_return = pct_change(exit_price, entry)
                rows.append({
                    "symbol": symbol,
                    "as_of": decision_time,
                    "feature_cutoff": decision_time,
                    "due_time": due,
                    "horizon": horizon,
                    "current_price": current,
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "price_return_pct": price_return,
                    "trade_return_pct": trade_return,
                    "target_normalized": price_return / scale,
                    "scale_pct": scale,
                    "features": features,
                    "technical_features": technical_features,
                    "risk_state": policy["market_state"],
                    "risk_cap_pct": float(asset["max_position_pct"]),
                    "risk_eligible": risk_allowed,
                    "regime": regime,
                })
    return {"rows": rows, "source_gaps": gaps, "risk_blocks": risk_blocks}


def assign_segments(rows, boundaries):
    output = []
    for row in rows:
        row = dict(row)
        row["segment"] = strict_segment(
            row["as_of"], row["due_time"], boundaries
        )
        if row["segment"]:
            output.append(row)
    return output


def non_overlapping(rows):
    """Deterministic per-symbol cooldown equal to the forecast horizon."""
    selected = []
    next_allowed = {}
    for row in sorted(rows, key=lambda item: (item["as_of"], item["symbol"])):
        key = (row["segment"], row["symbol"], row["horizon"])
        if row["as_of"] < next_allowed.get(key, datetime.min.replace(tzinfo=timezone.utc)):
            continue
        selected.append(row)
        next_allowed[key] = row["due_time"]
    return selected


def fit_standardizer(rows):
    if not rows:
        raise ValueError("cannot fit standardizer without rows")
    columns = list(zip(*(row["features"] for row in rows)))
    means = [statistics.fmean(column) for column in columns]
    scales = [statistics.pstdev(column) for column in columns]
    scales = [value if value > 1e-12 else 1.0 for value in scales]
    return {"means": means, "scales": scales}


def transform(features, standardizer):
    return [
        (float(value) - mean) / scale
        for value, mean, scale in zip(
            features, standardizer["means"], standardizer["scales"]
        )
    ]


def solve_linear_system(matrix, vector):
    size = len(vector)
    augmented = [list(map(float, matrix[index])) + [float(vector[index])]
                 for index in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("singular ridge system")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor:
                augmented[row] = [
                    current - factor * reference
                    for current, reference in zip(augmented[row], augmented[column])
                ]
    return [augmented[index][-1] for index in range(size)]


def fit_ridge(rows, standardizer, alpha=RIDGE_ALPHA):
    dimension = len(FEATURE_NAMES) + 1
    gram = [[0.0] * dimension for _ in range(dimension)]
    rhs = [0.0] * dimension
    for row in rows:
        vector = [1.0] + transform(row["features"], standardizer)
        target = float(row["target_normalized"])
        for left in range(dimension):
            rhs[left] += vector[left] * target
            for right in range(left, dimension):
                gram[left][right] += vector[left] * vector[right]
    for left in range(dimension):
        for right in range(left):
            gram[left][right] = gram[right][left]
        if left:
            # Objective: mean squared loss + alpha * ||beta||^2.
            # Scaling by n keeps the fixed alpha stable across sample sizes.
            gram[left][left] += float(alpha) * len(rows)
    return solve_linear_system(gram, rhs)


def predict_normalized(coefficients, features, standardizer):
    vector = [1.0] + transform(features, standardizer)
    return sum(weight * value for weight, value in zip(coefficients, vector))


def finite_sample_quantile(values, probability):
    if not values:
        raise ValueError("quantile requires observations")
    ordered = sorted(float(value) for value in values)
    rank = math.ceil((len(ordered) + 1) * float(probability)) - 1
    return ordered[min(max(rank, 0), len(ordered) - 1)]


def probability_gt(sorted_values, threshold):
    count = len(sorted_values)
    if not count:
        raise ValueError("probability requires observations")
    greater = count - bisect.bisect_right(sorted_values, threshold)
    return (greater + 0.5) / (count + 1.0)


def probability_lt(sorted_values, threshold):
    count = len(sorted_values)
    if not count:
        raise ValueError("probability requires observations")
    lower = bisect.bisect_left(sorted_values, threshold)
    return (lower + 0.5) / (count + 1.0)


def fit_model(fit_rows, calibration_rows, horizon):
    fit_selected = [row for row in fit_rows if row["horizon"] == horizon]
    calibration_selected = [
        row for row in calibration_rows if row["horizon"] == horizon
    ]
    if not fit_selected or not calibration_selected:
        raise ValueError(f"horizon {horizon}: insufficient fit/calibration rows")
    standardizer = fit_standardizer(fit_selected)
    coefficients = fit_ridge(fit_selected, standardizer)
    price_residuals = []
    trade_residuals = []
    classes = Counter()
    threshold_pct = (ROUND_TRIP_COST_BPS + MINIMUM_MOVE_BPS) / 100.0
    for row in calibration_selected:
        prediction = predict_normalized(
            coefficients, row["features"], standardizer
        )
        price_residuals.append(row["target_normalized"] - prediction)
        trade_residuals.append(
            (row["trade_return_pct"] - prediction * row["scale_pct"])
            / row["scale_pct"]
        )
        if row["trade_return_pct"] > threshold_pct:
            classes["UP"] += 1
        elif row["trade_return_pct"] < -threshold_pct:
            classes["DOWN"] += 1
        else:
            classes["NEUTRAL"] += 1
    total = sum(classes.values())
    base_probabilities = {
        name: (classes[name] + 1.0) / (total + 3.0)
        for name in ("UP", "DOWN", "NEUTRAL")
    }
    cost_pct = ROUND_TRIP_COST_BPS / 100.0
    calibration_long_utility = statistics.fmean(
        row["trade_return_pct"] - cost_pct for row in calibration_selected
    )
    calibration_sell_utility = statistics.fmean(
        -row["trade_return_pct"] - cost_pct for row in calibration_selected
    )
    constant_baseline_direction = (
        "AL" if calibration_long_utility >= calibration_sell_utility else "SAT"
    )
    return {
        "horizon": horizon,
        "standardizer": standardizer,
        "coefficients": coefficients,
        "price_residuals": sorted(price_residuals),
        "trade_residuals": sorted(trade_residuals),
        "price_residual_mean": statistics.fmean(price_residuals),
        "trade_residual_mean": statistics.fmean(trade_residuals),
        "price_q10": finite_sample_quantile(price_residuals, 0.10),
        "price_q50": finite_sample_quantile(price_residuals, 0.50),
        "price_q90": finite_sample_quantile(price_residuals, 0.90),
        "naive_abs_q80": finite_sample_quantile(
            [abs(row["price_return_pct"] / row["scale_pct"])
             for row in calibration_selected],
            0.80,
        ),
        "base_probabilities": base_probabilities,
        # Frozen on CALIBRATION; VALIDATION outcomes never choose the baseline.
        "constant_baseline_direction": constant_baseline_direction,
        "fit_rows": len(fit_selected),
        "calibration_rows": len(calibration_selected),
    }


def forecast_row(model, row):
    raw = predict_normalized(
        model["coefficients"], row["features"], model["standardizer"]
    )
    scale = row["scale_pct"]
    median_return = (raw + model["price_q50"]) * scale
    low_return = (raw + model["price_q10"]) * scale
    high_return = (raw + model["price_q90"]) * scale
    meaningful = (ROUND_TRIP_COST_BPS + MINIMUM_MOVE_BPS) / 100.0
    trade_residuals = model["trade_residuals"]
    p_up = probability_gt(trade_residuals, (meaningful / scale) - raw)
    p_down = probability_lt(trade_residuals, (-meaningful / scale) - raw)
    p_neutral = max(0.0, 1.0 - p_up - p_down)
    total = p_up + p_down + p_neutral
    p_up, p_down, p_neutral = (p_up / total, p_down / total, p_neutral / total)
    expected_trade = (raw + model["trade_residual_mean"]) * scale
    expected_long_net = expected_trade - ROUND_TRIP_COST_BPS / 100.0
    expected_short_net = -expected_trade - ROUND_TRIP_COST_BPS / 100.0
    if p_up >= ACTION_PROBABILITY and expected_long_net > 0:
        action = "AL"
        expected_net = expected_long_net
    elif p_down >= ACTION_PROBABILITY and expected_short_net > 0:
        action = "SAT"
        expected_net = expected_short_net
    else:
        action = "BEKLE"
        expected_net = 0.0
    risk_adjusted_action = action if row["risk_eligible"] else "BEKLE"
    current = row["current_price"]
    predicted = dict(row)
    target_price = current * (1.0 + median_return / 100.0)
    low_price = current * (1.0 + low_return / 100.0)
    high_price = current * (1.0 + high_return / 100.0)
    if not (
        math.isfinite(target_price)
        and math.isfinite(low_price)
        and math.isfinite(high_price)
        and 0 < low_price <= target_price <= high_price
    ):
        raise ValueError("model produced an invalid price distribution")
    predicted.update({
        "predicted_return_pct": median_return,
        "target_price": target_price,
        "price_p10": low_price,
        "price_p90": high_price,
        "p_up": p_up,
        "p_down": p_down,
        "p_neutral": p_neutral,
        "expected_trade_return_pct": expected_trade,
        "expected_net_return_pct": expected_net,
        "action": action,
        "risk_adjusted_action": risk_adjusted_action,
        "actual_class": (
            "UP" if row["trade_return_pct"] > meaningful else (
                "DOWN" if row["trade_return_pct"] < -meaningful else "NEUTRAL"
            )
        ),
    })
    return predicted


def brier_score(rows, probability_getter):
    if not rows:
        return 0.0
    total = 0.0
    for row in rows:
        probabilities = probability_getter(row)
        for name in ("UP", "DOWN", "NEUTRAL"):
            observed = 1.0 if row["actual_class"] == name else 0.0
            total += (float(probabilities[name]) - observed) ** 2
    return total / len(rows)


def log_loss(rows, probability_getter):
    if not rows:
        return 0.0
    losses = []
    for row in rows:
        probability = float(probability_getter(row)[row["actual_class"]])
        losses.append(-math.log(min(max(probability, 1e-12), 1.0)))
    return statistics.fmean(losses)


def binary_ece(pairs, bins=10):
    pairs = list(pairs)
    if not pairs:
        return 1.0
    # Sort only by probability. Sorting the whole tuple also sorts equal
    # probabilities by outcome, manufacturing calibration error for discrete
    # models that legitimately emit many identical probabilities.
    predictions = [
        (float(probability), int(outcome)) for probability, outcome in pairs
    ]
    predictions.sort(key=lambda item: item[0])
    error = 0.0
    target_size = max(1, math.ceil(len(predictions) / bins))
    start = 0
    while start < len(predictions):
        end = min(len(predictions), start + target_size)
        # Equal predictions are one calibration claim and must never be split
        # into outcome-ordered pseudo-bins.
        while (
            end < len(predictions)
            and predictions[end][0] == predictions[end - 1][0]
        ):
            end += 1
        group = predictions[start:end]
        confidence = statistics.fmean(item[0] for item in group)
        accuracy = statistics.fmean(item[1] for item in group)
        error += len(group) / len(predictions) * abs(confidence - accuracy)
        start = end
    return error


def reliability_line(pairs):
    if len(pairs) < 2:
        return 0.0, 0.0
    probabilities = [float(item[0]) for item in pairs]
    outcomes = [float(item[1]) for item in pairs]
    mean_probability = statistics.fmean(probabilities)
    mean_outcome = statistics.fmean(outcomes)
    variance = sum((value - mean_probability) ** 2 for value in probabilities)
    if variance <= 1e-12:
        return 0.0, mean_outcome
    slope = sum(
        (probability - mean_probability) * (outcome - mean_outcome)
        for probability, outcome in zip(probabilities, outcomes)
    ) / variance
    return slope, mean_outcome - slope * mean_probability


def pinball(actual, predicted, quantile):
    difference = float(actual) - float(predicted)
    return max(quantile * difference, (quantile - 1.0) * difference)


def wilson_lower(hits, count, z=1.959963984540054):
    if count <= 0:
        return 0.0
    proportion = hits / count
    denominator = 1.0 + z * z / count
    centre = proportion + z * z / (2.0 * count)
    adjustment = z * math.sqrt(
        (proportion * (1.0 - proportion) + z * z / (4.0 * count))
        / count
    )
    return max(0.0, (centre - adjustment) / denominator)


def week_key(timestamp):
    monday = timestamp.date() - timedelta(days=timestamp.weekday())
    return monday.isoformat()


def weekly_block_bootstrap_interval(
    rows, value_getter, seed, repetitions=2000, minimum_blocks=8
):
    """Event-weighted paired 95% interval by resampling whole UTC weeks."""
    weekly = defaultdict(list)
    for row in rows:
        weekly[week_key(row["as_of"])].append(float(value_getter(row)))
    blocks = [weekly[key] for key in sorted(weekly)]
    if len(blocks) < int(minimum_blocks):
        return float("-inf"), float("inf")
    rng = random.Random(int(seed))
    bootstrapped = []
    for _ in range(int(repetitions)):
        sample = []
        for _block in blocks:
            sample.extend(blocks[rng.randrange(len(blocks))])
        bootstrapped.append(statistics.fmean(sample))
    bootstrapped.sort()
    lower_index = max(0, math.floor(0.025 * (len(bootstrapped) - 1)))
    upper_index = min(
        len(bootstrapped) - 1,
        math.ceil(0.975 * (len(bootstrapped) - 1)),
    )
    return bootstrapped[lower_index], bootstrapped[upper_index]


def weekly_block_bootstrap_lower(rows, value_getter, seed, repetitions=2000):
    return weekly_block_bootstrap_interval(
        rows, value_getter, seed, repetitions
    )[0]


def directional_utility(row, direction=None, cost_multiplier=1.0):
    """Long net return or avoided-loss utility for a spot SELL/AVOID call.

    SAT is never represented as a short position or portfolio profit.
    """
    cost = ROUND_TRIP_COST_BPS / 100.0 * cost_multiplier
    direction = direction or row["risk_adjusted_action"]
    if direction == "AL":
        return row["trade_return_pct"] - cost
    if direction == "SAT":
        return -row["trade_return_pct"] - cost
    return None


def claimed_event_hit(row, direction=None):
    direction = direction or row["risk_adjusted_action"]
    return bool(
        (direction == "AL" and row["actual_class"] == "UP")
        or (direction == "SAT" and row["actual_class"] == "DOWN")
    )


def concentration(rows, key):
    if not rows:
        return {"largest": 1.0, "top3": 1.0, "unique": 0}
    counts = Counter(row[key] for row in rows)
    shares = sorted((value / len(rows) for value in counts.values()), reverse=True)
    return {
        "largest": shares[0],
        "top3": sum(shares[:3]),
        "unique": len(counts),
    }


def slice_support(rows, key, minimum_events, action_field="risk_adjusted_action"):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row[key]].append(row)
    evaluated = []
    supported = []
    unsupported = []
    for name, group in sorted(grouped.items()):
        if len(group) < minimum_events:
            continue
        evaluated.append(name)
        probabilities = [
            row["p_up"] if row[action_field] == "AL" else row["p_down"]
            for row in group
        ]
        outcomes = [int(claimed_event_hit(row, row[action_field])) for row in group]
        calibration_gap = abs(
            statistics.fmean(probabilities) - statistics.fmean(outcomes)
        )
        utility = statistics.fmean(
            directional_utility(row, row[action_field]) for row in group
        )
        if utility > 0.0 and calibration_gap <= 0.10:
            supported.append(name)
        else:
            unsupported.append(name)
    return {
        "evaluated": evaluated,
        "supported": supported,
        "unsupported": unsupported,
    }


def subwindow_positive_count(rows, action_field="risk_adjusted_action"):
    if not rows:
        return 0
    ordered = sorted(rows, key=lambda row: row["as_of"])
    # Contiguous chronological thirds, not round-robin buckets.
    size = math.ceil(len(ordered) / 3)
    groups = [ordered[start:start + size] for start in range(0, len(ordered), size)][:3]
    while len(groups) < 3:
        groups.append([])
    return sum(
        bool(group) and statistics.fmean(
            directional_utility(row, row[action_field]) for row in group
        ) > 0
        for group in groups
    )


def evaluate(model, rows):
    forecasts = [forecast_row(model, row) for row in rows]
    base = model["base_probabilities"]
    model_probs = lambda row: {
        "UP": row["p_up"], "DOWN": row["p_down"], "NEUTRAL": row["p_neutral"]
    }
    climate_probs = lambda row: base
    no_change_probs = lambda row: {"UP": 0.0, "DOWN": 0.0, "NEUTRAL": 1.0}
    model_brier = brier_score(forecasts, model_probs)
    climate_brier = brier_score(forecasts, climate_probs)
    no_change_brier = brier_score(forecasts, no_change_probs)
    benchmark_brier = min(climate_brier, no_change_brier)
    model_log = log_loss(forecasts, model_probs)
    climate_log = log_loss(forecasts, climate_probs)
    no_change_log = log_loss(forecasts, no_change_probs)
    benchmark_log = min(climate_log, no_change_log)
    benchmark_probability_getter = (
        climate_probs if climate_brier <= no_change_brier else no_change_probs
    )
    benchmark_log_getter = (
        climate_probs if climate_log <= no_change_log else no_change_probs
    )

    model_errors = []
    naive_errors = []
    model_distribution = []
    naive_distribution = []
    model_covered = 0
    naive_covered = 0
    model_widths = []
    naive_widths = []
    naive_q80 = float(model["naive_abs_q80"])
    for row in forecasts:
        actual = row["price_return_pct"]
        scale = row["scale_pct"]
        low = (row["price_p10"] / row["current_price"] - 1.0) * 100.0
        median = row["predicted_return_pct"]
        high = (row["price_p90"] / row["current_price"] - 1.0) * 100.0
        naive_low = -naive_q80 * scale
        naive_high = naive_q80 * scale
        model_errors.append(abs(actual - median) / scale)
        naive_errors.append(abs(actual) / scale)
        model_distribution.append(sum((
            pinball(actual, low, 0.10),
            pinball(actual, median, 0.50),
            pinball(actual, high, 0.90),
        )) / (3.0 * scale))
        naive_distribution.append(sum((
            pinball(actual, naive_low, 0.10),
            pinball(actual, 0.0, 0.50),
            pinball(actual, naive_high, 0.90),
        )) / (3.0 * scale))
        model_covered += int(low <= actual <= high)
        naive_covered += int(naive_low <= actual <= naive_high)
        model_widths.append((high - low) / scale)
        naive_widths.append((naive_high - naive_low) / scale)
        model_probabilities = model_probs(row)
        benchmark_probabilities = benchmark_probability_getter(row)
        row["_brier_advantage"] = sum(
            (benchmark_probabilities[name] - int(row["actual_class"] == name)) ** 2
            - (model_probabilities[name] - int(row["actual_class"] == name)) ** 2
            for name in ("UP", "DOWN", "NEUTRAL")
        )
        row["_log_loss_advantage"] = (
            -math.log(max(benchmark_log_getter(row)[row["actual_class"]], 1e-12))
            + math.log(max(model_probabilities[row["actual_class"]], 1e-12))
        )
        row["_mae_advantage"] = abs(actual) / scale - abs(actual - median) / scale
        row["_distribution_advantage"] = (
            naive_distribution[-1] - model_distribution[-1]
        )

    raw_actions = [
        row for row in forecasts
        if row["action"] in {"AL", "SAT"}
    ]
    post_risk_actions = [
        row for row in forecasts
        if row["risk_adjusted_action"] in {"AL", "SAT"}
    ]
    risk_vetoed_actions = [
        row for row in raw_actions if row["risk_adjusted_action"] == "BEKLE"
    ]
    actions = raw_actions
    action_utilities = [directional_utility(row, row["action"]) for row in actions]
    double_cost_utilities = [
        directional_utility(row, row["action"], cost_multiplier=2.0)
        for row in actions
    ]
    claimed_hits = sum(claimed_event_hit(row, row["action"]) for row in actions)
    economic_hits = sum(value > 0 for value in action_utilities)
    positives = sum(value for value in action_utilities if value > 0)
    negatives = abs(sum(value for value in action_utilities if value < 0))
    action_pairs = [
        (
            row["p_up"] if row["action"] == "AL" else row["p_down"],
            int(claimed_event_hit(row, row["action"])),
        )
        for row in actions
    ]
    for row, (probability, outcome) in zip(actions, action_pairs):
        row["_claimed_hit"] = float(outcome)
        row["_action_probability"] = float(probability)
        row["_calibration_bias"] = float(outcome) - float(probability)
    action_slope, action_intercept = reliability_line(action_pairs)
    action_predicted = statistics.fmean(item[0] for item in action_pairs) if action_pairs else 0.0
    action_observed = statistics.fmean(item[1] for item in action_pairs) if action_pairs else 0.0

    constant_direction = model["constant_baseline_direction"]
    for row in actions:
        row["_constant_baseline_utility"] = directional_utility(
            row, constant_direction
        )
        row["_action_advantage"] = (
            directional_utility(row, row["action"])
            - row["_constant_baseline_utility"]
        )

    up_ece = binary_ece((row["p_up"], row["actual_class"] == "UP")
                        for row in forecasts)
    down_ece = binary_ece((row["p_down"], row["actual_class"] == "DOWN")
                          for row in forecasts)
    neutral_ece = binary_ece(
        (row["p_neutral"], row["actual_class"] == "NEUTRAL")
        for row in forecasts
    )
    coin_skill_positive = 0
    coin_skill_evaluated = 0
    for symbol in SYMBOLS:
        coin_rows = [row for row in forecasts if row["symbol"] == symbol]
        if len(coin_rows) < 50:
            continue
        coin_skill_evaluated += 1
        if brier_score(coin_rows, model_probs) < brier_score(
            coin_rows, benchmark_probability_getter
        ):
            coin_skill_positive += 1
    coin = concentration(actions, "symbol")
    regime = concentration(actions, "regime")
    coin_slices = slice_support(
        actions, "symbol", minimum_events=20, action_field="action"
    )
    regime_slices = slice_support(
        actions, "regime", minimum_events=30, action_field="action"
    )
    forecast_weekly_blocks = len({week_key(row["as_of"]) for row in forecasts})
    action_weekly_blocks = len({week_key(row["as_of"]) for row in actions})
    claimed_hit_interval = weekly_block_bootstrap_interval(
        actions, lambda row: row["_claimed_hit"], 1700 + model["horizon"]
    )
    calibration_bias_interval = weekly_block_bootstrap_interval(
        actions, lambda row: row["_calibration_bias"], 1800 + model["horizon"]
    )
    metrics = {
        "events": len(forecasts),
        "model_brier": model_brier,
        "benchmark_brier": benchmark_brier,
        "brier_skill": 1.0 - model_brier / benchmark_brier if benchmark_brier else -1.0,
        "model_log_loss": model_log,
        "benchmark_log_loss": benchmark_log,
        "log_loss_improvement": 1.0 - model_log / benchmark_log if benchmark_log else -1.0,
        "up_ece": up_ece,
        "down_ece": down_ece,
        "neutral_ece": neutral_ece,
        "classwise_ece_max": max(up_ece, down_ece, neutral_ece),
        "actual_up_rate": sum(row["actual_class"] == "UP" for row in forecasts) / len(forecasts) if forecasts else 0.0,
        "actual_down_rate": sum(row["actual_class"] == "DOWN" for row in forecasts) / len(forecasts) if forecasts else 0.0,
        "actual_neutral_rate": sum(row["actual_class"] == "NEUTRAL" for row in forecasts) / len(forecasts) if forecasts else 0.0,
        "brier_advantage_block_lb": weekly_block_bootstrap_lower(
            forecasts, lambda row: row["_brier_advantage"], 1100 + model["horizon"]
        ),
        "log_loss_advantage_block_lb": weekly_block_bootstrap_lower(
            forecasts, lambda row: row["_log_loss_advantage"], 1200 + model["horizon"]
        ),
        "normalized_mae": statistics.fmean(model_errors) if model_errors else 0.0,
        "naive_normalized_mae": statistics.fmean(naive_errors) if naive_errors else 0.0,
        "mae_skill": 1.0 - statistics.fmean(model_errors) / statistics.fmean(naive_errors)
        if naive_errors and statistics.fmean(naive_errors) else -1.0,
        "distribution_loss": statistics.fmean(model_distribution) if model_distribution else 0.0,
        "naive_distribution_loss": statistics.fmean(naive_distribution) if naive_distribution else 0.0,
        "distribution_skill": 1.0 - statistics.fmean(model_distribution) / statistics.fmean(naive_distribution)
        if naive_distribution and statistics.fmean(naive_distribution) else -1.0,
        "mae_advantage_block_lb": weekly_block_bootstrap_lower(
            forecasts, lambda row: row["_mae_advantage"], 1300 + model["horizon"]
        ),
        "distribution_advantage_block_lb": weekly_block_bootstrap_lower(
            forecasts, lambda row: row["_distribution_advantage"], 1400 + model["horizon"]
        ),
        "interval_coverage": model_covered / len(forecasts) if forecasts else 0.0,
        "naive_interval_coverage": naive_covered / len(forecasts) if forecasts else 0.0,
        "interval_width_scaled": statistics.fmean(model_widths) if model_widths else 0.0,
        "naive_interval_width_scaled": statistics.fmean(naive_widths) if naive_widths else 0.0,
        "action_events": len(actions),
        "action_coverage": len(actions) / len(forecasts) if forecasts else 0.0,
        "post_risk_action_events": len(post_risk_actions),
        "post_risk_action_coverage": len(post_risk_actions) / len(forecasts) if forecasts else 0.0,
        "risk_vetoed_action_events": len(risk_vetoed_actions),
        "risk_veto_rate": len(risk_vetoed_actions) / len(actions) if actions else 0.0,
        "action_claimed_event_hit_rate": claimed_hits / len(actions) if actions else 0.0,
        "action_claimed_hit_block_lb": claimed_hit_interval[0],
        "action_claimed_hit_block_ub": claimed_hit_interval[1],
        "action_wilson_lower": wilson_lower(claimed_hits, len(actions)),
        "action_economic_net_hit_rate": economic_hits / len(actions) if actions else 0.0,
        "action_mean_predicted_probability": action_predicted,
        "action_observed_frequency": action_observed,
        "action_calibration_gap": abs(action_predicted - action_observed),
        "action_calibration_bias_block_lb": calibration_bias_interval[0],
        "action_calibration_bias_block_ub": calibration_bias_interval[1],
        "action_ece": binary_ece(action_pairs),
        "action_calibration_slope": action_slope,
        "action_calibration_intercept": action_intercept,
        "average_directional_utility_pct": statistics.fmean(action_utilities)
        if action_utilities else 0.0,
        "double_cost_directional_utility_pct": statistics.fmean(double_cost_utilities)
        if double_cost_utilities else 0.0,
        "constant_baseline_direction": constant_direction,
        "constant_baseline_utility_pct": statistics.fmean(
            directional_utility(row, constant_direction) for row in actions
        ) if actions else 0.0,
        "action_utility_block_lb": weekly_block_bootstrap_lower(
            actions, lambda row: directional_utility(row, row["action"]),
            1500 + model["horizon"]
        ),
        "action_vs_constant_block_lb": weekly_block_bootstrap_lower(
            actions, lambda row: row["_action_advantage"], 1600 + model["horizon"]
        ),
        "profit_factor": positives / negatives if negatives else (float("inf") if positives else 0.0),
        "forecast_weekly_blocks": forecast_weekly_blocks,
        "action_weekly_blocks": action_weekly_blocks,
        "coin_skill_evaluated": coin_skill_evaluated,
        "coin_skill_positive": coin_skill_positive,
        "supported_coin_slices": len(coin_slices["supported"]),
        "supported_coin_slice_names": ",".join(coin_slices["supported"]),
        "unsupported_coin_slices": ",".join(coin_slices["unsupported"]),
        "supported_regime_slices": len(regime_slices["supported"]),
        "supported_regime_slice_names": ",".join(regime_slices["supported"]),
        "unsupported_regime_slices": ",".join(regime_slices["unsupported"]),
        "coin_count": coin["unique"],
        "largest_coin_share": coin["largest"],
        "top3_coin_share": coin["top3"],
        "regime_count": regime["unique"],
        "largest_regime_share": regime["largest"],
        "positive_subwindows": subwindow_positive_count(
            actions, action_field="action"
        ),
    }
    return forecasts, metrics


def validation_gate(metrics):
    checks = {
        "events_gte_1000": metrics["events"] >= 1000,
        "forecast_weekly_blocks_gte_8": metrics["forecast_weekly_blocks"] >= 8,
        "action_weekly_blocks_gte_8": metrics["action_weekly_blocks"] >= 8,
        "brier_skill_gte_5pct": metrics["brier_skill"] >= 0.05,
        "log_loss_improvement_gte_2pct": metrics["log_loss_improvement"] >= 0.02,
        "classwise_ece_lte_8pct": metrics["classwise_ece_max"] <= 0.08,
        "brier_block_lb_positive": metrics["brier_advantage_block_lb"] > 0.0,
        "log_loss_block_lb_positive": metrics["log_loss_advantage_block_lb"] > 0.0,
        "mae_skill_gte_5pct": metrics["mae_skill"] >= 0.05,
        "distribution_skill_gte_5pct": metrics["distribution_skill"] >= 0.05,
        "mae_block_lb_positive": metrics["mae_advantage_block_lb"] > 0.0,
        "distribution_block_lb_positive": metrics["distribution_advantage_block_lb"] > 0.0,
        "interval_coverage_72_88pct": 0.72 <= metrics["interval_coverage"] <= 0.88,
        "interval_not_wider_than_naive": (
            metrics["interval_width_scaled"]
            <= metrics["naive_interval_width_scaled"] * 1.05
        ),
        "action_events_gte_100": metrics["action_events"] >= 100,
        "action_coverage_5_35pct": 0.05 <= metrics["action_coverage"] <= 0.35,
        "action_claimed_hit_gte_65pct": metrics["action_claimed_event_hit_rate"] >= 0.65,
        "action_wilson_gte_55pct": metrics["action_wilson_lower"] >= 0.55,
        "action_block_hit_lb_gte_55pct": metrics["action_claimed_hit_block_lb"] >= 0.55,
        "action_ece_lte_7pct": metrics["action_ece"] <= 0.07,
        "action_calibration_gap_lte_5pct": metrics["action_calibration_gap"] <= 0.05,
        "action_calibration_bias_block_inside_5pct": (
            metrics["action_calibration_bias_block_lb"] >= -0.05
            and metrics["action_calibration_bias_block_ub"] <= 0.05
        ),
        "action_calibration_slope_0_8_1_2": 0.80 <= metrics["action_calibration_slope"] <= 1.20,
        "action_calibration_intercept_lte_10pct": abs(metrics["action_calibration_intercept"]) <= 0.10,
        "directional_utility_positive": metrics["average_directional_utility_pct"] > 0.0,
        "double_cost_utility_positive": metrics["double_cost_directional_utility_pct"] > 0.0,
        "action_utility_block_lb_positive": metrics["action_utility_block_lb"] > 0.0,
        "beats_constant_direction_block_lb": metrics["action_vs_constant_block_lb"] > 0.0,
        "profit_factor_gte_1_15": metrics["profit_factor"] >= 1.15,
        "coins_gte_5": metrics["coin_count"] >= 5,
        "largest_coin_lte_35pct": metrics["largest_coin_share"] <= 0.35,
        "top3_coins_lte_70pct": metrics["top3_coin_share"] <= 0.70,
        "regimes_gte_2": metrics["regime_count"] >= 2,
        "largest_regime_lte_80pct": metrics["largest_regime_share"] <= 0.80,
        "positive_subwindows_gte_2": metrics["positive_subwindows"] >= 2,
        "coin_skill_positive_gte_5": metrics["coin_skill_positive"] >= 5,
        "supported_coin_slices_gte_5": metrics["supported_coin_slices"] >= 5,
        "supported_regime_slices_gte_2": metrics["supported_regime_slices"] >= 2,
    }
    return checks, all(checks.values())


def serializable(value):
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, float) and not math.isfinite(value):
        return "inf" if value > 0 else "-inf"
    return value


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({key: serializable(value) for key, value in row.items()}
                         for row in rows)


def compact_forecast(row):
    reasons = ("FORECAST_RESEARCH_ONLY",)
    if not row["risk_eligible"]:
        reasons += ("RISK_VETO",)
    contract = build_forecast_record(
        symbol=row["symbol"],
        as_of=iso(row["as_of"]),
        feature_cutoff=iso(row["feature_cutoff"]),
        due_time=iso(row["due_time"]),
        horizon_hours=row["horizon"],
        current_price=row["current_price"],
        target_price=row["target_price"],
        price_p10=row["price_p10"],
        price_p90=row["price_p90"],
        interval_probability=INTERVAL_PROBABILITY,
        round_trip_cost_bps=ROUND_TRIP_COST_BPS,
        minimum_move_bps=MINIMUM_MOVE_BPS,
        action_probability_threshold=ACTION_PROBABILITY,
        target_reference=REFERENCE_PRICE_BASIS,
        entry_rule=ENTRY_RULE,
        probability_event=PROBABILITY_EVENT,
        p_up_net=row["p_up"],
        p_down_net=row["p_down"],
        p_neutral=row["p_neutral"],
        expected_net_return_pct=row["expected_net_return_pct"],
        shadow_action=row["action"],
        risk_adjusted_action=row["risk_adjusted_action"],
        risk_eligible=row["risk_eligible"],
        risk_state=row["risk_state"],
        risk_cap_pct=row["risk_cap_pct"],
        model_version=MODEL_VERSION,
        artifact_hash="DEVELOPMENT_UNSEALED",
        calibration_state="DEVELOPMENT_ONLY",
        production_eligible=False,
        reason_codes=reasons,
    ).to_dict()
    return {
        "lab_version": LAB_VERSION,
        "segment": row["segment"],
        **contract,
        "actual_price": row["exit_price"],
        "actual_trade_return_pct": row["trade_return_pct"],
        "actual_class": row["actual_class"],
        "regime": row["regime"],
    }


def model_artifact(models, boundaries, passed_horizons):
    payload = {
        "contract_version": CONTRACT_VERSION,
        "lab_version": LAB_VERSION,
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "created_at": iso(utcnow()),
        "state": "DEVELOPMENT_ONLY",
        "production_eligible": False,
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "minimum_move_bps": MINIMUM_MOVE_BPS,
        "action_probability": ACTION_PROBABILITY,
        "ridge_objective": "MEAN_SQUARED_NORMALIZED_RETURN_PLUS_ALPHA_L2",
        "ridge_alpha": RIDGE_ALPHA,
        "feature_names": FEATURE_NAMES,
        "boundaries": {key: iso(value) for key, value in boundaries.items()},
        "horizons": {},
        "warning": (
            "Historical EXAM chronology was previously observed by the project; "
            "this artifact is development evidence only and requires a newly "
            "sealed future period plus forward validation. The fixed current "
            "13-symbol universe also carries survivorship/listing-history bias."
        ),
    }
    for horizon in passed_horizons:
        model = models[horizon]
        payload["horizons"][str(horizon)] = {
            key: model[key]
            for key in (
                "standardizer", "coefficients", "price_residuals",
                "trade_residuals", "price_residual_mean",
                "trade_residual_mean", "price_q10", "price_q50",
                "price_q90", "naive_abs_q80", "base_probabilities",
                "constant_baseline_direction", "fit_rows", "calibration_rows",
            )
        }
    return payload


def seal_artifact(payload):
    sealed = dict(payload)
    sealed.pop("artifact_hash", None)
    canonical = json.dumps(sealed, sort_keys=True, separators=(",", ":"))
    sealed["artifact_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return sealed


def artifact_hash_valid(payload):
    expected = str(payload.get("artifact_hash") or "")
    return bool(expected) and seal_artifact(payload)["artifact_hash"] == expected


def run_lab(now=None):
    from data import historical_market_data

    if LOOKBACK_DAYS < 180:
        raise ValueError("AGCA_FORECAST_LAB_DAYS must be at least 180")
    start, fetch_start, end = requested_range(now)
    boundaries = split_boundaries(start, end)
    historical_market_data.DB_NAME = DB_NAME
    historical_market_data.create_market_klines_table()
    con = runtime_connect(DB_NAME)
    try:
        fetch_result = ensure_historical_coverage(con, fetch_start, end)
        from data.truth_engine import DataValidationError, record_audit
        try:
            series = load_series(con, fetch_start, end)
        except DataValidationError as exc:
            record_audit(
                con, gate_name="FORECAST_LAB_MARKET_SERIES",
                source=exc.source, status="DATA_BLOCKED", reasons=exc.reasons,
            )
            raise
        record_audit(
            con, gate_name="FORECAST_LAB_MARKET_SERIES",
            source="BINANCE_MARKET_KLINES", status="PASS",
            record_count=sum(len(item["candles"]) for item in series.values()),
        )
        built = build_rows(series, start, end)
    finally:
        con.close()

    segmented = assign_segments(built["rows"], boundaries)
    selected = non_overlapping(segmented)
    fit_rows = [row for row in selected if row["segment"] == "FIT"]
    calibration_rows = [
        row for row in selected if row["segment"] == "CALIBRATION"
    ]
    models = {}
    selection = []
    detail = []
    passed_horizons = []

    for horizon in HORIZONS:
        try:
            model = fit_model(fit_rows, calibration_rows, horizon)
            models[horizon] = model
            validation_rows = [
                row for row in selected
                if row["segment"] == "VALIDATION" and row["horizon"] == horizon
            ]
            validation_forecasts, validation_metrics = evaluate(
                model, validation_rows
            )
            checks, passed = validation_gate(validation_metrics)
            checks["point_in_time_gaps_zero"] = built["source_gaps"] == 0
            checks["all_symbols_ready"] = all(
                item.get("status") in {"READY", "FETCHED_READY"}
                for item in fetch_result.values()
            )
            passed = all(checks.values())
            detail.extend(compact_forecast(row) for row in validation_forecasts)
            selection.append({
                "lab_version": LAB_VERSION,
                "model_version": MODEL_VERSION,
                "horizon_hours": horizon,
                "segment": "VALIDATION",
                "status": "PASS" if passed else "REJECT",
                "failed_gates": ",".join(
                    name for name, value in checks.items() if not value
                ),
                **validation_metrics,
            })
            if not passed:
                continue

            # This chronology is opened only after validation, but earlier
            # project work has already exposed the broad dates.  It is named
            # OBSERVED_DIAGNOSTIC, never pristine EXAM/product evidence.
            exam_rows = [
                row for row in selected
                if row["segment"] == "EXAM" and row["horizon"] == horizon
            ]
            exam_forecasts, exam_metrics = evaluate(model, exam_rows)
            detail.extend(compact_forecast(row) for row in exam_forecasts)
            selection.append({
                "lab_version": LAB_VERSION,
                "model_version": MODEL_VERSION,
                "horizon_hours": horizon,
                "segment": "OBSERVED_DIAGNOSTIC",
                "status": "MEASURED_NOT_PROMOTED",
                "failed_gates": "NOT_PRISTINE_PRODUCT_EVIDENCE",
                **exam_metrics,
            })
            contradicted = bool(
                exam_metrics["brier_skill"] <= 0.0
                or exam_metrics["mae_skill"] <= 0.0
                or exam_metrics["classwise_ece_max"] > 0.10
                or (
                    exam_metrics["action_events"] >= 50
                    and exam_metrics["average_directional_utility_pct"] <= 0.0
                )
            )
            if contradicted:
                selection[-1]["status"] = "CONTRADICTED_BY_OBSERVED_HOLDOUT"
                selection[-1]["failed_gates"] += ",OBSERVED_DIAGNOSTIC_CONTRADICTION"
            else:
                passed_horizons.append(horizon)
        except Exception as exc:
            selection.append({
                "lab_version": LAB_VERSION,
                "model_version": MODEL_VERSION,
                "horizon_hours": horizon,
                "segment": "VALIDATION",
                "status": "ERROR",
                "failed_gates": f"MODEL_ERROR:{exc}",
            })

    write_csv(DETAIL_PATH, detail)
    write_csv(SELECTION_PATH, selection)
    artifact = model_artifact(models, boundaries, passed_horizons)
    if passed_horizons:
        artifact["state"] = "HISTORICAL_DEVELOPMENT_CANDIDATE"
        conclusion = "HISTORICAL_DEVELOPMENT_CANDIDATE"
    else:
        artifact["state"] = "NO_VALIDATED_PROBABILISTIC_EDGE"
        artifact["horizons"] = {}
        artifact["production_eligible"] = False
        conclusion = "NO_VALIDATED_PROBABILISTIC_EDGE"
    artifact = seal_artifact(artifact)
    CANDIDATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATE_PATH.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, default=serializable) + "\n",
        encoding="utf-8",
    )
    return {
        "start": start,
        "end": end,
        "boundaries": boundaries,
        "fetch_result": fetch_result,
        "built": built,
        "segmented_rows": len(segmented),
        "selected_rows": len(selected),
        "selection": selection,
        "passed_horizons": passed_horizons,
        "conclusion": conclusion,
        "artifact_hash": artifact["artifact_hash"],
    }


def percent(value):
    value = float(value)
    return "N/A" if not math.isfinite(value) else f"{100.0 * value:.1f}%"


def render(result):
    fetch_result = result["fetch_result"]
    fetched = sum(item.get("fetched", 0) for item in fetch_result.values())
    fetch_errors = sum(item.get("status") == "ERROR" for item in fetch_result.values())
    ready = sum(item.get("status") in {"READY", "FETCHED_READY"}
                for item in fetch_result.values())
    print("AG Crypto Analyst - Probabilistic Forecast Core Lab v1.0.1")
    print(
        f"Contract: {CONTRACT_VERSION} | model={MODEL_VERSION} | "
        f"horizons=1h,4h,24h | cost={ROUND_TRIP_COST_BPS:.1f}bps | "
        f"min_move={MINIMUM_MOVE_BPS:.1f}bps"
    )
    print(
        f"Data: ready={ready}/{len(SYMBOLS)} | new_candles={fetched} | "
        f"fetch_errors={fetch_errors} | point_in_time_gaps={result['built']['source_gaps']}"
    )
    boundaries = result["boundaries"]
    print(
        "Chronology: "
        f"FIT<{iso(boundaries['fit_end'])} | "
        f"CAL<{iso(boundaries['learn_end'])} | "
        f"VAL<{iso(boundaries['validation_end'])} | "
        "OBSERVED_DIAGNOSTIC=remaining_if_VAL_passes"
    )
    print(
        f"Contexts: raw={len(result['built']['rows'])} | "
        f"strict={result['segmented_rows']} | "
        f"non_overlap={result['selected_rows']} | "
        f"risk_blocks={result['built']['risk_blocks']}"
    )

    for row in result["selection"]:
        if row["segment"] != "VALIDATION":
            continue
        horizon = row["horizon_hours"]
        if row["status"] == "ERROR":
            print(f"\n{horizon}H VALIDATION | ERROR | {row['failed_gates']}")
            continue
        print(
            f"\n{horizon}H VALIDATION | {row['status']} | n={row['events']} | "
            f"BrierSkill={percent(row['brier_skill'])} | "
            f"MAESkill={percent(row['mae_skill'])} | "
            f"classECE={percent(row['classwise_ece_max'])} | "
            f"interval={percent(row['interval_coverage'])}"
        )
        block_lb = (
            f"{row['action_utility_block_lb']:+.3f}%"
            if math.isfinite(float(row["action_utility_block_lb"]))
            else "N/A"
        )
        print(
            f"raw_actions={row['action_events']} ({percent(row['action_coverage'])}) | "
            f"risk_veto={row['risk_vetoed_action_events']} | "
            f"post_risk={row['post_risk_action_events']} | "
            f"forecast_weeks={row['forecast_weekly_blocks']} | "
            f"action_weeks={row['action_weekly_blocks']} | "
            f"claimed_hit={percent(row['action_claimed_event_hit_rate'])} | "
            f"weekly_hit_LB={percent(row['action_claimed_hit_block_lb'])} | "
            f"utility={row['average_directional_utility_pct']:+.3f}% | "
            f"block_bootstrap_LB={block_lb}"
        )
        if row["failed_gates"]:
            print(f"failed: {row['failed_gates']}")

    print(f"\nCONCLUSION: {result['conclusion']} | product_state=SHADOW_ONLY")
    if result["passed_horizons"]:
        print(
            "Historical validation passed for: "
            + ", ".join(f"{value}h" for value in result["passed_horizons"])
            + ". This is development evidence, not a product forecast."
        )
    else:
        print(
            "The single pre-registered price-only challenger was rejected. "
            "No threshold was tuned and OBSERVED_DIAGNOSTIC was not opened "
            "for failed horizons."
        )
    print(
        "Live Decision Engine and alert policy were not changed. A new sealed "
        "future period and forward calibration remain mandatory."
    )
    print(
        "SAT means SELL/REDUCE/AVOID for a spot holder; it is not counted as "
        "a short position or portfolio profit."
    )
    print(f"Details: {DETAIL_PATH}")
    print(f"Selection: {SELECTION_PATH}")
    print(f"Manifest: {CANDIDATE_PATH}")


def synthetic_row(features, target):
    return {"features": list(features), "target_normalized": float(target)}


def self_test():
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bounds = split_boundaries(base, base + timedelta(days=100))
    assert strict_segment(
        bounds["start"] + timedelta(days=1),
        bounds["start"] + timedelta(days=1, hours=24),
        bounds,
    ) == "FIT"
    assert strict_segment(
        bounds["fit_end"] - timedelta(hours=1),
        bounds["fit_end"] + timedelta(hours=1),
        bounds,
    ) is None

    dimension = len(FEATURE_NAMES)
    ridge_rows = []
    for index in range(120):
        features = [0.0] * dimension
        features[0] = (index - 60) / 10.0
        features[1] = math.sin(index / 7.0)
        ridge_rows.append(synthetic_row(features, 1.25 + 0.75 * features[0]))
    standardizer = fit_standardizer(ridge_rows)
    coefficients = fit_ridge(ridge_rows, standardizer)
    errors = [
        abs(predict_normalized(coefficients, row["features"], standardizer)
            - row["target_normalized"])
        for row in ridge_rows
    ]
    assert statistics.fmean(errors) < 0.15
    frozen_mean = standardizer["means"][0]
    future = synthetic_row([9999.0] + [0.0] * (dimension - 1), 0.0)
    _ = transform(future["features"], standardizer)
    assert standardizer["means"][0] == frozen_mean

    residuals = [-2.0, -1.0, 0.0, 1.0, 2.0]
    assert finite_sample_quantile(residuals, 0.50) == 0.0
    assert probability_gt(residuals, 0.5) > 0.0
    assert probability_lt(residuals, -0.5) > 0.0

    record = build_forecast_record(
        symbol="BTCUSDT",
        as_of=iso(base),
        feature_cutoff=iso(base),
        due_time=iso(base + timedelta(hours=1)),
        horizon_hours=1,
        current_price=100.0,
        target_price=101.0,
        price_p10=98.0,
        price_p90=103.0,
        interval_probability=0.80,
        round_trip_cost_bps=ROUND_TRIP_COST_BPS,
        minimum_move_bps=MINIMUM_MOVE_BPS,
        action_probability_threshold=ACTION_PROBABILITY,
        target_reference=REFERENCE_PRICE_BASIS,
        entry_rule=ENTRY_RULE,
        probability_event=PROBABILITY_EVENT,
        p_up_net=0.70,
        p_down_net=0.20,
        p_neutral=0.10,
        expected_net_return_pct=0.50,
        shadow_action="AL",
        risk_adjusted_action="AL",
        risk_eligible=True,
        risk_state="NORMAL",
        risk_cap_pct=10.0,
        model_version=MODEL_VERSION,
        artifact_hash="test",
        calibration_state="DEVELOPMENT_ONLY",
        production_eligible=False,
        reason_codes=("SELF_TEST",),
    )
    assert record.to_dict()["target_price"] == 101.0
    bad = record.to_dict()
    bad["current_price"] = float("nan")
    try:
        build_forecast_record(**bad)
        raise AssertionError("NaN contract value was accepted")
    except ValueError:
        pass
    bad = record.to_dict()
    bad["due_time"] = iso(base + timedelta(hours=2))
    try:
        build_forecast_record(**bad)
        raise AssertionError("wrong horizon duration was accepted")
    except ValueError:
        pass
    bad = record.to_dict()
    bad["risk_adjusted_action"] = "SAT"
    try:
        build_forecast_record(**bad)
        raise AssertionError("risk layer reversed forecast direction")
    except ValueError:
        pass
    bad = record.to_dict()
    bad["risk_adjusted_action"] = "SAT"
    try:
        build_forecast_record(**bad)
        raise AssertionError("risk layer reversed the forecast direction")
    except ValueError:
        pass

    # Absolute price target is anchored to known decision close; trade P&L is
    # independently anchored to the next open.
    current_close, next_open, exit_close = 100.0, 102.0, 103.0
    assert round(pct_change(exit_close, current_close), 6) == 3.0
    assert round(pct_change(exit_close, next_open), 6) != 3.0
    threshold_semantics = {
        "risk_adjusted_action": "AL",
        "actual_class": "NEUTRAL",
        "trade_return_pct": 0.12,
    }
    assert directional_utility(threshold_semantics) > 0.0
    assert not claimed_event_hit(threshold_semantics)
    vetoed_raw_action = {
        "action": "AL", "risk_adjusted_action": "BEKLE",
        "trade_return_pct": 0.50,
    }
    assert directional_utility(vetoed_raw_action) is None
    assert directional_utility(
        vetoed_raw_action, vetoed_raw_action["action"]
    ) is not None
    perfectly_calibrated_ties = [(0.75, index < 75) for index in range(100)]
    assert binary_ece(perfectly_calibrated_ties) < 1e-12

    candles = []
    for index in range(200):
        open_time = base + timedelta(hours=index)
        price = 100.0 + index * 0.1
        candles.append({
            "open_time": open_time,
            "close_time": open_time + timedelta(hours=1),
            "open": price,
            "high": price + 0.2,
            "low": price - 0.2,
            "close": price + 0.1,
            "volume": 10.0,
            "quote_volume": 1000.0 + index,
        })
    before = feature_values("BTCUSDT", candles, 168, candles, 168)
    candles[169]["open"] = 999999.0
    candles[169]["close"] = 999999.0
    after = feature_values("BTCUSDT", candles, 168, candles, 168)
    assert before == after

    artifact = seal_artifact({"state": "TEST", "horizons": {}})
    assert artifact_hash_valid(artifact)
    artifact["state"] = "MUTATED"
    assert not artifact_hash_valid(artifact)

    block_rows = [
        {"as_of": base + timedelta(weeks=index), "value": 0.70}
        for index in range(10)
    ]
    block_interval = weekly_block_bootstrap_interval(
        block_rows, lambda row: row["value"], seed=42, repetitions=200
    )
    assert 0.69 <= block_interval[0] <= block_interval[1] <= 0.71
    too_few_blocks = weekly_block_bootstrap_interval(
        block_rows[:7], lambda row: row["value"], seed=42, repetitions=50
    )
    assert too_few_blocks == (float("-inf"), float("inf"))

    print("Forecast Core self-test: PASS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run deterministic unit checks without market data or network",
    )
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run_lab()
    render(result)


if __name__ == "__main__":
    main()
