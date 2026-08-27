"""Point-in-time spot-flow Feature Challenger V1 development lab.

The final EXAM segment is deliberately not read.  All models remain shadow-only
regardless of development results; only newly issued forward predictions can
provide promotion evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import pickle
from datetime import timedelta
from pathlib import Path

from analysis import forecast_core_lab as v1
from analysis import forecast_core_v2_lab as booster
from database.runtime_store import connect as runtime_connect


LAB_VERSION = "SPOT_FLOW_FEATURE_CHALLENGER_LAB_V1"
MODEL_VERSION = "SPOT_FLOW_HISTOGRAM_CHALLENGER_V1_FIXED"
MODEL_PATH = Path("reports/forecast_feature_challenger_model.pkl")
ARTIFACT_PATH = Path("reports/forecast_feature_challenger_candidate.json")
SELECTION_PATH = Path("reports/forecast_feature_challenger_selection.csv")
ANALYSIS_DAYS = 400
MIN_FLOW_COVERAGE = 0.98
FLOW_FEATURE_NAMES = (
    "spot_taker_imbalance_1h", "spot_taker_imbalance_4h",
    "spot_taker_imbalance_24h", "spot_flow_acceleration_4h",
    "spot_flow_price_confirmation_24h", "btc_spot_taker_imbalance_1h",
    "btc_spot_taker_imbalance_4h", "btc_spot_taker_imbalance_24h",
)
FEATURE_NAMES = v1.FEATURE_NAMES + FLOW_FEATURE_NAMES


def _imbalance(candles, index, hours):
    first = index - int(hours) + 1
    v1.require_hourly_continuity(candles, first, index)
    window = candles[first:index + 1]
    quote = sum(float(row["quote_volume"]) for row in window)
    buy = sum(float(row["taker_buy_quote_volume"]) for row in window)
    if not math.isfinite(quote) or not math.isfinite(buy):
        raise ValueError("non-finite spot flow")
    if quote <= 0.0 or buy < 0.0 or buy > quote:
        raise ValueError("invalid spot flow")
    return (2.0 * buy - quote) / quote


def feature_values(candles, index, btc_candles, btc_index):
    own_1 = _imbalance(candles, index, 1)
    own_4 = _imbalance(candles, index, 4)
    own_24 = _imbalance(candles, index, 24)
    previous_4 = _imbalance(candles, index - 4, 4)
    price_24 = v1.pct_change(candles[index]["close"], candles[index - 24]["close"])
    confirmation = own_24 * (1.0 if price_24 > 0 else (-1.0 if price_24 < 0 else 0.0))
    return [
        own_1, own_4, own_24, own_4 - previous_4, confirmation,
        _imbalance(btc_candles, btc_index, 1),
        _imbalance(btc_candles, btc_index, 4),
        _imbalance(btc_candles, btc_index, 24),
    ]


def flow_coverage(con, start, end):
    result = {}
    for symbol in v1.SYMBOLS:
        total, complete = con.execute("""
            SELECT COUNT(*), SUM(
                taker_buy_quote_volume IS NOT NULL AND quote_volume IS NOT NULL
                AND quote_volume>0 AND taker_buy_quote_volume>=0
                AND taker_buy_quote_volume<=quote_volume
            ) FROM market_klines
            WHERE symbol=? AND interval='1h' AND close_time>=? AND close_time<=?
        """, (symbol, v1.iso(start), v1.iso(end))).fetchone()
        ratio = int(complete or 0) / int(total or 1)
        result[symbol] = {"total": int(total), "complete": int(complete or 0), "ratio": ratio}
    return result


def build_rows(series, start, end):
    built = v1.build_rows(series, start, end)
    btc = series["BTCUSDT"]
    rows, gaps = [], 0
    meaningful = (v1.ROUND_TRIP_COST_BPS + v1.MINIMUM_MOVE_BPS) / 100.0
    for row in built["rows"]:
        item = series[row["symbol"]]
        index = item["by_close"].get(row["as_of"])
        btc_index = btc["by_close"].get(row["as_of"])
        try:
            extra = feature_values(item["candles"], index, btc["candles"], btc_index)
        except (TypeError, ValueError, IndexError):
            gaps += 1
            continue
        trade_return = float(row["trade_return_pct"])
        rows.append({
            **row,
            "v1_features": list(row["features"]),
            "features": list(row["features"]) + extra,
            "trade_target_normalized": trade_return / float(row["scale_pct"]),
            "actual_class": "UP" if trade_return > meaningful else (
                "DOWN" if trade_return < -meaningful else "NEUTRAL"
            ),
        })
    return {**built, "rows": rows, "flow_feature_gaps": gaps}


def _seal(payload):
    unsigned = dict(payload)
    unsigned.pop("artifact_hash", None)
    raw = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), default=v1.serializable)
    return {**unsigned, "artifact_hash": hashlib.sha256(raw.encode()).hexdigest()}


def _save_model(models, now, boundaries, selection):
    payload = {
        "lab_version": LAB_VERSION,
        "model_version": MODEL_VERSION,
        "created_at": v1.iso(now),
        "feature_names": FEATURE_NAMES,
        "models": models,
        "boundaries": {key: v1.iso(value) for key, value in boundaries.items()},
        "development_selection": selection,
        "production_eligible": False,
        "action_authority": False,
    }
    unsigned = pickle.dumps(payload, protocol=4)
    payload["model_hash"] = hashlib.sha256(unsigned).hexdigest()
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_bytes(pickle.dumps(payload, protocol=4))
    return payload


def run_lab(now=None):
    now = now or v1.utcnow()
    end = now.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=ANALYSIS_DAYS)
    fetch_start = start - timedelta(hours=v1.WARMUP_HOURS)
    boundaries = v1.split_boundaries(start, end)
    con = runtime_connect(v1.DB_NAME)
    try:
        coverage = flow_coverage(con, fetch_start, end)
        failures = [symbol for symbol, score in coverage.items() if score["ratio"] < MIN_FLOW_COVERAGE]
        if failures:
            raise RuntimeError("SPOT_FLOW_COVERAGE_BLOCKED:" + ",".join(failures))
        series = v1.load_series(con, fetch_start, end)
        built = build_rows(series, start, end)
    finally:
        con.close()
    segmented = v1.assign_segments(built["rows"], boundaries)
    selected = v1.non_overlapping(segmented)
    fit = [row for row in selected if row["segment"] == "FIT"]
    calibration = [row for row in selected if row["segment"] == "CALIBRATION"]
    models, selection = {}, []
    for horizon in (1, 4, 24):
        model = booster.fit_model(fit, calibration, horizon)
        models[horizon] = model
        incumbent = v1.fit_model(
            [{**row, "features": row["v1_features"]} for row in fit],
            [{**row, "features": row["v1_features"]} for row in calibration],
            horizon,
        )
        validation = [
            row for row in selected
            if row["segment"] == "VALIDATION" and row["horizon"] == horizon
        ]
        _, metrics = booster.evaluate(model, incumbent, validation)
        checks, passed = booster.validation_gate(metrics)
        selection.append({
            "horizon_hours": horizon,
            "status": "DEVELOPMENT_PASS" if passed else "DEVELOPMENT_REJECT",
            "failed_gates": ",".join(name for name, ok in checks.items() if not ok),
            **metrics,
        })
    payload = _save_model(models, now, boundaries, selection)
    artifact = _seal({
        "lab_version": LAB_VERSION, "model_version": MODEL_VERSION,
        "state": "FROZEN_FORWARD_SHADOW",
        "production_eligible": False, "action_authority": False,
        "feature_names": FEATURE_NAMES, "model_hash": payload["model_hash"],
        "boundaries": payload["boundaries"], "flow_coverage": coverage,
        "flow_feature_gaps": built["flow_feature_gaps"],
        "development_selection": selection,
        "sealed_exam_opened": False,
        "warning": "Development metrics cannot authorize PAPER allocation or real orders.",
    })
    ARTIFACT_PATH.write_text(json.dumps(artifact, indent=2, default=v1.serializable) + "\n")
    booster.write_csv(SELECTION_PATH, selection)
    return artifact


def live_row(base_row, series):
    item = series[base_row["symbol"]]
    btc = series["BTCUSDT"]
    index = item["by_close"][base_row["as_of"]]
    btc_index = btc["by_close"][base_row["as_of"]]
    return {
        **base_row,
        "v1_features": list(base_row["features"]),
        "features": list(base_row["features"]) + feature_values(
            item["candles"], index, btc["candles"], btc_index
        ),
    }


def load_model():
    payload = pickle.loads(MODEL_PATH.read_bytes())
    expected = payload.get("model_hash")
    unsigned = dict(payload)
    unsigned.pop("model_hash", None)
    if hashlib.sha256(pickle.dumps(unsigned, protocol=4)).hexdigest() != expected:
        raise RuntimeError("FEATURE_CHALLENGER_MODEL_HASH_INVALID")
    return payload


if __name__ == "__main__":
    print(json.dumps(run_lab(), indent=2, default=v1.serializable))
