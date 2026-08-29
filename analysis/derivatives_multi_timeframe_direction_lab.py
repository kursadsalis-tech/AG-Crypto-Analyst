"""Fixed 4h test of derivatives added to causal multi-timeframe context."""
from __future__ import annotations

import json
import math
import statistics
from datetime import timedelta
from pathlib import Path

from analysis import forecast_core_lab as v1
from analysis import forecast_core_v2_lab as v2
from analysis import multi_timeframe_features as mtf
from database.runtime_store import connect


VERSION = "DERIVATIVES_MULTI_TIMEFRAME_4H_V1_FIXED"
REPORT_PATH = Path("reports/derivatives_multi_timeframe_direction.json")
HORIZON = 4
BLOCK_DAYS = 30
BLOCK_COUNT = 6
TRAINING_DAYS = 120
HALF_LIFE_DAYS = 45.0
RANDOM_STATE = 20260829
CLASSES = ("UP", "DOWN", "NEUTRAL")


def _label(row):
    value = float(row["trade_return_pct"])
    return "UP" if value > 0.15 else ("DOWN" if value < -0.15 else "NEUTRAL")


def _features(row, include_derivatives):
    base = row["features"] if include_derivatives else row["v1_features"]
    return base + row["technical_features"] + row["multi_timeframe_features"]


def _non_overlapping(rows):
    selected, next_allowed = [], {}
    for row in sorted(rows, key=lambda item: (item["as_of"], item["symbol"])):
        if row["as_of"] < next_allowed.get(row["symbol"], row["as_of"]):
            continue
        selected.append(row)
        next_allowed[row["symbol"]] = row["due_time"]
    return selected


def _fit(rows, cutoff, include_derivatives):
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier

    selected = _non_overlapping([
        row for row in rows
        if cutoff - timedelta(days=TRAINING_DAYS) <= row["as_of"]
        and row["due_time"] < cutoff
    ])
    matrix = np.asarray([_features(row, include_derivatives) for row in selected])
    labels = np.asarray([_label(row) for row in selected])
    ages = np.asarray([(cutoff - row["as_of"]).total_seconds() / 86400 for row in selected])
    weights = np.exp(-math.log(2) * ages / HALF_LIFE_DAYS)
    model = HistGradientBoostingClassifier(
        max_iter=100, max_leaf_nodes=15, max_depth=3,
        min_samples_leaf=100, l2_regularization=2.0,
        learning_rate=0.05, random_state=RANDOM_STATE,
    )
    model.fit(matrix, labels, sample_weight=weights)
    return model, len(selected)


def _score(model, rows, include_derivatives):
    import numpy as np

    raw_rows = model.predict_proba(np.asarray([
        _features(row, include_derivatives) for row in rows
    ]))
    names = tuple(str(value) for value in model.classes_)
    hits, briers, utilities = [], [], []
    for row, raw in zip(rows, raw_rows):
        probabilities = {
            name: float(raw[names.index(name)]) if name in names else 0.0
            for name in CLASSES
        }
        actual = _label(row)
        predicted = max(probabilities, key=probabilities.get)
        hits.append(predicted == actual)
        briers.append(sum(
            (probabilities[name] - (name == actual)) ** 2 for name in CLASSES
        ))
        if predicted != "NEUTRAL":
            utilities.append((
                float(row["trade_return_pct"])
                if predicted == "UP" else -float(row["trade_return_pct"])
            ) - 0.10)
    return {
        "events": len(rows),
        "accuracy": statistics.fmean(hits),
        "brier": statistics.fmean(briers),
        "directional_events": len(utilities),
        "directional_net_utility_pct": statistics.fmean(utilities) if utilities else 0.0,
    }


def compare(rows, end):
    blocks = []
    for offset in reversed(range(BLOCK_COUNT)):
        start = end - timedelta(days=(offset + 1) * BLOCK_DAYS)
        stop = start + timedelta(days=BLOCK_DAYS)
        test = _non_overlapping([
            row for row in rows if start <= row["as_of"] < stop and row["due_time"] < stop
        ])
        baseline, training_rows = _fit(rows, start, False)
        combined, _ = _fit(rows, start, True)
        blocks.append({
            "start": v1.iso(start), "end": v1.iso(stop),
            "training_rows": training_rows,
            "price_technical_multi_timeframe": _score(baseline, test, False),
            "with_derivatives": _score(combined, test, True),
        })
    def mean(name, metric):
        return statistics.fmean(block[name][metric] for block in blocks)
    base_accuracy, new_accuracy = mean("price_technical_multi_timeframe", "accuracy"), mean("with_derivatives", "accuracy")
    base_brier, new_brier = mean("price_technical_multi_timeframe", "brier"), mean("with_derivatives", "brier")
    base_utility = mean("price_technical_multi_timeframe", "directional_net_utility_pct")
    new_utility = mean("with_derivatives", "directional_net_utility_pct")
    accuracy_wins = sum(b["with_derivatives"]["accuracy"] > b["price_technical_multi_timeframe"]["accuracy"] for b in blocks)
    brier_wins = sum(b["with_derivatives"]["brier"] < b["price_technical_multi_timeframe"]["brier"] for b in blocks)
    utility_wins = sum(b["with_derivatives"]["directional_net_utility_pct"] > b["price_technical_multi_timeframe"]["directional_net_utility_pct"] for b in blocks)
    selected = bool(
        new_accuracy > base_accuracy and new_brier < base_brier
        and new_utility > 0.0 and new_utility > base_utility
        and min(accuracy_wins, brier_wins, utility_wins) >= 4
    )
    return {
        "version": VERSION,
        "claim": "HISTORICAL_DEVELOPMENT_EVIDENCE_NOT_FORWARD_PROOF",
        "blocks": blocks,
        "summary": {
            "baseline_accuracy": base_accuracy, "with_derivatives_accuracy": new_accuracy,
            "baseline_brier": base_brier, "with_derivatives_brier": new_brier,
            "baseline_net_utility_pct": base_utility, "with_derivatives_net_utility_pct": new_utility,
            "accuracy_block_wins": accuracy_wins, "brier_block_wins": brier_wins,
            "utility_block_wins": utility_wins,
            "selected_for_forward_shadow": selected,
            "production_eligible": False, "action_authority": False,
        },
    }


def run(database=v1.DB_NAME):
    con = connect(database)
    try:
        latest = con.execute("SELECT MAX(hour_close_time) FROM futures_positioning_hourly").fetchone()[0]
        requested_end = v1.parse_time(latest) + timedelta(milliseconds=1)
        readiness = v2.derivative_readiness(con, requested_end)
        if not readiness["ready"]:
            raise RuntimeError("derivatives not ready: " + ", ".join(readiness["failures"]))
        start, end = readiness["analysis_start"], readiness["analysis_end"]
        spot_start = start - timedelta(days=155)
        series = v1.load_series(con, spot_start, end)
        symbols = tuple(readiness["supported_symbols"])
        derivatives = v2.load_derivatives(con, readiness["fetch_start"], end, symbols)
        built = v2.build_rows(series, derivatives, start, end, symbols)
    finally:
        con.close()
    rows = []
    btc = series["BTCUSDT"]
    for row in built["rows"]:
        if int(row["horizon"]) != HORIZON:
            continue
        item = series[row["symbol"]]
        index = item["by_close"].get(row["as_of"])
        btc_index = btc["by_close"].get(row["as_of"])
        if index is None or btc_index is None:
            continue
        try:
            row["technical_features"] = __import__(
                "analysis.advanced_technical_features", fromlist=["vector"]
            ).vector(item["candles"], index)
            row["multi_timeframe_features"] = mtf.vector(item["candles"], index)
        except ValueError:
            continue
        rows.append(row)
    report = compare(rows, end)
    report["data"] = {
        "start": v1.iso(start), "end": v1.iso(end),
        "symbols": list(symbols), "eligible_4h_rows": len(rows),
        "source_gap_rate": built["source_gap_rate"],
        "archive_manifest_digest": readiness["archive_manifest_digest"],
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


if __name__ == "__main__":
    result = run()
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
