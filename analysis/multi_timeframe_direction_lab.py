"""Fixed six-block comparison for the top-down direction feature layer."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import timedelta
from pathlib import Path

from analysis import forecast_core_lab as core


VERSION = "MULTI_TIMEFRAME_DIRECTION_LAB_V1_FIXED"
REPORT_PATH = Path("reports/multi_timeframe_direction_comparison.json")
BLOCK_DAYS = 30
BLOCK_COUNT = 6
TRAINING_DAYS = 120
HALF_LIFE_DAYS = 45.0
RANDOM_STATE = 20260829
CLASSES = ("UP", "DOWN", "NEUTRAL")


def _features(row, include_multi_timeframe):
    values = row["features"][:12] + row["technical_features"]
    if include_multi_timeframe:
        values += row["multi_timeframe_features"]
    return values


def _label(row):
    meaningful = 0.15
    value = float(row["trade_return_pct"])
    return "UP" if value > meaningful else ("DOWN" if value < -meaningful else "NEUTRAL")


def _fit(rows, cutoff, include_multi_timeframe):
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier

    selected = [
        row for row in rows
        if int(row["horizon"]) == 1
        and cutoff - timedelta(days=TRAINING_DAYS) <= row["as_of"]
        and row["due_time"] < cutoff
    ]
    matrix = np.asarray([
        _features(row, include_multi_timeframe) for row in selected
    ], dtype=float)
    labels = np.asarray([_label(row) for row in selected])
    ages = np.asarray([
        (cutoff - row["as_of"]).total_seconds() / 86400.0
        for row in selected
    ])
    weights = np.exp(-math.log(2.0) * ages / HALF_LIFE_DAYS)
    model = HistGradientBoostingClassifier(
        max_iter=100, max_leaf_nodes=15, max_depth=3,
        min_samples_leaf=100, l2_regularization=2.0,
        learning_rate=0.05, random_state=RANDOM_STATE,
    )
    model.fit(matrix, labels, sample_weight=weights)
    return model, len(selected)


def _score(model, rows, include_multi_timeframe):
    import numpy as np

    matrix = np.asarray([
        _features(row, include_multi_timeframe) for row in rows
    ], dtype=float)
    raw = model.predict_proba(matrix)
    classes = tuple(str(value) for value in model.classes_)
    hits = []
    briers = []
    for row, values in zip(rows, raw):
        probabilities = {
            name: float(values[classes.index(name)]) for name in CLASSES
        }
        actual = _label(row)
        hits.append(int(max(probabilities, key=probabilities.get) == actual))
        briers.append(sum(
            (probabilities[name] - (1.0 if name == actual else 0.0)) ** 2
            for name in CLASSES
        ))
    return {
        "events": len(rows),
        "accuracy": statistics.fmean(hits),
        "brier": statistics.fmean(briers),
    }


def compare(rows, end):
    blocks = []
    for offset in reversed(range(BLOCK_COUNT)):
        start = end - timedelta(days=(offset + 1) * BLOCK_DAYS)
        stop = start + timedelta(days=BLOCK_DAYS)
        test = [
            row for row in rows
            if int(row["horizon"]) == 1
            and start <= row["as_of"] < stop
            and row["due_time"] < stop
        ]
        old_model, training_rows = _fit(rows, start, False)
        new_model, _ = _fit(rows, start, True)
        blocks.append({
            "start": core.iso(start), "end": core.iso(stop),
            "training_rows": training_rows,
            "advanced_1h_only": _score(old_model, test, False),
            "top_down_multi_timeframe": _score(new_model, test, True),
        })
    old_accuracy = statistics.fmean(
        block["advanced_1h_only"]["accuracy"] for block in blocks
    )
    new_accuracy = statistics.fmean(
        block["top_down_multi_timeframe"]["accuracy"] for block in blocks
    )
    old_brier = statistics.fmean(
        block["advanced_1h_only"]["brier"] for block in blocks
    )
    new_brier = statistics.fmean(
        block["top_down_multi_timeframe"]["brier"] for block in blocks
    )
    accuracy_wins = sum(
        block["top_down_multi_timeframe"]["accuracy"]
        > block["advanced_1h_only"]["accuracy"]
        for block in blocks
    )
    brier_wins = sum(
        block["top_down_multi_timeframe"]["brier"]
        < block["advanced_1h_only"]["brier"]
        for block in blocks
    )
    selected = bool(
        new_accuracy > old_accuracy and new_brier < old_brier
        and accuracy_wins >= 4 and brier_wins >= 4
    )
    return {
        "version": VERSION,
        "claim": "HISTORICAL_DEVELOPMENT_EVIDENCE_NOT_FORWARD_PROOF",
        "blocks": blocks,
        "summary": {
            "advanced_1h_only_accuracy": old_accuracy,
            "top_down_multi_timeframe_accuracy": new_accuracy,
            "advanced_1h_only_brier": old_brier,
            "top_down_multi_timeframe_brier": new_brier,
            "accuracy_block_wins": accuracy_wins,
            "brier_block_wins": brier_wins,
            "selected_for_forward_shadow": selected,
            "production_eligible": False,
            "action_authority": False,
        },
    }


def run(database=core.DB_NAME):
    now = core.utcnow()
    start, fetch_start, end = core.requested_range(now)
    con = core.sqlite3.connect(database)
    try:
        series = core.load_series(con, fetch_start, end)
        built = core.build_rows(series, start, end)
    finally:
        con.close()
    from analysis import multi_timeframe_features
    for symbol, item in series.items():
        indexes = item["by_close"]
        candles = item["candles"]
        for row in built["rows"]:
            if row["symbol"] == symbol and "multi_timeframe_features" not in row:
                row["multi_timeframe_features"] = multi_timeframe_features.vector(
                    candles, indexes[row["as_of"]]
                )
    report = compare(built["rows"], end)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=core.DB_NAME)
    args = parser.parse_args()
    print(json.dumps(run(args.database)["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
