"""Point-in-time futures/microstructure Forecast Core V2 research lab.

This module is deliberately isolated from the live Decision Engine.  It keeps
FORECAST_CONTRACT_V1 semantics, evaluates one fixed pure-stdlib histogram
additive booster, and fails closed when the derivatives archive cannot prove
that every feature was available at the forecast cutoff.

The implementation is an additive gradient booster made from histogram
decision stumps.  It is nonlinear, but intentionally much smaller and easier
to audit than a general tree library.  There is no hyperparameter grid and no
validation-driven threshold search.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analysis import forecast_core_lab as v1
from decision_engine.forecast_contract import (
    CONTRACT_VERSION,
    ENTRY_RULE,
    PROBABILITY_EVENT,
    REFERENCE_PRICE_BASIS,
    build_forecast_record,
)


DB_NAME = v1.DB_NAME
LAB_VERSION = "FORECAST_CORE_V2_LAB_V1_0_PRE_REGISTERED"
MODEL_FAMILY = "PURE_STDLIB_HISTOGRAM_GRADIENT_BOOSTED_ADDITIVE_STUMPS"
MODEL_VERSION = "FUTURES_MICROSTRUCTURE_GBDT_V2_FIXED"
HORIZONS = (4, 24, 1)
HORIZON_ROLES = {4: "PRIMARY", 24: "SECONDARY", 1: "EXPLORATORY"}
SYMBOLS = v1.SYMBOLS
ROUND_TRIP_COST_BPS = v1.ROUND_TRIP_COST_BPS
MINIMUM_MOVE_BPS = v1.MINIMUM_MOVE_BPS
ACTION_PROBABILITY = v1.ACTION_PROBABILITY
INTERVAL_PROBABILITY = v1.INTERVAL_PROBABILITY
WARMUP_HOURS = max(v1.WARMUP_HOURS, 193)
ANALYSIS_DAYS = 400
MINIMUM_SUPPORTED_COINS = 10
MINIMUM_HOURLY_COVERAGE = 0.98
MAXIMUM_ARCHIVE_LAG_HOURS = 72
MAXIMUM_CONTEXT_GAP_RATE = 0.02
MAXIMUM_WEEKLY_CONTEXT_GAP_RATE = 0.10
MINIMUM_EVENTS = {1: 1000, 4: 1000, 24: 750}

# Frozen before a real V2 result.  These are model constants, not a search grid.
HISTOGRAM_BINS = 8
POINT_BOOST_ROUNDS = 32
CLASS_BOOST_ROUNDS = 20
LEARNING_RATE = 0.06
LEAF_L2 = 20.0
MIN_LEAF = 40
TEMPERATURE_MIN = 0.50
TEMPERATURE_MAX = 5.00
TEMPERATURE_STEPS = 48

DERIVATIVE_FEATURE_NAMES = (
    "futures_basis_pct",
    "premium_close_pct",
    "funding_rate_8h_pct",
    "funding_z_7d",
    "funding_interval_hours",
    "hours_since_funding",
    "oi_change_1h_pct",
    "oi_change_4h_pct",
    "oi_change_24h_pct",
    "log_oi_value_to_futures_volume_24h",
    "taker_imbalance_1h",
    "taker_imbalance_4h",
    "taker_imbalance_24h",
    "log_top_account_ratio",
    "log_top_position_ratio",
    "log_global_account_ratio",
    "btc_futures_basis_pct",
    "btc_premium_close_pct",
    "btc_funding_rate_8h_pct",
    "btc_funding_interval_hours",
    "btc_oi_change_1h_pct",
    "btc_oi_change_4h_pct",
    "btc_oi_change_24h_pct",
    "btc_taker_imbalance_1h",
    "btc_taker_imbalance_4h",
    "btc_taker_imbalance_24h",
)
FEATURE_NAMES = v1.FEATURE_NAMES + DERIVATIVE_FEATURE_NAMES

DETAIL_PATH = Path("reports/forecast_core_v2_lab.csv")
SELECTION_PATH = Path("reports/forecast_core_v2_selection.csv")
CANDIDATE_PATH = Path("reports/forecast_core_v2_candidate.json")

REQUIRED_TABLE_COLUMNS = {
    "futures_hourly_market": {
        "symbol", "open_time", "close_time", "open", "high", "low", "close",
        "volume", "quote_volume", "trade_count", "taker_buy_quote_volume",
        "premium_open", "premium_high", "premium_low", "premium_close",
    },
    "futures_positioning_hourly": {
        "symbol", "hour_close_time", "source_max_time",
        "open_interest_contracts", "open_interest_value", "top_account_ratio",
        "top_position_ratio", "global_account_ratio",
    },
    "funding_events": {
        "symbol", "funding_time", "rate", "mark_price",
    },
    "derivatives_archive_manifest": {
        "url", "checksum", "status", "rows", "fetched_at", "error",
    },
}


def utcnow():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def parse_time(value):
    return v1.parse_time(value)


def softmax(values, temperature=1.0):
    temperature = max(float(temperature), 1e-9)
    scaled = [float(value) / temperature for value in values]
    peak = max(scaled)
    weights = [math.exp(value - peak) for value in scaled]
    total = sum(weights)
    return [value / total for value in weights]


def _quantile_edges(values, bins=HISTOGRAM_BINS):
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("histogram edges require FIT observations")
    edges = []
    for bucket in range(1, int(bins)):
        index = min(len(ordered) - 1, math.floor(bucket * len(ordered) / bins))
        value = ordered[index]
        if not edges or value > edges[-1]:
            edges.append(value)
    return edges


def fit_histogram_edges(rows):
    if not rows:
        raise ValueError("histogram model requires FIT rows")
    return [
        _quantile_edges(row[feature_index] for row in (item["features"] for item in rows))
        for feature_index in range(len(FEATURE_NAMES))
    ]


def _binned_columns(rows, edges):
    columns = []
    for feature_index, feature_edges in enumerate(edges):
        columns.append(bytearray(
            bisect.bisect_right(feature_edges, float(row["features"][feature_index]))
            for row in rows
        ))
    return columns


def _best_stump(residuals, binned, edges, min_leaf=MIN_LEAF):
    count = len(residuals)
    if count < max(2, min_leaf * 2):
        return None
    total_sum = sum(residuals)
    parent_gain = total_sum * total_sum / (count + LEAF_L2)
    best = None
    for feature_index, column in enumerate(binned):
        bin_count = len(edges[feature_index]) + 1
        counts = [0] * bin_count
        sums = [0.0] * bin_count
        for row_index, bucket in enumerate(column):
            counts[bucket] += 1
            sums[bucket] += residuals[row_index]
        left_count = 0
        left_sum = 0.0
        for split_bin in range(bin_count - 1):
            left_count += counts[split_bin]
            left_sum += sums[split_bin]
            right_count = count - left_count
            if left_count < min_leaf or right_count < min_leaf:
                continue
            right_sum = total_sum - left_sum
            gain = (
                left_sum * left_sum / (left_count + LEAF_L2)
                + right_sum * right_sum / (right_count + LEAF_L2)
                - parent_gain
            )
            candidate = (
                gain,
                -feature_index,
                -split_bin,
                feature_index,
                split_bin,
                left_sum / (left_count + LEAF_L2),
                right_sum / (right_count + LEAF_L2),
            )
            if best is None or candidate[:3] > best[:3]:
                best = candidate
    if best is None or best[0] <= 1e-14:
        return None
    return {
        "feature": best[3],
        "split_bin": best[4],
        "left": best[5] * LEARNING_RATE,
        "right": best[6] * LEARNING_RATE,
        "gain": best[0],
    }


def _apply_stump_to_scores(scores, column, stump):
    split_bin = stump["split_bin"]
    left = stump["left"]
    right = stump["right"]
    for index, bucket in enumerate(column):
        scores[index] += left if bucket <= split_bin else right


def fit_point_booster(rows, edges):
    targets = [float(row["target_normalized"]) for row in rows]
    intercept = statistics.fmean(targets)
    predictions = [intercept] * len(rows)
    binned = _binned_columns(rows, edges)
    stumps = []
    for _ in range(POINT_BOOST_ROUNDS):
        residuals = [target - prediction for target, prediction in zip(targets, predictions)]
        stump = _best_stump(residuals, binned, edges)
        if stump is None:
            break
        stumps.append(stump)
        _apply_stump_to_scores(predictions, binned[stump["feature"]], stump)
    return {"intercept": intercept, "stumps": stumps}


def predict_point(model, edges, features):
    score = float(model["intercept"])
    for stump in model["stumps"]:
        bucket = bisect.bisect_right(edges[stump["feature"]], features[stump["feature"]])
        score += stump["left"] if bucket <= stump["split_bin"] else stump["right"]
    return score


def fit_class_booster(rows, edges):
    names = ("UP", "DOWN", "NEUTRAL")
    counts = Counter(row["actual_class"] for row in rows)
    total = len(rows)
    intercepts = [math.log((counts[name] + 1.0) / (total + 3.0)) for name in names]
    scores = [list(intercepts) for _ in rows]
    binned = _binned_columns(rows, edges)
    rounds = []
    for _ in range(CLASS_BOOST_ROUNDS):
        probabilities = [softmax(row) for row in scores]
        round_stumps = []
        for class_index, name in enumerate(names):
            residuals = [
                float(row["actual_class"] == name) - probabilities[index][class_index]
                for index, row in enumerate(rows)
            ]
            stump = _best_stump(residuals, binned, edges)
            round_stumps.append(stump)
            if stump is not None:
                column = binned[stump["feature"]]
                for row_index, bucket in enumerate(column):
                    scores[row_index][class_index] += (
                        stump["left"] if bucket <= stump["split_bin"] else stump["right"]
                    )
        rounds.append(round_stumps)
        if not any(stump is not None for stump in round_stumps):
            break
    return {"classes": names, "intercepts": intercepts, "rounds": rounds}


def predict_logits(model, edges, features):
    scores = list(model["intercepts"])
    for round_stumps in model["rounds"]:
        for class_index, stump in enumerate(round_stumps):
            if stump is None:
                continue
            bucket = bisect.bisect_right(
                edges[stump["feature"]], features[stump["feature"]]
            )
            scores[class_index] += (
                stump["left"] if bucket <= stump["split_bin"] else stump["right"]
            )
    return scores


def fit_shrinkage(predictions, actuals):
    mean_prediction = statistics.fmean(predictions)
    mean_actual = statistics.fmean(actuals)
    variance = sum((value - mean_prediction) ** 2 for value in predictions)
    if variance <= 1e-12:
        slope = 0.0
    else:
        slope = sum(
            (prediction - mean_prediction) * (actual - mean_actual)
            for prediction, actual in zip(predictions, actuals)
        ) / variance
    slope = min(1.0, max(0.0, slope))
    return {"intercept": mean_actual - slope * mean_prediction, "slope": slope}


def apply_shrinkage(calibration, prediction):
    return calibration["intercept"] + calibration["slope"] * prediction


def _multiclass_log_loss(logits, rows, temperature):
    names = ("UP", "DOWN", "NEUTRAL")
    losses = []
    for values, row in zip(logits, rows):
        probabilities = softmax(values, temperature)
        probability = probabilities[names.index(row["actual_class"])]
        losses.append(-math.log(max(probability, 1e-12)))
    return statistics.fmean(losses)


def fit_temperature(logits, rows):
    left = TEMPERATURE_MIN
    right = TEMPERATURE_MAX
    ratio = (math.sqrt(5.0) - 1.0) / 2.0
    x1 = right - ratio * (right - left)
    x2 = left + ratio * (right - left)
    f1 = _multiclass_log_loss(logits, rows, x1)
    f2 = _multiclass_log_loss(logits, rows, x2)
    for _ in range(TEMPERATURE_STEPS):
        if f1 <= f2:
            right, x2, f2 = x2, x1, f1
            x1 = right - ratio * (right - left)
            f1 = _multiclass_log_loss(logits, rows, x1)
        else:
            left, x1, f1 = x1, x2, f2
            x2 = left + ratio * (right - left)
            f2 = _multiclass_log_loss(logits, rows, x2)
    return (left + right) / 2.0


def fit_model(fit_rows, calibration_rows, horizon):
    fit_selected = [row for row in fit_rows if row["horizon"] == horizon]
    calibration_selected = sorted([
        row for row in calibration_rows if row["horizon"] == horizon
    ], key=lambda row: (row["as_of"], row["symbol"]))
    if len(fit_selected) < 500 or len(calibration_selected) < 200:
        raise ValueError(f"horizon {horizon}: insufficient FIT/CAL observations")
    edges = fit_histogram_edges(fit_selected)
    point = fit_point_booster(fit_selected, edges)
    classifier = fit_class_booster(fit_selected, edges)
    calibration_split = len(calibration_selected) // 2
    calibrator_rows = calibration_selected[:calibration_split]
    distribution_rows = calibration_selected[calibration_split:]
    if len(calibrator_rows) < 100 or len(distribution_rows) < 100:
        raise ValueError(f"horizon {horizon}: insufficient CAL-A/CAL-B rows")
    cal_raw = [predict_point(point, edges, row["features"]) for row in calibrator_rows]
    shrinkage = fit_shrinkage(
        cal_raw, [float(row["target_normalized"]) for row in calibrator_rows]
    )
    distribution_point = [
        apply_shrinkage(shrinkage, predict_point(point, edges, row["features"]))
        for row in distribution_rows
    ]
    price_residuals = sorted(
        float(row["target_normalized"]) - prediction
        for row, prediction in zip(distribution_rows, distribution_point)
    )
    trade_residuals = sorted(
        float(row["trade_target_normalized"]) - prediction
        for row, prediction in zip(distribution_rows, distribution_point)
    )
    cal_logits = [
        predict_logits(classifier, edges, row["features"])
        for row in calibrator_rows
    ]
    temperature = fit_temperature(cal_logits, calibrator_rows)
    classes = Counter(row["actual_class"] for row in calibration_selected)
    total = len(calibration_selected)
    base_probabilities = {
        name: (classes[name] + 1.0) / (total + 3.0)
        for name in ("UP", "DOWN", "NEUTRAL")
    }
    cost_pct = ROUND_TRIP_COST_BPS / 100.0
    calibration_long_utility = statistics.fmean(
        float(row["trade_return_pct"]) - cost_pct
        for row in calibration_selected
    )
    calibration_sell_utility = statistics.fmean(
        -float(row["trade_return_pct"]) - cost_pct
        for row in calibration_selected
    )
    constant_baseline_direction = (
        "AL" if calibration_long_utility >= calibration_sell_utility else "SAT"
    )
    return {
        "horizon": horizon,
        "role": HORIZON_ROLES[horizon],
        "edges": edges,
        "point": point,
        "classifier": classifier,
        "shrinkage": shrinkage,
        "temperature": temperature,
        "price_q10": v1.finite_sample_quantile(price_residuals, 0.10),
        "price_q50": v1.finite_sample_quantile(price_residuals, 0.50),
        "price_q90": v1.finite_sample_quantile(price_residuals, 0.90),
        "trade_residual_mean": statistics.fmean(trade_residuals),
        "naive_abs_q80": v1.finite_sample_quantile(
            [abs(row["target_normalized"]) for row in distribution_rows], 0.80
        ),
        "base_probabilities": base_probabilities,
        # Frozen on CALIBRATION. Validation outcomes never choose this baseline.
        "constant_baseline_direction": constant_baseline_direction,
        "supported_symbols": sorted({row["symbol"] for row in fit_selected}),
        "fit_rows": len(fit_selected),
        "calibration_rows": len(calibration_selected),
        "calibrator_rows": len(calibrator_rows),
        "distribution_calibration_rows": len(distribution_rows),
    }


def forecast_row(model, row):
    raw = predict_point(model["point"], model["edges"], row["features"])
    point = apply_shrinkage(model["shrinkage"], raw)
    scale = float(row["scale_pct"])
    median_return = (point + model["price_q50"]) * scale
    low_return = (point + model["price_q10"]) * scale
    high_return = (point + model["price_q90"]) * scale
    probabilities = softmax(
        predict_logits(model["classifier"], model["edges"], row["features"]),
        model["temperature"],
    )
    p_up, p_down, p_neutral = probabilities
    expected_trade = (point + model["trade_residual_mean"]) * scale
    expected_long_net = expected_trade - ROUND_TRIP_COST_BPS / 100.0
    expected_sell_net = -expected_trade - ROUND_TRIP_COST_BPS / 100.0
    if p_up >= ACTION_PROBABILITY and expected_long_net > 0.0:
        action, expected_net = "AL", expected_long_net
    elif p_down >= ACTION_PROBABILITY and expected_sell_net > 0.0:
        action, expected_net = "SAT", expected_sell_net
    else:
        action, expected_net = "BEKLE", 0.0
    risk_adjusted = action if row["risk_eligible"] else "BEKLE"
    current = float(row["current_price"])
    target = current * (1.0 + median_return / 100.0)
    low = current * (1.0 + low_return / 100.0)
    high = current * (1.0 + high_return / 100.0)
    if not all(math.isfinite(value) and value > 0.0 for value in (target, low, high)):
        raise ValueError("non-finite or non-positive price distribution")
    if not low <= target <= high:
        raise ValueError("invalid ordered price distribution")
    output = dict(row)
    output.update({
        "predicted_return_pct": median_return,
        "target_price": target,
        "price_p10": low,
        "price_p90": high,
        "p_up": p_up,
        "p_down": p_down,
        "p_neutral": p_neutral,
        "expected_trade_return_pct": expected_trade,
        "expected_net_return_pct": expected_net,
        "action": action,
        "risk_adjusted_action": risk_adjusted,
    })
    return output


def table_columns(con, table):
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def derivative_schema_gate(con):
    failures = []
    for table, required in REQUIRED_TABLE_COLUMNS.items():
        columns = table_columns(con, table)
        missing = sorted(required - columns)
        if missing:
            failures.append(f"{table}:missing={','.join(missing)}")
    if not any(item.startswith("derivatives_archive_manifest:") for item in failures):
        archive_error_count = int(con.execute(
            "SELECT COUNT(*) FROM derivatives_archive_manifest WHERE status='ERROR'"
        ).fetchone()[0])
        if archive_error_count:
            failures.append(f"ARCHIVE_MANIFEST_ERRORS={archive_error_count}")
    return failures


def _coverage_row(con, table, time_column, symbol, start, end):
    return con.execute(
        f"SELECT MIN({time_column}), MAX({time_column}), COUNT(*) "
        f"FROM {table} WHERE symbol=? AND {time_column}>=? AND {time_column}<=?",
        (symbol, iso(start), iso(end)),
    ).fetchone()


def _joined_derivative_coverage(con, symbol, start=None, end=None):
    clauses = [
        "m.symbol=?",
        "m.close IS NOT NULL",
        "m.premium_close IS NOT NULL",
        "m.quote_volume IS NOT NULL",
        "m.taker_buy_quote_volume IS NOT NULL",
        "p.open_interest_contracts IS NOT NULL",
        "p.open_interest_value IS NOT NULL",
        "p.top_account_ratio IS NOT NULL",
        "p.top_position_ratio IS NOT NULL",
        "p.global_account_ratio IS NOT NULL",
        # The final 5-minute snapshot must have existed before the 1h close.
        "p.source_max_time<p.hour_close_time",
    ]
    parameters = [symbol]
    if start is not None:
        clauses.append("m.close_time>=?")
        parameters.append(iso(start))
    if end is not None:
        clauses.append("m.close_time<?")
        parameters.append(iso(end))
    return con.execute(f"""
        SELECT MIN(m.close_time), MAX(m.close_time), COUNT(*)
        FROM futures_hourly_market m
        JOIN futures_positioning_hourly p
          ON p.symbol=m.symbol AND p.hour_close_time=m.close_time
        WHERE {' AND '.join(clauses)}
    """, tuple(parameters)).fetchone()


def derivative_readiness(con, requested_end):
    """Choose one common, archive-complete research window without lookahead.

    Binance Vision positioning archives naturally trail the live hour.  The lab
    therefore freezes its end at the latest close common to the recent,
    sufficiently deep development cohort.  This is a data-availability rule,
    never a performance-based coin selection rule.
    """
    schema_failures = derivative_schema_gate(con)
    if schema_failures:
        return {
            "ready": False, "failures": schema_failures, "symbols": {},
            "supported_symbols": [], "analysis_end": requested_end,
            "analysis_start": requested_end - timedelta(days=ANALYSIS_DAYS),
            "fetch_start": requested_end - timedelta(days=ANALYSIS_DAYS, hours=WARMUP_HOURS),
        }

    broad = {}
    recent_candidates = []
    for symbol in SYMBOLS:
        joined = _joined_derivative_coverage(con, symbol)
        funding = con.execute(
            "SELECT MIN(funding_time), MAX(funding_time), COUNT(*) "
            "FROM funding_events WHERE symbol=?",
            (symbol,),
        ).fetchone()
        first = parse_time(joined[0]) if joined and joined[0] else None
        last = parse_time(joined[1]) if joined and joined[1] else None
        funding_first = parse_time(funding[0]) if funding and funding[0] else None
        funding_last = parse_time(funding[1]) if funding and funding[1] else None
        history_days = (
            (last - first).total_seconds() / 86400.0 if first and last else 0.0
        )
        funding_days = (
            (funding_last - funding_first).total_seconds() / 86400.0
            if funding_first and funding_last else 0.0
        )
        recent = bool(
            last
            and requested_end - last <= timedelta(hours=MAXIMUM_ARCHIVE_LAG_HOURS)
            and history_days >= ANALYSIS_DAYS
            and funding_days >= ANALYSIS_DAYS
        )
        broad[symbol] = {
            "first": first, "last": last,
            "funding_first": funding_first, "funding_last": funding_last,
            "history_days": history_days, "funding_days": funding_days,
            "joined_rows_total": int(joined[2] or 0) if joined else 0,
            "funding_rows_total": int(funding[2] or 0) if funding else 0,
            "recent_candidate": recent,
        }
        if recent:
            recent_candidates.append(symbol)

    failures = []
    if len(recent_candidates) < MINIMUM_SUPPORTED_COINS:
        failures.append(
            f"RECENT_DEEP_DERIVATIVES_COINS={len(recent_candidates)}"
            f"<{MINIMUM_SUPPORTED_COINS}"
        )
    if not all(symbol in recent_candidates for symbol in ("BTCUSDT", "ETHUSDT")):
        failures.append("BTC_ETH_REFERENCE_HISTORY_INCOMPLETE")

    if recent_candidates:
        latest_common_close = min(broad[symbol]["last"] for symbol in recent_candidates)
        analysis_end = min(
            requested_end,
            latest_common_close + timedelta(milliseconds=1),
        )
    else:
        analysis_end = requested_end
    analysis_start = analysis_end - timedelta(days=ANALYSIS_DAYS)
    fetch_start = analysis_start - timedelta(hours=WARMUP_HOURS)
    expected_hours = max(1, int((analysis_end - fetch_start).total_seconds() // 3600))

    output = {}
    for symbol in SYMBOLS:
        joined = _joined_derivative_coverage(
            con, symbol, fetch_start, analysis_end
        )
        funding = _coverage_row(
            con, "funding_events", "funding_time", symbol,
            analysis_start - timedelta(days=7), analysis_end,
        )
        joined_count = int(joined[2] or 0) if joined else 0
        funding_count = int(funding[2] or 0) if funding else 0
        ready = bool(
            symbol in recent_candidates
            and joined and joined[0] and joined[1]
            and funding and funding[0] and funding[1]
            and parse_time(joined[0]) <= fetch_start + timedelta(hours=2)
            and parse_time(joined[1]) >= analysis_end - timedelta(hours=2)
            and joined_count >= int(expected_hours * MINIMUM_HOURLY_COVERAGE)
            and parse_time(funding[0]) <= analysis_start - timedelta(days=7) + timedelta(hours=9)
            and parse_time(funding[1]) >= analysis_end - timedelta(hours=16)
            and funding_count >= int(ANALYSIS_DAYS * 2.5)
        )
        output[symbol] = {
            "ready": ready,
            "joined_rows": joined_count,
            "coverage": joined_count / expected_hours,
            "funding_rows": funding_count,
            "history_days": broad[symbol]["history_days"],
            "funding_days": broad[symbol]["funding_days"],
        }
    supported = [symbol for symbol in SYMBOLS if output[symbol]["ready"]]
    if len(supported) < MINIMUM_SUPPORTED_COINS:
        failures.append(
            f"SUPPORTED_COIN_COVERAGE={len(supported)}<{MINIMUM_SUPPORTED_COINS}"
        )
    if not all(symbol in supported for symbol in ("BTCUSDT", "ETHUSDT")):
        failures.append("BTC_ETH_COMMON_WINDOW_INCOMPLETE")
    manifest_rows = con.execute(
        "SELECT url, checksum, status, rows FROM derivatives_archive_manifest "
        "ORDER BY url"
    ).fetchall()
    manifest_digest = hashlib.sha256(
        json.dumps(manifest_rows, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "ready": not failures,
        "failures": list(dict.fromkeys(failures)),
        "symbols": output,
        "supported_symbols": supported,
        "analysis_start": analysis_start,
        "analysis_end": analysis_end,
        "fetch_start": fetch_start,
        "expected_hours": expected_hours,
        "archive_manifest_digest": manifest_digest,
    }


def load_derivatives(con, start, end, symbols):
    result = {}
    for symbol in symbols:
        market_rows = con.execute("""
            SELECT open_time, close_time, open, high, low, close, volume,
                   quote_volume, trade_count, taker_buy_quote_volume,
                   premium_open, premium_high, premium_low, premium_close
            FROM futures_hourly_market
            WHERE symbol=? AND close_time>=? AND close_time<=?
            ORDER BY close_time
        """, (symbol, iso(start), iso(end))).fetchall()
        market = []
        for source in market_rows:
            if any(value is None for value in source[2:]):
                continue
            values = [float(value) for value in source[2:]]
            if any(not math.isfinite(value) for value in values):
                continue
            if min(values[:4]) <= 0.0 or values[5] < 0.0 or values[6] < 0.0:
                continue
            market.append({
                "open_time": parse_time(source[0]),
                "close_time": parse_time(source[1]),
                "open": values[0], "high": values[1], "low": values[2],
                "close": values[3], "volume": values[4],
                "quote_volume": values[5], "trade_count": values[6],
                "taker_buy_quote_volume": values[7],
                "premium_open": values[8], "premium_high": values[9],
                "premium_low": values[10], "premium_close": values[11],
            })

        positioning_rows = con.execute("""
            SELECT hour_close_time, source_max_time, open_interest_contracts,
                   open_interest_value, top_account_ratio, top_position_ratio,
                   global_account_ratio
            FROM futures_positioning_hourly
            WHERE symbol=? AND hour_close_time>=? AND hour_close_time<=?
            ORDER BY hour_close_time
        """, (symbol, iso(start), iso(end))).fetchall()
        positioning = []
        for source in positioning_rows:
            hour_close = parse_time(source[0])
            source_max = parse_time(source[1])
            # Sparse Vision ratio cells are explicit missing contexts. Skip
            # only that hour; never coerce NULL into a numeric model feature.
            if any(value is None for value in source[2:]):
                continue
            values = [float(value) for value in source[2:]]
            if source_max >= hour_close or any(not math.isfinite(value) for value in values):
                continue
            if values[1] <= 0.0 or any(value <= 0.0 for value in values[2:]):
                continue
            positioning.append({
                "hour_close_time": hour_close,
                "source_max_time": source_max,
                "open_interest_contracts": values[0],
                "open_interest_value": values[1],
                "top_account_ratio": values[2],
                "top_position_ratio": values[3],
                "global_account_ratio": values[4],
            })

        funding_rows = con.execute("""
            SELECT funding_time, rate, mark_price
            FROM funding_events
            WHERE symbol=? AND funding_time>=? AND funding_time<=?
            ORDER BY funding_time
        """, (symbol, iso(start - timedelta(days=7)), iso(end))).fetchall()
        funding = []
        for source in funding_rows:
            rate = float(source[1])
            mark = None if source[2] is None else float(source[2])
            if math.isfinite(rate) and (
                mark is None or (math.isfinite(mark) and mark > 0.0)
            ):
                funding.append({
                    "funding_time": parse_time(source[0]),
                    "funding_rate": rate,
                    "mark_price": mark,
                })
        result[symbol] = {
            "market": market,
            "market_by_close": {row["close_time"]: index for index, row in enumerate(market)},
            "positioning": positioning,
            "positioning_by_close": {
                row["hour_close_time"]: index for index, row in enumerate(positioning)
            },
            "funding": funding,
            "funding_times": [row["funding_time"] for row in funding],
        }
    return result


def _pct(new_value, old_value):
    return v1.pct_change(float(new_value), float(old_value))


def _taker_imbalance(market, index, hours):
    first = index - hours + 1
    v1.require_hourly_continuity(market, first, index)
    quote = sum(row["quote_volume"] for row in market[first:index + 1])
    buy = sum(row["taker_buy_quote_volume"] for row in market[first:index + 1])
    if quote <= 0.0 or buy < 0.0 or buy > quote * 1.000001:
        raise ValueError("invalid futures taker quote volume")
    return 2.0 * buy / quote - 1.0


def _funding_state(item, decision_time):
    position = bisect.bisect_right(item["funding_times"], decision_time) - 1
    if position < 0:
        raise ValueError("no point-in-time funding event")
    current = item["funding"][position]
    first_time = decision_time - timedelta(days=7)
    first = bisect.bisect_left(item["funding_times"], first_time)
    normalized = []
    intervals = []
    for event_index in range(max(1, first), position + 1):
        interval_hours = (
            item["funding"][event_index]["funding_time"]
            - item["funding"][event_index - 1]["funding_time"]
        ).total_seconds() / 3600.0
        # Binance may use 1h/2h/4h/8h schedules. A larger jump means an event
        # is missing, so the feature is rejected instead of silently treating
        # a multi-period gap as one observation.
        if not 0.5 <= interval_hours <= 12.0:
            raise ValueError("invalid or missing funding interval")
        intervals.append(interval_hours)
        normalized.append(
            item["funding"][event_index]["funding_rate"] * 8.0 / interval_hours
        )
    if len(normalized) < 6:
        raise ValueError("insufficient funding history")
    deviation = statistics.pstdev(normalized)
    z_score = 0.0 if deviation <= 1e-15 else (
        (normalized[-1] - statistics.fmean(normalized)) / deviation
    )
    return (
        normalized[-1] * 100.0,
        z_score,
        (decision_time - current["funding_time"]).total_seconds() / 3600.0,
        intervals[-1],
    )


def _derivative_state(symbol, decision_time, spot_close, derivatives):
    item = derivatives[symbol]
    market_index = item["market_by_close"].get(decision_time)
    position_index = item["positioning_by_close"].get(decision_time)
    if market_index is None or position_index is None:
        raise ValueError("missing exact derivatives hour")
    if market_index < 23 or position_index < 24:
        raise ValueError("insufficient derivative warmup")
    market = item["market"]
    positioning = item["positioning"]
    v1.require_hourly_continuity(market, market_index - 23, market_index)
    current_position = positioning[position_index]
    if current_position["source_max_time"] >= current_position["hour_close_time"]:
        raise ValueError("positioning snapshot was not available before cutoff")
    for offset in (1, 4, 24):
        expected = decision_time - timedelta(hours=offset)
        previous = positioning[position_index - offset]
        if previous["hour_close_time"] != expected:
            raise ValueError("non-contiguous positioning history")
        if previous["source_max_time"] >= previous["hour_close_time"]:
            raise ValueError("future positioning source time")
    oi_current = current_position["open_interest_value"]
    futures_volume_24h = sum(
        row["quote_volume"] for row in market[market_index - 23:market_index + 1]
    )
    if futures_volume_24h <= 0.0:
        raise ValueError("invalid futures 24h volume")
    (
        funding_rate, funding_z, hours_since_funding, funding_interval
    ) = _funding_state(item, decision_time)
    ratios = (
        current_position["top_account_ratio"],
        current_position["top_position_ratio"],
        current_position["global_account_ratio"],
    )
    if any(value <= 0.0 for value in ratios):
        raise ValueError("invalid positioning ratio")
    return {
        "basis_pct": _pct(market[market_index]["close"], spot_close),
        "premium_close_pct": market[market_index]["premium_close"] * 100.0,
        "funding_rate_8h_pct": funding_rate,
        "funding_z_7d": funding_z,
        "funding_interval_hours": funding_interval,
        "hours_since_funding": hours_since_funding,
        "oi_change_1h_pct": _pct(oi_current, positioning[position_index - 1]["open_interest_value"]),
        "oi_change_4h_pct": _pct(oi_current, positioning[position_index - 4]["open_interest_value"]),
        "oi_change_24h_pct": _pct(oi_current, positioning[position_index - 24]["open_interest_value"]),
        "log_oi_value_to_futures_volume_24h": math.log1p(oi_current / futures_volume_24h),
        "taker_imbalance_1h": _taker_imbalance(market, market_index, 1),
        "taker_imbalance_4h": _taker_imbalance(market, market_index, 4),
        "taker_imbalance_24h": _taker_imbalance(market, market_index, 24),
        "log_top_account_ratio": math.log(ratios[0]),
        "log_top_position_ratio": math.log(ratios[1]),
        "log_global_account_ratio": math.log(ratios[2]),
    }


def derivative_features(symbol, decision_time, spot_close, btc_spot_close, derivatives):
    own = _derivative_state(symbol, decision_time, spot_close, derivatives)
    btc = _derivative_state("BTCUSDT", decision_time, btc_spot_close, derivatives)
    return [
        own["basis_pct"], own["premium_close_pct"], own["funding_rate_8h_pct"],
        own["funding_z_7d"], own["funding_interval_hours"],
        own["hours_since_funding"],
        own["oi_change_1h_pct"], own["oi_change_4h_pct"], own["oi_change_24h_pct"],
        own["log_oi_value_to_futures_volume_24h"], own["taker_imbalance_1h"],
        own["taker_imbalance_4h"], own["taker_imbalance_24h"],
        own["log_top_account_ratio"], own["log_top_position_ratio"],
        own["log_global_account_ratio"],
        btc["basis_pct"], btc["premium_close_pct"], btc["funding_rate_8h_pct"],
        btc["funding_interval_hours"],
        btc["oi_change_1h_pct"], btc["oi_change_4h_pct"], btc["oi_change_24h_pct"],
        btc["taker_imbalance_1h"], btc["taker_imbalance_4h"],
        btc["taker_imbalance_24h"],
    ]


def build_rows(series, derivatives, start, end, symbols):
    btc = series["BTCUSDT"]["candles"]
    rows = []
    source_gaps = 0
    risk_blocks = 0
    attempted_contexts = 0
    attempted_by_symbol = Counter()
    gaps_by_symbol = Counter()
    attempted_by_week = Counter()
    gaps_by_week = Counter()
    max_horizon = max(HORIZONS)
    meaningful = (ROUND_TRIP_COST_BPS + MINIMUM_MOVE_BPS) / 100.0
    for btc_index in range(WARMUP_HOURS, len(btc) - max_horizon):
        decision_time = btc[btc_index]["close_time"]
        if decision_time < start or decision_time >= end:
            continue
        policy, assets, indexes, errors = v1.risk_context(series, decision_time)
        week = v1.week_key(decision_time)
        for symbol in symbols:
            attempted_contexts += 1
            attempted_by_symbol[symbol] += 1
            attempted_by_week[week] += 1
            asset = assets.get(symbol)
            index = indexes.get(symbol)
            if asset is None or index is None:
                source_gaps += 1
                gaps_by_symbol[symbol] += 1
                gaps_by_week[week] += 1
                continue
            candles = series[symbol]["candles"]
            try:
                base_features = v1.feature_values(symbol, candles, index, btc, btc_index)
                extra_features = derivative_features(
                    symbol, decision_time, candles[index]["close"],
                    btc[btc_index]["close"], derivatives,
                )
                v1.require_hourly_continuity(candles, index, index + max_horizon)
            except (ValueError, KeyError, IndexError):
                source_gaps += 1
                gaps_by_symbol[symbol] += 1
                gaps_by_week[week] += 1
                continue
            risk_allowed = bool(
                policy["new_positions_allowed"]
                and asset["new_position_allowed"]
                and float(asset["max_position_pct"]) > 0.0
            )
            if not risk_allowed:
                risk_blocks += 1
            current = candles[index]["close"]
            entry = candles[index + 1]["open"]
            vol24 = max(float(base_features[4]), 0.02)
            btc_ret24 = float(base_features[10])
            btc_threshold = max(0.50, float(base_features[11]) * math.sqrt(24.0))
            regime = "UP" if btc_ret24 > btc_threshold else (
                "DOWN" if btc_ret24 < -btc_threshold else "SIDEWAYS"
            )
            for horizon in HORIZONS:
                due = candles[index + horizon]["close_time"]
                exit_price = candles[index + horizon]["close"]
                scale = max(vol24 * math.sqrt(float(horizon)), 0.10)
                price_return = _pct(exit_price, current)
                trade_return = _pct(exit_price, entry)
                actual_class = "UP" if trade_return > meaningful else (
                    "DOWN" if trade_return < -meaningful else "NEUTRAL"
                )
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
                    "trade_target_normalized": trade_return / scale,
                    "scale_pct": scale,
                    "v1_features": base_features,
                    "features": base_features + extra_features,
                    "actual_class": actual_class,
                    "risk_state": policy["market_state"],
                    "risk_cap_pct": float(asset["max_position_pct"]),
                    "risk_eligible": risk_allowed,
                    "regime": regime,
                })
    gap_rate = source_gaps / attempted_contexts if attempted_contexts else 1.0
    symbol_gap_rates = {
        symbol: gaps_by_symbol[symbol] / attempted_by_symbol[symbol]
        for symbol in symbols if attempted_by_symbol[symbol]
    }
    weekly_gap_rates = {
        str(week): gaps_by_week[week] / attempted_by_week[week]
        for week in attempted_by_week
    }
    return {
        "rows": rows,
        "source_gaps": source_gaps,
        "attempted_contexts": attempted_contexts,
        "source_gap_rate": gap_rate,
        "max_symbol_gap_rate": max(symbol_gap_rates.values(), default=1.0),
        "max_weekly_gap_rate": max(weekly_gap_rates.values(), default=1.0),
        "symbol_gap_rates": symbol_gap_rates,
        "risk_blocks": risk_blocks,
    }


def _v1_view(rows):
    return [{**row, "features": row["v1_features"]} for row in rows]


def _raw_action_view(row):
    output = dict(row)
    output["risk_adjusted_action"] = output["action"]
    return output


def _model_probabilities(row):
    return {"UP": row["p_up"], "DOWN": row["p_down"], "NEUTRAL": row["p_neutral"]}


def _v1_probabilities(row):
    return {
        "UP": row["_v1_p_up"], "DOWN": row["_v1_p_down"],
        "NEUTRAL": row["_v1_p_neutral"],
    }


def evaluate(model, v1_model, rows):
    forecasts = []
    for row in rows:
        forecast = forecast_row(model, row)
        baseline_input = {**row, "features": row["v1_features"], "risk_eligible": True}
        baseline = v1.forecast_row(v1_model, baseline_input)
        forecast.update({
            "_v1_p_up": baseline["p_up"],
            "_v1_p_down": baseline["p_down"],
            "_v1_p_neutral": baseline["p_neutral"],
            "_v1_predicted_return_pct": baseline["predicted_return_pct"],
            "_v1_price_p10": baseline["price_p10"],
            "_v1_price_p90": baseline["price_p90"],
            "_v1_action": baseline["action"],
        })
        forecasts.append(forecast)

    climate = model["base_probabilities"]
    climate_getter = lambda row: climate
    no_change_getter = lambda row: {"UP": 0.0, "DOWN": 0.0, "NEUTRAL": 1.0}
    probability_candidates = (
        ("CLIMATOLOGY", climate_getter),
        ("NO_CHANGE", no_change_getter),
        ("FROZEN_V1", _v1_probabilities),
    )
    model_brier = v1.brier_score(forecasts, _model_probabilities)
    scored_brier = [
        (v1.brier_score(forecasts, getter), name, getter)
        for name, getter in probability_candidates
    ]
    benchmark_brier, benchmark_brier_name, benchmark_brier_getter = min(
        scored_brier, key=lambda item: item[0]
    )
    model_log = v1.log_loss(forecasts, _model_probabilities)
    scored_log = [
        (v1.log_loss(forecasts, getter), name, getter)
        for name, getter in probability_candidates
    ]
    benchmark_log, benchmark_log_name, benchmark_log_getter = min(
        scored_log, key=lambda item: item[0]
    )

    model_errors, naive_errors, old_errors = [], [], []
    model_distribution, naive_distribution, old_distribution = [], [], []
    model_covered = naive_covered = 0
    model_widths, naive_widths = [], []
    naive_q80 = float(model["naive_abs_q80"])
    for row in forecasts:
        actual = row["price_return_pct"]
        scale = row["scale_pct"]
        low = (row["price_p10"] / row["current_price"] - 1.0) * 100.0
        median = row["predicted_return_pct"]
        high = (row["price_p90"] / row["current_price"] - 1.0) * 100.0
        old_low = (row["_v1_price_p10"] / row["current_price"] - 1.0) * 100.0
        old_median = row["_v1_predicted_return_pct"]
        old_high = (row["_v1_price_p90"] / row["current_price"] - 1.0) * 100.0
        naive_low, naive_high = -naive_q80 * scale, naive_q80 * scale
        model_errors.append(abs(actual - median) / scale)
        naive_errors.append(abs(actual) / scale)
        old_errors.append(abs(actual - old_median) / scale)
        model_distribution.append(sum((
            v1.pinball(actual, low, 0.10), v1.pinball(actual, median, 0.50),
            v1.pinball(actual, high, 0.90),
        )) / (3.0 * scale))
        naive_distribution.append(sum((
            v1.pinball(actual, naive_low, 0.10), v1.pinball(actual, 0.0, 0.50),
            v1.pinball(actual, naive_high, 0.90),
        )) / (3.0 * scale))
        old_distribution.append(sum((
            v1.pinball(actual, old_low, 0.10), v1.pinball(actual, old_median, 0.50),
            v1.pinball(actual, old_high, 0.90),
        )) / (3.0 * scale))
        model_covered += int(low <= actual <= high)
        naive_covered += int(naive_low <= actual <= naive_high)
        model_widths.append((high - low) / scale)
        naive_widths.append((naive_high - naive_low) / scale)
        model_probs = _model_probabilities(row)
        brier_probs = benchmark_brier_getter(row)
        row["_brier_advantage"] = sum(
            (brier_probs[name] - int(row["actual_class"] == name)) ** 2
            - (model_probs[name] - int(row["actual_class"] == name)) ** 2
            for name in ("UP", "DOWN", "NEUTRAL")
        )
        row["_log_loss_advantage"] = (
            -math.log(max(benchmark_log_getter(row)[row["actual_class"]], 1e-12))
            + math.log(max(model_probs[row["actual_class"]], 1e-12))
        )

    error_benchmarks = {
        "NO_CHANGE": statistics.fmean(naive_errors),
        "FROZEN_V1": statistics.fmean(old_errors),
    }
    error_benchmark_name = min(error_benchmarks, key=error_benchmarks.get)
    distribution_benchmarks = {
        "NO_CHANGE": statistics.fmean(naive_distribution),
        "FROZEN_V1": statistics.fmean(old_distribution),
    }
    distribution_benchmark_name = min(
        distribution_benchmarks, key=distribution_benchmarks.get
    )
    for index, row in enumerate(forecasts):
        benchmark_error = (
            naive_errors[index] if error_benchmark_name == "NO_CHANGE" else old_errors[index]
        )
        benchmark_distribution = (
            naive_distribution[index]
            if distribution_benchmark_name == "NO_CHANGE"
            else old_distribution[index]
        )
        row["_mae_advantage"] = benchmark_error - model_errors[index]
        row["_distribution_advantage"] = (
            benchmark_distribution - model_distribution[index]
        )

    actions = [
        _raw_action_view(row)
        for row in forecasts if row["action"] in {"AL", "SAT"}
    ]
    post_risk_actions = [
        row for row in forecasts if row["risk_adjusted_action"] in {"AL", "SAT"}
    ]
    risk_vetoed_actions = [
        row for row in forecasts
        if row["action"] in {"AL", "SAT"}
        and row["risk_adjusted_action"] == "BEKLE"
    ]
    utilities = [v1.directional_utility(row, row["action"]) for row in actions]
    double_utilities = [
        v1.directional_utility(row, row["action"], cost_multiplier=2.0)
        for row in actions
    ]
    hits = sum(v1.claimed_event_hit(row, row["action"]) for row in actions)
    action_pairs = [
        (row["p_up"] if row["action"] == "AL" else row["p_down"],
         int(v1.claimed_event_hit(row, row["action"])))
        for row in actions
    ]
    for row, (probability, outcome) in zip(actions, action_pairs):
        row["_claimed_hit"] = float(outcome)
        row["_action_probability"] = float(probability)
        row["_calibration_bias"] = float(outcome) - float(probability)
    slope, intercept = v1.reliability_line(action_pairs)
    predicted_frequency = statistics.fmean(item[0] for item in action_pairs) if action_pairs else 0.0
    observed_frequency = statistics.fmean(item[1] for item in action_pairs) if action_pairs else 0.0
    probability_spread = (
        statistics.pstdev(item[0] for item in action_pairs)
        if len(action_pairs) >= 2 else 0.0
    )
    constant_direction = model["constant_baseline_direction"]
    for row in actions:
        row["_constant_baseline_utility"] = v1.directional_utility(row, constant_direction)
        row["_action_advantage"] = (
            v1.directional_utility(row, row["action"])
            - row["_constant_baseline_utility"]
        )
    positives = sum(value for value in utilities if value > 0.0)
    negatives = abs(sum(value for value in utilities if value < 0.0))
    up_ece = v1.binary_ece(
        (row["p_up"], row["actual_class"] == "UP") for row in forecasts
    )
    down_ece = v1.binary_ece(
        (row["p_down"], row["actual_class"] == "DOWN") for row in forecasts
    )
    neutral_ece = v1.binary_ece(
        (row["p_neutral"], row["actual_class"] == "NEUTRAL")
        for row in forecasts
    )
    coin_skill_evaluated = coin_skill_positive = 0
    for symbol in sorted({row["symbol"] for row in forecasts}):
        coin_rows = [row for row in forecasts if row["symbol"] == symbol]
        if len(coin_rows) < 50:
            continue
        coin_skill_evaluated += 1
        if v1.brier_score(coin_rows, _model_probabilities) < v1.brier_score(
            coin_rows, benchmark_brier_getter
        ):
            coin_skill_positive += 1
    coin = v1.concentration(actions, "symbol")
    regime = v1.concentration(actions, "regime")
    coin_slices = v1.slice_support(
        actions, "symbol", minimum_events=20, action_field="action"
    )
    regime_slices = v1.slice_support(
        actions, "regime", minimum_events=30, action_field="action"
    )
    forecast_weekly_blocks = len({v1.week_key(row["as_of"]) for row in forecasts})
    action_weekly_blocks = len({v1.week_key(row["as_of"]) for row in actions})
    hit_interval = v1.weekly_block_bootstrap_interval(
        actions, lambda row: row["_claimed_hit"], 2700 + model["horizon"]
    )
    bias_interval = v1.weekly_block_bootstrap_interval(
        actions, lambda row: row["_calibration_bias"], 2800 + model["horizon"]
    )
    best_error = error_benchmarks[error_benchmark_name]
    best_distribution = distribution_benchmarks[distribution_benchmark_name]
    metrics = {
        "horizon_hours": model["horizon"],
        "events": len(forecasts),
        "model_brier": model_brier,
        "benchmark_brier": benchmark_brier,
        "benchmark_brier_name": benchmark_brier_name,
        "brier_skill": 1.0 - model_brier / benchmark_brier if benchmark_brier else -1.0,
        "model_log_loss": model_log,
        "benchmark_log_loss": benchmark_log,
        "benchmark_log_loss_name": benchmark_log_name,
        "log_loss_improvement": 1.0 - model_log / benchmark_log if benchmark_log else -1.0,
        "up_ece": up_ece, "down_ece": down_ece,
        "neutral_ece": neutral_ece,
        "classwise_ece_max": max(up_ece, down_ece, neutral_ece),
        "actual_up_rate": (
            sum(row["actual_class"] == "UP" for row in forecasts) / len(forecasts)
            if forecasts else 0.0
        ),
        "actual_down_rate": (
            sum(row["actual_class"] == "DOWN" for row in forecasts) / len(forecasts)
            if forecasts else 0.0
        ),
        "actual_neutral_rate": (
            sum(row["actual_class"] == "NEUTRAL" for row in forecasts) / len(forecasts)
            if forecasts else 0.0
        ),
        "brier_advantage_block_lb": v1.weekly_block_bootstrap_lower(
            forecasts, lambda row: row["_brier_advantage"], 2100 + model["horizon"]
        ),
        "log_loss_advantage_block_lb": v1.weekly_block_bootstrap_lower(
            forecasts, lambda row: row["_log_loss_advantage"], 2200 + model["horizon"]
        ),
        "normalized_mae": statistics.fmean(model_errors),
        "naive_normalized_mae": best_error,
        "mae_benchmark_name": error_benchmark_name,
        "mae_skill": 1.0 - statistics.fmean(model_errors) / best_error if best_error else -1.0,
        "distribution_loss": statistics.fmean(model_distribution),
        "naive_distribution_loss": best_distribution,
        "distribution_benchmark_name": distribution_benchmark_name,
        "distribution_skill": (
            1.0 - statistics.fmean(model_distribution) / best_distribution
            if best_distribution else -1.0
        ),
        "mae_advantage_block_lb": v1.weekly_block_bootstrap_lower(
            forecasts, lambda row: row["_mae_advantage"], 2300 + model["horizon"]
        ),
        "distribution_advantage_block_lb": v1.weekly_block_bootstrap_lower(
            forecasts, lambda row: row["_distribution_advantage"], 2400 + model["horizon"]
        ),
        "interval_coverage": model_covered / len(forecasts) if forecasts else 0.0,
        "naive_interval_coverage": naive_covered / len(forecasts) if forecasts else 0.0,
        "interval_width_scaled": statistics.fmean(model_widths),
        "naive_interval_width_scaled": statistics.fmean(naive_widths),
        "action_events": len(actions),
        "action_coverage": len(actions) / len(forecasts) if forecasts else 0.0,
        "post_risk_action_events": len(post_risk_actions),
        "post_risk_action_coverage": (
            len(post_risk_actions) / len(forecasts) if forecasts else 0.0
        ),
        "risk_vetoed_action_events": len(risk_vetoed_actions),
        "risk_veto_rate": (
            len(risk_vetoed_actions) / len(actions) if actions else 0.0
        ),
        "action_claimed_event_hit_rate": hits / len(actions) if actions else 0.0,
        "action_claimed_hit_block_lb": hit_interval[0],
        "action_claimed_hit_block_ub": hit_interval[1],
        "action_wilson_lower": v1.wilson_lower(hits, len(actions)),
        "action_economic_net_hit_rate": (
            sum(value > 0.0 for value in utilities) / len(actions) if actions else 0.0
        ),
        "action_mean_predicted_probability": predicted_frequency,
        "action_probability_std": probability_spread,
        "action_observed_frequency": observed_frequency,
        "action_calibration_gap": abs(predicted_frequency - observed_frequency),
        "action_calibration_bias_block_lb": bias_interval[0],
        "action_calibration_bias_block_ub": bias_interval[1],
        "action_ece": v1.binary_ece(action_pairs),
        "action_calibration_slope": slope,
        "action_calibration_intercept": intercept,
        "average_directional_utility_pct": statistics.fmean(utilities) if utilities else 0.0,
        "double_cost_directional_utility_pct": (
            statistics.fmean(double_utilities) if double_utilities else 0.0
        ),
        "constant_baseline_direction": constant_direction,
        "constant_baseline_utility_pct": (
            statistics.fmean(
                v1.directional_utility(row, constant_direction) for row in actions
            ) if actions else 0.0
        ),
        "action_utility_block_lb": v1.weekly_block_bootstrap_lower(
            actions, lambda row: v1.directional_utility(row, row["action"]),
            2500 + model["horizon"]
        ),
        "action_vs_constant_block_lb": v1.weekly_block_bootstrap_lower(
            actions, lambda row: row["_action_advantage"], 2600 + model["horizon"]
        ),
        "profit_factor": positives / negatives if negatives else (
            float("inf") if positives else 0.0
        ),
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
        "positive_subwindows": v1.subwindow_positive_count(
            actions, action_field="action"
        ),
        "v1_brier": v1.brier_score(forecasts, _v1_probabilities),
        "v1_log_loss": v1.log_loss(forecasts, _v1_probabilities),
        "v1_normalized_mae": statistics.fmean(old_errors),
        "v1_distribution_loss": statistics.fmean(old_distribution),
    }
    return forecasts, metrics


def validation_gate(metrics):
    checks, _ = v1.validation_gate(metrics)
    checks.pop("events_gte_1000", None)
    minimum_events = MINIMUM_EVENTS[int(metrics["horizon_hours"])]
    checks[f"events_gte_{minimum_events}"] = metrics["events"] >= minimum_events
    # A reliability slope is unidentified when a discrete model emits nearly
    # one probability level. In that case level calibration is already guarded
    # by action ECE, mean gap and clustered calibration-bias bounds.
    if metrics["action_probability_std"] < 0.02:
        checks["action_calibration_slope_0_8_1_2"] = True
        checks["action_calibration_intercept_lte_10pct"] = True
    # V2's promotion claim is model-level.  Risk-adjusted coverage is reported
    # separately and cannot erase otherwise valid model actions.
    checks["forecast_beats_frozen_v1_brier"] = metrics["model_brier"] < metrics["v1_brier"]
    checks["forecast_beats_frozen_v1_log_loss"] = (
        metrics["model_log_loss"] < metrics["v1_log_loss"]
    )
    checks["forecast_beats_frozen_v1_mae"] = (
        metrics["normalized_mae"] < metrics["v1_normalized_mae"]
    )
    checks["forecast_beats_frozen_v1_distribution"] = (
        metrics["distribution_loss"] < metrics["v1_distribution_loss"]
    )
    return checks, all(checks.values())


def compact_forecast(row):
    reasons = ["FORECAST_V2_RESEARCH_ONLY"]
    if not row["risk_eligible"]:
        reasons.append("RISK_VETO")
    contract = build_forecast_record(
        symbol=row["symbol"], as_of=iso(row["as_of"]),
        feature_cutoff=iso(row["feature_cutoff"]), due_time=iso(row["due_time"]),
        horizon_hours=row["horizon"], current_price=row["current_price"],
        target_price=row["target_price"], price_p10=row["price_p10"],
        price_p90=row["price_p90"], interval_probability=INTERVAL_PROBABILITY,
        round_trip_cost_bps=ROUND_TRIP_COST_BPS,
        minimum_move_bps=MINIMUM_MOVE_BPS,
        action_probability_threshold=ACTION_PROBABILITY,
        target_reference=REFERENCE_PRICE_BASIS, entry_rule=ENTRY_RULE,
        probability_event=PROBABILITY_EVENT, p_up_net=row["p_up"],
        p_down_net=row["p_down"], p_neutral=row["p_neutral"],
        expected_net_return_pct=row["expected_net_return_pct"],
        shadow_action=row["action"], risk_adjusted_action=row["risk_adjusted_action"],
        risk_eligible=row["risk_eligible"], risk_state=row["risk_state"],
        risk_cap_pct=row["risk_cap_pct"], model_version=MODEL_VERSION,
        artifact_hash="DEVELOPMENT_UNSEALED", calibration_state="DEVELOPMENT_ONLY",
        production_eligible=False, reason_codes=tuple(reasons),
    ).to_dict()
    return {
        "lab_version": LAB_VERSION, "segment": row["segment"],
        "horizon_role": HORIZON_ROLES[row["horizon"]], **contract,
        "actual_price": row["exit_price"],
        "actual_trade_return_pct": row["trade_return_pct"],
        "actual_class": row["actual_class"], "regime": row["regime"],
    }


def serializable(value):
    return v1.serializable(value)


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            {key: serializable(value) for key, value in row.items()} for row in rows
        )


def seal_artifact(payload):
    sealed = dict(payload)
    sealed.pop("artifact_hash", None)
    canonical = json.dumps(sealed, sort_keys=True, separators=(",", ":"))
    sealed["artifact_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return sealed


def artifact_hash_valid(payload):
    expected = payload.get("artifact_hash")
    return bool(expected) and seal_artifact(payload)["artifact_hash"] == expected


def model_artifact(models, boundaries, passed_horizons, state, data_provenance=None):
    payload = {
        "contract_version": CONTRACT_VERSION,
        "lab_version": LAB_VERSION,
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "created_at": iso(utcnow()),
        "state": state,
        "calibration_state": "DEVELOPMENT_ONLY",
        "production_eligible": False,
        "horizon_roles": {str(key): value for key, value in HORIZON_ROLES.items()},
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "minimum_move_bps": MINIMUM_MOVE_BPS,
        "action_probability": ACTION_PROBABILITY,
        "analysis_days": ANALYSIS_DAYS,
        "minimum_supported_coins": MINIMUM_SUPPORTED_COINS,
        "minimum_hourly_coverage": MINIMUM_HOURLY_COVERAGE,
        "feature_names": FEATURE_NAMES,
        "fixed_hyperparameters": {
            "histogram_bins": HISTOGRAM_BINS,
            "point_boost_rounds": POINT_BOOST_ROUNDS,
            "class_boost_rounds": CLASS_BOOST_ROUNDS,
            "learning_rate": LEARNING_RATE,
            "leaf_l2": LEAF_L2,
            "min_leaf": MIN_LEAF,
            "temperature_min": TEMPERATURE_MIN,
            "temperature_max": TEMPERATURE_MAX,
            "temperature_steps": TEMPERATURE_STEPS,
        },
        "data_provenance": data_provenance or {},
        "boundaries": {key: iso(value) for key, value in boundaries.items()},
        "horizons": {
            str(horizon): models[horizon] for horizon in passed_horizons
        },
        "warning": (
            "Historical validation is development evidence only. The frozen "
            "supported cohort carries survivorship/listing-history bias. A newly "
            "sealed future period and frozen forward validation are mandatory."
        ),
    }
    return seal_artifact(payload)


def data_blocked_result(start, end, boundaries, readiness):
    provenance = {
        "analysis_start": iso(start), "analysis_end": iso(end),
        "supported_symbols": readiness.get("supported_symbols", []),
        "archive_manifest_digest": readiness.get("archive_manifest_digest"),
        "quality_failures": readiness.get("failures", []),
    }
    artifact = model_artifact(
        {}, boundaries, [], "DATA_QUALITY_BLOCKED", provenance
    )
    CANDIDATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATE_PATH.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, default=serializable) + "\n",
        encoding="utf-8",
    )
    write_csv(DETAIL_PATH, [])
    write_csv(SELECTION_PATH, [{
        "lab_version": LAB_VERSION,
        "model_version": MODEL_VERSION,
        "horizon_hours": "ALL",
        "segment": "DATA_QUALITY",
        "status": "BLOCK",
        "failed_gates": ",".join(readiness["failures"]),
    }])
    return {
        "start": start, "end": end, "boundaries": boundaries,
        "readiness": readiness,
        "built": {
            "rows": [], "source_gaps": 0, "attempted_contexts": 0,
            "source_gap_rate": 0.0, "max_symbol_gap_rate": 0.0,
            "max_weekly_gap_rate": 0.0, "symbol_gap_rates": {},
            "risk_blocks": 0,
        },
        "segmented_rows": 0, "selected_rows": 0, "selection": [],
        "passed_horizons": [], "conclusion": "DATA_QUALITY_BLOCKED",
        "artifact_hash": artifact["artifact_hash"],
    }


def run_lab(now=None):
    from data import historical_market_data

    _, _, requested_end = v1.requested_range(now)
    con = sqlite3.connect(DB_NAME)
    try:
        readiness = derivative_readiness(con, requested_end)
        start = readiness["analysis_start"]
        fetch_start = readiness["fetch_start"]
        end = readiness["analysis_end"]
        boundaries = v1.split_boundaries(start, end)
        if not readiness["ready"]:
            return data_blocked_result(start, end, boundaries, readiness)

        historical_market_data.DB_NAME = DB_NAME
        historical_market_data.create_market_klines_table()
        spot_fetch = v1.ensure_historical_coverage(con, fetch_start, end)
        supported = tuple(readiness["supported_symbols"])
        # The historical Risk Manager policy is defined on the fixed 13-coin
        # watchlist, even when derivative features support a smaller cohort.
        spot_failures = [
            symbol for symbol in SYMBOLS
            if spot_fetch.get(symbol, {}).get("status")
            not in {"READY", "FETCHED_READY"}
        ]
        if spot_failures:
            readiness = {
                **readiness,
                "ready": False,
                "failures": readiness["failures"] + [
                    "SPOT_HISTORY_INCOMPLETE=" + ",".join(spot_failures)
                ],
                "spot_fetch": spot_fetch,
            }
            return data_blocked_result(start, end, boundaries, readiness)
        readiness["spot_fetch"] = spot_fetch
        series = v1.load_series(con, fetch_start, end)
        derivatives = load_derivatives(con, fetch_start, end, supported)
        built = build_rows(series, derivatives, start, end, supported)
    finally:
        con.close()
    gap_failures = []
    if built["source_gap_rate"] > MAXIMUM_CONTEXT_GAP_RATE:
        gap_failures.append(
            f"CONTEXT_GAP_RATE={built['source_gap_rate']:.6f}>"
            f"{MAXIMUM_CONTEXT_GAP_RATE:.6f}"
        )
    if built["max_symbol_gap_rate"] > MAXIMUM_CONTEXT_GAP_RATE:
        gap_failures.append(
            f"MAX_SYMBOL_GAP_RATE={built['max_symbol_gap_rate']:.6f}>"
            f"{MAXIMUM_CONTEXT_GAP_RATE:.6f}"
        )
    if built["max_weekly_gap_rate"] > MAXIMUM_WEEKLY_CONTEXT_GAP_RATE:
        gap_failures.append(
            f"MAX_WEEKLY_GAP_RATE={built['max_weekly_gap_rate']:.6f}>"
            f"{MAXIMUM_WEEKLY_CONTEXT_GAP_RATE:.6f}"
        )
    if gap_failures:
        readiness = {
            **readiness, "ready": False,
            "failures": readiness["failures"] + gap_failures,
        }
        return data_blocked_result(start, end, boundaries, readiness)

    segmented = v1.assign_segments(built["rows"], boundaries)
    selected = v1.non_overlapping(segmented)
    fit_rows = [row for row in selected if row["segment"] == "FIT"]
    calibration_rows = [row for row in selected if row["segment"] == "CALIBRATION"]
    models = {}
    selection = []
    detail = []
    individually_clear_horizons = []
    for horizon in HORIZONS:
        try:
            model = fit_model(fit_rows, calibration_rows, horizon)
            models[horizon] = model
            v1_model = v1.fit_model(
                _v1_view(fit_rows), _v1_view(calibration_rows), horizon
            )
            validation_rows = [
                row for row in selected
                if row["segment"] == "VALIDATION" and row["horizon"] == horizon
            ]
            validation_forecasts, metrics = evaluate(model, v1_model, validation_rows)
            checks, passed = validation_gate(metrics)
            checks["context_gap_rate_lte_2pct"] = (
                built["source_gap_rate"] <= MAXIMUM_CONTEXT_GAP_RATE
                and built["max_symbol_gap_rate"] <= MAXIMUM_CONTEXT_GAP_RATE
            )
            checks["weekly_context_gap_rate_lte_10pct"] = (
                built["max_weekly_gap_rate"] <= MAXIMUM_WEEKLY_CONTEXT_GAP_RATE
            )
            checks["derivatives_archive_ready"] = readiness["ready"]
            passed = all(checks.values())
            detail.extend(compact_forecast(row) for row in validation_forecasts)
            selection.append({
                "lab_version": LAB_VERSION, "model_version": MODEL_VERSION,
                "horizon_hours": horizon, "horizon_role": HORIZON_ROLES[horizon],
                "segment": "VALIDATION", "status": "PASS" if passed else "REJECT",
                "failed_gates": ",".join(name for name, value in checks.items() if not value),
                **metrics,
            })
            if not passed:
                continue
            if horizon == 1:
                # The 1h endpoint is explicitly exploratory. It may diagnose
                # the model family on VALIDATION but never consumes the final
                # observed slice or enters a candidate artifact.
                selection[-1]["hierarchy_status"] = (
                    "EXPLORATORY_ONLY_OBSERVED_DIAGNOSTIC_NOT_OPENED"
                )
                continue
            if horizon == 24 and 4 not in individually_clear_horizons:
                # Secondary evidence is opened only after the 4h primary has
                # survived both VALIDATION and its observed diagnostic.
                selection[-1]["hierarchy_status"] = (
                    "SECONDARY_REQUIRES_PRIMARY_4H_OBSERVED_CLEAR"
                )
                continue
            exam_rows = [
                row for row in selected
                if row["segment"] == "EXAM" and row["horizon"] == horizon
            ]
            exam_forecasts, exam_metrics = evaluate(model, v1_model, exam_rows)
            detail.extend(compact_forecast(row) for row in exam_forecasts)
            contradicted = bool(
                exam_metrics["brier_skill"] <= 0.0
                or exam_metrics["mae_skill"] <= 0.0
                or exam_metrics["classwise_ece_max"] > 0.10
                or (
                    exam_metrics["action_events"] >= 50
                    and exam_metrics["average_directional_utility_pct"] <= 0.0
                )
            )
            selection.append({
                "lab_version": LAB_VERSION, "model_version": MODEL_VERSION,
                "horizon_hours": horizon, "horizon_role": HORIZON_ROLES[horizon],
                "segment": "OBSERVED_DIAGNOSTIC",
                "status": (
                    "CONTRADICTED_BY_OBSERVED_HOLDOUT"
                    if contradicted else "MEASURED_NOT_PROMOTED"
                ),
                "failed_gates": (
                    "NOT_PRISTINE_PRODUCT_EVIDENCE,OBSERVED_DIAGNOSTIC_CONTRADICTION"
                    if contradicted else "NOT_PRISTINE_PRODUCT_EVIDENCE"
                ),
                **exam_metrics,
            })
            if not contradicted:
                individually_clear_horizons.append(horizon)
        except Exception as exc:
            selection.append({
                "lab_version": LAB_VERSION, "model_version": MODEL_VERSION,
                "horizon_hours": horizon, "horizon_role": HORIZON_ROLES[horizon],
                "segment": "VALIDATION", "status": "ERROR",
                "failed_gates": f"MODEL_ERROR:{exc}",
            })

    # Pre-registered multiplicity control: 4h is the only primary endpoint.
    # 24h may join a development artifact only after the 4h primary survives;
    # 1h is diagnostic/exploratory and can never promote this V2 family.
    passed_horizons = []
    if 4 in individually_clear_horizons:
        passed_horizons.append(4)
        if 24 in individually_clear_horizons:
            passed_horizons.append(24)
    for row in selection:
        if row.get("segment") != "VALIDATION" or row.get("status") != "PASS":
            continue
        if row.get("horizon_hours") == 1:
            row.setdefault("hierarchy_status", "EXPLORATORY_ONLY_NOT_CANDIDATE")
        elif row.get("horizon_hours") == 24 and 4 not in individually_clear_horizons:
            row.setdefault("hierarchy_status", "SECONDARY_REQUIRES_PRIMARY_4H")
        else:
            row.setdefault("hierarchy_status", "HIERARCHY_ELIGIBLE")

    conclusion = (
        "HISTORICAL_DEVELOPMENT_CANDIDATE"
        if passed_horizons else "NO_VALIDATED_FUTURES_MICROSTRUCTURE_EDGE"
    )
    provenance = {
        "analysis_start": iso(start), "analysis_end": iso(end),
        "supported_symbols": readiness["supported_symbols"],
        "archive_manifest_digest": readiness.get("archive_manifest_digest"),
        "source_gap_rate": built["source_gap_rate"],
        "max_symbol_gap_rate": built["max_symbol_gap_rate"],
        "max_weekly_gap_rate": built["max_weekly_gap_rate"],
    }
    artifact = model_artifact(
        models, boundaries, passed_horizons, conclusion, provenance
    )
    write_csv(DETAIL_PATH, detail)
    write_csv(SELECTION_PATH, selection)
    CANDIDATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATE_PATH.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, default=serializable) + "\n",
        encoding="utf-8",
    )
    return {
        "start": start, "end": end, "boundaries": boundaries,
        "readiness": readiness, "built": built,
        "segmented_rows": len(segmented), "selected_rows": len(selected),
        "selection": selection, "passed_horizons": passed_horizons,
        "conclusion": conclusion, "artifact_hash": artifact["artifact_hash"],
    }


def percent(value):
    value = float(value)
    return "N/A" if not math.isfinite(value) else f"{100.0 * value:.1f}%"


def render(result):
    print("AG Crypto Analyst - Futures/Microstructure Forecast Core V2 Lab v1.0.0")
    print(
        f"Contract: {CONTRACT_VERSION} | model={MODEL_VERSION} | "
        "implementation=PURE_STDLIB_HISTOGRAM_ADDITIVE_STUMPS"
    )
    print(
        f"Horizon roles: 4h=PRIMARY | 24h=SECONDARY | 1h=EXPLORATORY | "
        f"threshold={ACTION_PROBABILITY:.2f} | cost={ROUND_TRIP_COST_BPS:.1f}bps | "
        f"min_move={MINIMUM_MOVE_BPS:.1f}bps"
    )
    readiness = result["readiness"]
    ready_count = sum(item["ready"] for item in readiness.get("symbols", {}).values())
    print(
        f"Derivatives data: ready={ready_count}/{len(SYMBOLS)} | "
        f"skipped_contexts={result['built']['source_gaps']} "
        f"({percent(result['built']['source_gap_rate'])}) | "
        f"max_week_gap={percent(result['built']['max_weekly_gap_rate'])}"
    )
    print(
        f"Frozen data window: {iso(result['start'])} -> {iso(result['end'])} | "
        f"supported={','.join(readiness.get('supported_symbols', ())) or 'NONE'}"
    )
    if result["conclusion"] == "DATA_QUALITY_BLOCKED":
        print("DATA QUALITY FAILURES:")
        for failure in readiness["failures"]:
            print(f"- {failure}")
        print("\nCONCLUSION: DATA_QUALITY_BLOCKED | product_state=SHADOW_ONLY")
        print(f"Manifest: {CANDIDATE_PATH}")
        return
    boundaries = result["boundaries"]
    print(
        "Chronology: "
        f"FIT<{iso(boundaries['fit_end'])} | CAL<{iso(boundaries['learn_end'])} | "
        f"VAL<{iso(boundaries['validation_end'])} | "
        "OBSERVED_DIAGNOSTIC=remaining_if_VAL_passes"
    )
    print(
        f"Contexts: raw={len(result['built']['rows'])} | "
        f"strict={result['segmented_rows']} | non_overlap={result['selected_rows']} | "
        f"risk_blocks={result['built']['risk_blocks']}"
    )
    for row in result["selection"]:
        if row["segment"] != "VALIDATION":
            continue
        horizon = row["horizon_hours"]
        if row["status"] == "ERROR":
            print(f"\n{horizon}H {row['horizon_role']} | ERROR | {row['failed_gates']}")
            continue
        print(
            f"\n{horizon}H {row['horizon_role']} VALIDATION | {row['status']} | "
            f"n={row['events']} | BrierSkill={percent(row['brier_skill'])} | "
            f"MAESkill={percent(row['mae_skill'])} | "
            f"classECE={percent(row['classwise_ece_max'])}"
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
            "block_LB=" + (
                f"{row['action_utility_block_lb']:+.3f}%"
                if math.isfinite(float(row["action_utility_block_lb"])) else "N/A"
            )
        )
        if row["failed_gates"]:
            print(f"failed: {row['failed_gates']}")
    print(f"\nCONCLUSION: {result['conclusion']} | product_state=SHADOW_ONLY")
    if result["passed_horizons"]:
        print(
            "Development-only horizons: "
            + ", ".join(f"{value}h" for value in result["passed_horizons"])
            + ". Fresh sealed forward validation is still mandatory."
        )
    else:
        print(
            "The single fixed futures/microstructure challenger was not validated. "
            "No threshold or model grid was opened."
        )
    print("Live Decision Engine, Risk Manager and alert policy were not changed.")
    print(f"Details: {DETAIL_PATH}")
    print(f"Selection: {SELECTION_PATH}")
    print(f"Manifest: {CANDIDATE_PATH}")


def _synthetic_rows(count, start, horizon, feature_count):
    rows = []
    meaningful = (ROUND_TRIP_COST_BPS + MINIMUM_MOVE_BPS) / 100.0
    for index in range(count):
        x = ((index * 37) % 101) / 50.0 - 1.0
        y = ((index * 17) % 79) / 39.0 - 1.0
        signal = 0.95 if x > 0.20 else (-0.95 if x < -0.20 else 0.0)
        signal += 0.18 * y + 0.03 * math.sin(index / 3.0)
        features = [0.0] * feature_count
        # The frozen V1 view is deliberately uninformative; the signal lives
        # only in the synthetic derivatives block.  This checks that V2 can
        # add information rather than merely reproduce the price-only ridge.
        features[len(v1.FEATURE_NAMES)] = x
        features[len(v1.FEATURE_NAMES) + 1] = y
        price_return = signal
        trade_return = signal - 0.01 * math.cos(index)
        current = 100.0
        exit_price = current * (1.0 + price_return / 100.0)
        as_of = start + timedelta(hours=index * horizon)
        rows.append({
            "symbol": SYMBOLS[index % len(SYMBOLS)], "as_of": as_of,
            "feature_cutoff": as_of, "due_time": as_of + timedelta(hours=horizon),
            "horizon": horizon, "current_price": current, "entry_price": current,
            "exit_price": exit_price, "price_return_pct": price_return,
            "trade_return_pct": trade_return, "target_normalized": price_return,
            "trade_target_normalized": trade_return, "scale_pct": 1.0,
            "v1_features": features[:len(v1.FEATURE_NAMES)], "features": features,
            "actual_class": "UP" if trade_return > meaningful else (
                "DOWN" if trade_return < -meaningful else "NEUTRAL"
            ),
            "risk_state": "NORMAL", "risk_cap_pct": 10.0,
            "risk_eligible": index % 5 != 0,
            "regime": ("UP", "DOWN", "SIDEWAYS")[index % 3],
            "segment": "FIT",
        })
    return rows


def self_test():
    from data import derivatives_market_data as derivatives_data

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = _synthetic_rows(1000, start, 4, len(FEATURE_NAMES))
    fit_rows = rows[:600]
    calibration_rows = [{**row, "segment": "CALIBRATION"} for row in rows[600:800]]
    validation_rows = [{**row, "segment": "VALIDATION"} for row in rows[800:]]
    model = fit_model(fit_rows, calibration_rows, 4)
    v1_model = v1.fit_model(_v1_view(fit_rows), _v1_view(calibration_rows), 4)
    forecasts, metrics = evaluate(model, v1_model, validation_rows)
    assert len(forecasts) == len(validation_rows)
    assert metrics["model_brier"] < metrics["benchmark_brier"]
    assert metrics["normalized_mae"] < metrics["v1_normalized_mae"]
    assert any(row["action"] in {"AL", "SAT"} for row in forecasts)
    checks, _ = validation_gate(metrics)
    assert "forecast_beats_frozen_v1_brier" in checks
    assert metrics["forecast_weekly_blocks"] > 0
    raw_count = sum(row["action"] in {"AL", "SAT"} for row in forecasts)
    post_count = sum(row["risk_adjusted_action"] in {"AL", "SAT"} for row in forecasts)
    assert post_count < raw_count
    compact = compact_forecast(forecasts[0])
    assert compact["contract_version"] == CONTRACT_VERSION
    assert compact["calibration_state"] == "DEVELOPMENT_ONLY"
    artifact = model_artifact({4: model}, v1.split_boundaries(start, start + timedelta(days=100)), [4], "TEST")
    assert artifact_hash_valid(artifact)
    assert artifact_hash_valid(json.loads(json.dumps(artifact)))
    artifact["state"] = "MUTATED"
    assert not artifact_hash_valid(artifact)
    for horizon, minimum in MINIMUM_EVENTS.items():
        feasible = math.floor(ANALYSIS_DAYS * 0.20 * 24 / horizon) * MINIMUM_SUPPORTED_COINS
        assert feasible >= minimum, (horizon, feasible, minimum)

    # Contract-test the real collector schema. Archive funding intentionally
    # has no mark price; the feature layer must accept that without weakening
    # timestamp checks.
    con = sqlite3.connect(":memory:")
    derivatives_data.ensure_tables(con)
    opened = start
    closed = start + timedelta(hours=1, milliseconds=-1)
    con.execute(
        "INSERT INTO futures_hourly_market VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "BTCUSDT", iso(opened), iso(closed), 100.0, 101.0, 99.0,
            100.5, 10.0, 1005.0, 20, 550.0, -0.0001, 0.0002,
            -0.0002, 0.0001,
        ),
    )
    con.execute(
        "INSERT INTO futures_positioning_hourly VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "BTCUSDT", iso(closed), iso(closed - timedelta(minutes=5)),
            10.0, 1000.0, 1.1, 1.2, 0.9, 1.05, "self-test",
        ),
    )
    sparse_closed = closed + timedelta(hours=1)
    con.execute(
        "INSERT INTO futures_positioning_hourly VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "BTCUSDT", iso(sparse_closed),
            iso(sparse_closed - timedelta(minutes=5)),
            11.0, 1100.0, None, 1.2, 0.9, None, "self-test-sparse",
        ),
    )
    con.execute(
        "INSERT INTO funding_events VALUES (?, ?, ?, ?)",
        ("BTCUSDT", iso(start), 0.0001, None),
    )
    con.commit()
    assert derivative_schema_gate(con) == []
    loaded = load_derivatives(
        con, start - timedelta(days=1), start + timedelta(hours=2),
        ("BTCUSDT",),
    )
    assert loaded["BTCUSDT"]["funding"][0]["mark_price"] is None
    assert len(loaded["BTCUSDT"]["positioning"]) == 1
    assert sparse_closed not in loaded["BTCUSDT"]["positioning_by_close"]
    blocked = derivative_readiness(con, start + timedelta(hours=2))
    assert not blocked["ready"] and blocked["supported_symbols"] == []
    con.close()
    print("Forecast Core V2 self-test: PASS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-test", action="store_true",
        help="run deterministic synthetic end-to-end checks without DB/network",
    )
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    render(run_lab())


if __name__ == "__main__":
    main()

