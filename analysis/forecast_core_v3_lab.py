"""Frozen interaction-capable Forecast Core V3 development gate.

V3 changes only the model layer.  It reuses the audited Forecast Contract,
point-in-time V2 feature provider, baselines, costs, Risk veto and validation
metrics.  The post-2026-05-28 sealed period is not queried or evaluated here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pickle
import sqlite3
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analysis import forecast_core_lab as v1
from analysis import forecast_core_v2_lab as v2


LAB_VERSION = "FORECAST_CORE_V3_DEV_LAB_V1_0_PRE_REGISTERED"
MODEL_VERSION = "SKLEARN_HIST_GBDT_INTERACTION_V3_FIXED"
MODEL_FAMILY = "HIST_GRADIENT_BOOSTING_DEPTH_4_INTERACTIONS"
PRIMARY_HORIZON = 4
FROZEN_START = datetime(2025, 7, 12, tzinfo=timezone.utc)
FROZEN_FIT_END = datetime(2026, 1, 8, tzinfo=timezone.utc)
FROZEN_CAL_END = datetime(2026, 3, 9, tzinfo=timezone.utc)
SEALED_START = datetime(2026, 5, 28, tzinfo=timezone.utc)
SEALED_END = datetime(2026, 8, 16, tzinfo=timezone.utc)

# One fixed model, not a validation-driven grid.
MAX_ITER = 180
LEARNING_RATE = 0.05
MAX_LEAF_NODES = 15
MAX_DEPTH = 4
MIN_SAMPLES_LEAF = 80
L2_REGULARIZATION = 1.0
RANDOM_STATE = 20260816
REQUIRED_SKLEARN_VERSION = "1.8.0"

SELECTION_PATH = Path("reports/forecast_core_v3_development_selection.csv")
CANDIDATE_PATH = Path("reports/forecast_core_v3_development_candidate.json")
MODEL_PATH = Path("reports/forecast_core_v3_development_model.pkl")


def require_runtime():
    try:
        import numpy as np
        import sklearn
        from sklearn.ensemble import (
            HistGradientBoostingClassifier,
            HistGradientBoostingRegressor,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Forecast Core V3 requires scikit-learn and numpy"
        ) from exc
    if sklearn.__version__ != REQUIRED_SKLEARN_VERSION:
        raise RuntimeError(
            f"Forecast Core V3 requires scikit-learn=={REQUIRED_SKLEARN_VERSION}; "
            f"found {sklearn.__version__}"
        )
    return np, sklearn, HistGradientBoostingClassifier, HistGradientBoostingRegressor


def boundaries():
    return {
        "start": FROZEN_START,
        "fit_end": FROZEN_FIT_END,
        "learn_end": FROZEN_CAL_END,
        "validation_end": SEALED_START,
        "end": SEALED_END,
    }


def _matrix(rows, np):
    values = np.asarray([row["features"] for row in rows], dtype=float)
    if values.ndim != 2 or values.shape[1] != len(v2.FEATURE_NAMES):
        raise ValueError("invalid V3 feature matrix")
    if not np.isfinite(values).all():
        raise ValueError("non-finite V3 feature matrix")
    return values


def _regressor(Regressor, loss="squared_error"):
    return Regressor(
        loss=loss,
        learning_rate=LEARNING_RATE,
        max_iter=MAX_ITER,
        max_leaf_nodes=MAX_LEAF_NODES,
        max_depth=MAX_DEPTH,
        min_samples_leaf=MIN_SAMPLES_LEAF,
        l2_regularization=L2_REGULARIZATION,
        early_stopping=False,
        random_state=RANDOM_STATE,
    )


def _classifier(Classifier):
    return Classifier(
        loss="log_loss",
        learning_rate=LEARNING_RATE,
        max_iter=MAX_ITER,
        max_leaf_nodes=MAX_LEAF_NODES,
        max_depth=MAX_DEPTH,
        min_samples_leaf=MIN_SAMPLES_LEAF,
        l2_regularization=L2_REGULARIZATION,
        early_stopping=False,
        random_state=RANDOM_STATE,
    )


def fit_model(fit_rows, calibration_rows, horizon=PRIMARY_HORIZON):
    np, sklearn, Classifier, Regressor = require_runtime()
    fit = [row for row in fit_rows if row["horizon"] == horizon]
    calibration = sorted(
        [row for row in calibration_rows if row["horizon"] == horizon],
        key=lambda row: (row["as_of"], row["symbol"]),
    )
    if len(fit) < 1000 or len(calibration) < 400:
        raise ValueError("insufficient V3 FIT/CAL observations")
    split = len(calibration) // 2
    cal_a, cal_b = calibration[:split], calibration[split:]
    x_fit = _matrix(fit, np)
    point = _regressor(Regressor)
    point.fit(x_fit, np.asarray([row["target_normalized"] for row in fit]))
    classifier = _classifier(Classifier)
    classifier.fit(x_fit, np.asarray([row["actual_class"] for row in fit]))

    raw_a = point.predict(_matrix(cal_a, np)).tolist()
    shrinkage = v2.fit_shrinkage(
        raw_a, [float(row["target_normalized"]) for row in cal_a]
    )
    raw_b = point.predict(_matrix(cal_b, np)).tolist()
    calibrated_b = [v2.apply_shrinkage(shrinkage, value) for value in raw_b]
    price_residuals = sorted(
        float(row["target_normalized"]) - prediction
        for row, prediction in zip(cal_b, calibrated_b)
    )
    trade_residuals = sorted(
        float(row["trade_target_normalized"]) - prediction
        for row, prediction in zip(cal_b, calibrated_b)
    )
    probabilities_a = classifier.predict_proba(_matrix(cal_a, np))
    logits_a = [
        [math.log(max(float(value), 1e-12)) for value in probability]
        for probability in probabilities_a
    ]
    class_order = [str(value) for value in classifier.classes_]
    ordered_logits = [
        [values[class_order.index(name)] for name in ("UP", "DOWN", "NEUTRAL")]
        for values in logits_a
    ]
    temperature = v2.fit_temperature(ordered_logits, cal_a)
    counts = Counter(row["actual_class"] for row in calibration)
    base_probabilities = {
        name: (counts[name] + 1.0) / (len(calibration) + 3.0)
        for name in ("UP", "DOWN", "NEUTRAL")
    }
    cost = v2.ROUND_TRIP_COST_BPS / 100.0
    long_utility = statistics.fmean(row["trade_return_pct"] - cost for row in calibration)
    sell_utility = statistics.fmean(-row["trade_return_pct"] - cost for row in calibration)
    return {
        "horizon": horizon,
        "point": point,
        "classifier": classifier,
        "class_order": class_order,
        "shrinkage": shrinkage,
        "temperature": temperature,
        "price_q10": v1.finite_sample_quantile(price_residuals, 0.10),
        "price_q50": v1.finite_sample_quantile(price_residuals, 0.50),
        "price_q90": v1.finite_sample_quantile(price_residuals, 0.90),
        "trade_residual_mean": statistics.fmean(trade_residuals),
        "naive_abs_q80": v1.finite_sample_quantile(
            [abs(row["target_normalized"]) for row in cal_b], 0.80
        ),
        "base_probabilities": base_probabilities,
        "constant_baseline_direction": "AL" if long_utility >= sell_utility else "SAT",
        "fit_rows": len(fit),
        "calibration_rows": len(calibration),
        "runtime_version": sklearn.__version__,
    }


def forecast_row(model, row):
    np, _, _, _ = require_runtime()
    x = _matrix([row], np)
    raw_point = float(model["point"].predict(x)[0])
    point = v2.apply_shrinkage(model["shrinkage"], raw_point)
    raw_probs = model["classifier"].predict_proba(x)[0]
    logits = [
        math.log(max(float(raw_probs[model["class_order"].index(name)]), 1e-12))
        for name in ("UP", "DOWN", "NEUTRAL")
    ]
    p_up, p_down, p_neutral = v2.softmax(logits, model["temperature"])
    scale = float(row["scale_pct"])
    median_return = (point + model["price_q50"]) * scale
    low_return = (point + model["price_q10"]) * scale
    high_return = (point + model["price_q90"]) * scale
    expected_trade = (point + model["trade_residual_mean"]) * scale
    expected_long = expected_trade - v2.ROUND_TRIP_COST_BPS / 100.0
    expected_sell = -expected_trade - v2.ROUND_TRIP_COST_BPS / 100.0
    if p_up >= v2.ACTION_PROBABILITY and expected_long > 0.0:
        action, expected_net = "AL", expected_long
    elif p_down >= v2.ACTION_PROBABILITY and expected_sell > 0.0:
        action, expected_net = "SAT", expected_sell
    else:
        action, expected_net = "BEKLE", 0.0
    current = float(row["current_price"])
    prices = [
        current * (1.0 + value / 100.0)
        for value in (low_return, median_return, high_return)
    ]
    if not all(math.isfinite(value) and value > 0.0 for value in prices):
        raise ValueError("invalid V3 price distribution")
    if not prices[0] <= prices[1] <= prices[2]:
        raise ValueError("unordered V3 price distribution")
    return {
        **row,
        "predicted_return_pct": median_return,
        "target_price": prices[1], "price_p10": prices[0], "price_p90": prices[2],
        "p_up": p_up, "p_down": p_down, "p_neutral": p_neutral,
        "expected_trade_return_pct": expected_trade,
        "expected_net_return_pct": expected_net,
        "action": action,
        "risk_adjusted_action": action if row["risk_eligible"] else "BEKLE",
    }


def _artifact(state, metrics, model_sha256=None, runtime_version=None):
    payload = {
        "contract_version": v2.CONTRACT_VERSION,
        "lab_version": LAB_VERSION,
        "model_family": MODEL_FAMILY,
        "model_version": MODEL_VERSION,
        "state": state,
        "production_eligible": False,
        "primary_horizon": PRIMARY_HORIZON,
        "frozen_development_window": [v2.iso(FROZEN_START), v2.iso(SEALED_START)],
        "sealed_window": [v2.iso(SEALED_START), v2.iso(SEALED_END)],
        "sealed_window_opened": False,
        "fixed_hyperparameters": {
            "max_iter": MAX_ITER, "learning_rate": LEARNING_RATE,
            "max_leaf_nodes": MAX_LEAF_NODES, "max_depth": MAX_DEPTH,
            "min_samples_leaf": MIN_SAMPLES_LEAF,
            "l2_regularization": L2_REGULARIZATION,
            "early_stopping": False, "random_state": RANDOM_STATE,
        },
        "runtime_version": runtime_version,
        "development_metrics": metrics,
        "model_sha256": model_sha256,
        "warning": "Development evidence only; sealed evaluation and fresh forward proof are mandatory.",
    }
    return v2.seal_artifact(payload)


def run_lab():
    from data import historical_market_data

    con = sqlite3.connect(v2.DB_NAME)
    try:
        readiness = v2.derivative_readiness(con, SEALED_END)
        if not readiness["ready"]:
            raise RuntimeError("V3 derivatives readiness failed: " + ",".join(readiness["failures"]))
        supported = tuple(readiness["supported_symbols"])
        fetch_start = FROZEN_START - timedelta(hours=v2.WARMUP_HOURS)
        historical_market_data.DB_NAME = v2.DB_NAME
        historical_market_data.create_market_klines_table()
        spot = v1.ensure_historical_coverage(con, fetch_start, SEALED_START)
        failures = [
            symbol for symbol in v2.SYMBOLS
            if spot.get(symbol, {}).get("status") not in {"READY", "FETCHED_READY"}
        ]
        if failures:
            raise RuntimeError("V3 spot history incomplete: " + ",".join(failures))
        series = v1.load_series(con, fetch_start, SEALED_START)
        derivatives = v2.load_derivatives(con, fetch_start, SEALED_START, supported)
        built = v2.build_rows(series, derivatives, FROZEN_START, SEALED_START, supported)
    finally:
        con.close()
    if built["source_gap_rate"] > v2.MAXIMUM_CONTEXT_GAP_RATE:
        raise RuntimeError("V3 global context gap gate failed")
    if built["max_symbol_gap_rate"] > v2.MAXIMUM_CONTEXT_GAP_RATE:
        raise RuntimeError("V3 symbol context gap gate failed")
    if built["max_weekly_gap_rate"] > v2.MAXIMUM_WEEKLY_CONTEXT_GAP_RATE:
        raise RuntimeError("V3 weekly context gap gate failed")

    segmented = v1.assign_segments(built["rows"], boundaries())
    selected = v1.non_overlapping(segmented)
    fit_rows = [row for row in selected if row["segment"] == "FIT"]
    calibration_rows = [row for row in selected if row["segment"] == "CALIBRATION"]
    development_rows = [
        row for row in selected
        if row["segment"] == "VALIDATION" and row["horizon"] == PRIMARY_HORIZON
    ]
    model = fit_model(fit_rows, calibration_rows)
    v1_model = v1.fit_model(
        v2._v1_view(fit_rows), v2._v1_view(calibration_rows), PRIMARY_HORIZON
    )
    _, metrics = v2.evaluate(model, v1_model, development_rows, forecast_row)
    frozen_v2_model = v2.fit_model(fit_rows, calibration_rows, PRIMARY_HORIZON)
    _, frozen_v2_metrics = v2.evaluate(
        frozen_v2_model, v1_model, development_rows
    )
    checks, passed = v2.validation_gate(metrics)
    checks.update({
        "forecast_beats_frozen_v2_brier": (
            metrics["model_brier"] < frozen_v2_metrics["model_brier"]
        ),
        "forecast_beats_frozen_v2_log_loss": (
            metrics["model_log_loss"] < frozen_v2_metrics["model_log_loss"]
        ),
        "forecast_beats_frozen_v2_mae": (
            metrics["normalized_mae"] < frozen_v2_metrics["normalized_mae"]
        ),
        "forecast_beats_frozen_v2_distribution": (
            metrics["distribution_loss"] < frozen_v2_metrics["distribution_loss"]
        ),
    })
    passed = all(checks.values())
    metrics.update({
        "frozen_v2_brier": frozen_v2_metrics["model_brier"],
        "frozen_v2_log_loss": frozen_v2_metrics["model_log_loss"],
        "frozen_v2_normalized_mae": frozen_v2_metrics["normalized_mae"],
        "frozen_v2_distribution_loss": frozen_v2_metrics["distribution_loss"],
    })
    failed = [name for name, value in checks.items() if not value]
    state = "FROZEN_FOR_SEALED_EVALUATION" if passed else "NO_DEVELOPMENT_EDGE"
    model_sha = None
    if passed:
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL)
        MODEL_PATH.write_bytes(payload)
        model_sha = hashlib.sha256(payload).hexdigest()
    artifact = _artifact(
        state, {**metrics, "failed_gates": failed}, model_sha,
        model.get("runtime_version"),
    )
    CANDIDATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATE_PATH.write_text(json.dumps(artifact, indent=2, default=v2.serializable) + "\n")
    v2.write_csv(SELECTION_PATH, [{
        "lab_version": LAB_VERSION, "model_version": MODEL_VERSION,
        "segment": "DEVELOPMENT_GATE", "status": "PASS" if passed else "REJECT",
        "sealed_window_opened": False, "failed_gates": ",".join(failed), **metrics,
    }])
    return {
        "passed": passed, "state": state, "metrics": metrics,
        "failed": failed, "built": built, "supported": supported,
        "artifact_hash": artifact["artifact_hash"],
    }


def render(result):
    metrics = result["metrics"]
    print("AG Crypto Analyst - Interaction Forecast Core V3 Development Lab v1.0.0")
    print(f"Model: {MODEL_VERSION} | horizon=4h PRIMARY | sealed_window_opened=NO")
    print(
        f"Data: supported={len(result['supported'])}/13 | "
        f"skipped={result['built']['source_gaps']} "
        f"({100.0 * result['built']['source_gap_rate']:.3f}%) | "
        f"max_week_gap={100.0 * result['built']['max_weekly_gap_rate']:.2f}%"
    )
    print(
        f"DEVELOPMENT GATE | {'PASS' if result['passed'] else 'REJECT'} | "
        f"n={metrics['events']} | BrierSkill={100.0 * metrics['brier_skill']:.1f}% | "
        f"MAESkill={100.0 * metrics['mae_skill']:.1f}% | "
        f"classECE={100.0 * metrics['classwise_ece_max']:.1f}%"
    )
    print(
        f"actions={metrics['action_events']} ({100.0 * metrics['action_coverage']:.1f}%) | "
        f"hit={100.0 * metrics['action_claimed_event_hit_rate']:.1f}% | "
        f"utility={metrics['average_directional_utility_pct']:+.3f}%"
    )
    if result["failed"]:
        print("failed: " + ",".join(result["failed"]))
    print(f"CONCLUSION: {result['state']} | product_state=SHADOW_ONLY")
    print("The 2026-05-28..2026-08-16 sealed window was not read or evaluated.")
    print(f"Selection: {SELECTION_PATH}")
    print(f"Manifest: {CANDIDATE_PATH}")


def self_test():
    require_runtime()
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = v2._synthetic_rows(1800, start, 4, len(v2.FEATURE_NAMES))
    fit = rows[:1100]
    calibration = [{**row, "segment": "CALIBRATION"} for row in rows[1100:1600]]
    validation = [{**row, "segment": "VALIDATION"} for row in rows[1600:]]
    model = fit_model(fit, calibration)
    forecasts = [forecast_row(model, row) for row in validation]
    assert len(forecasts) == len(validation)
    assert all(abs(row["p_up"] + row["p_down"] + row["p_neutral"] - 1.0) < 1e-9 for row in forecasts)
    artifact = _artifact("TEST", {"ok": True}, "0" * 64, model["runtime_version"])
    assert v2.artifact_hash_valid(artifact)
    assert v2.artifact_hash_valid(json.loads(json.dumps(artifact)))
    assert SEALED_START < SEALED_END
    print("Forecast Core V3 self-test: PASS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        render(run_lab())


if __name__ == "__main__":
    main()
