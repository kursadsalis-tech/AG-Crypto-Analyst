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
HORIZONS = (1, 4, 24)


def _features(row, include_multi_timeframe):
    values = row["features"][:12] + row["technical_features"]
    if include_multi_timeframe:
        values += row["multi_timeframe_features"]
    return values


def _label(row):
    meaningful = 0.15
    value = float(row["trade_return_pct"])
    return "UP" if value > meaningful else ("DOWN" if value < -meaningful else "NEUTRAL")


def _non_overlapping(rows, horizon):
    selected = []
    next_allowed = {}
    for row in sorted(rows, key=lambda item: (item["as_of"], item["symbol"])):
        if row["as_of"] < next_allowed.get(row["symbol"], row["as_of"]):
            continue
        selected.append(row)
        next_allowed[row["symbol"]] = row["due_time"]
    return selected


def _fit(rows, cutoff, horizon, include_multi_timeframe):
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier

    selected = [
        row for row in rows
        if int(row["horizon"]) == int(horizon)
        and cutoff - timedelta(days=TRAINING_DAYS) <= row["as_of"]
        and row["due_time"] < cutoff
    ]
    selected = _non_overlapping(selected, horizon)
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


def _hierarchy_score(model, rows):
    import numpy as np
    from analysis import hierarchical_market_context as hierarchy

    matrix = np.asarray([_features(row, False) for row in rows], dtype=float)
    raw = model.predict_proba(matrix)
    classes = tuple(str(value) for value in model.classes_)
    baseline = []
    selected = []
    tier_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "BLOCK": 0}
    regime_counts = {}
    for row, values in zip(rows, raw):
        probabilities = {
            name: float(values[classes.index(name)]) for name in CLASSES
        }
        predicted = max(probabilities, key=probabilities.get)
        if predicted == "NEUTRAL":
            continue
        actual = _label(row)
        hit = int(predicted == actual)
        utility = (
            float(row["trade_return_pct"])
            if predicted == "UP" else -float(row["trade_return_pct"])
        ) - 0.10
        assessment = hierarchy.assess_forecast(
            row["hierarchical_context"], predicted
        )
        tier = assessment["tier"]
        regime = assessment.get("regime", row["hierarchical_context"].get("regime"))
        tier_counts[tier] += 1
        regime_counts[regime] = regime_counts.get(regime, 0) + 1
        item = (hit, utility)
        baseline.append(item)
        if tier in {"HIGH", "MEDIUM"}:
            selected.append(item)

    def metrics(selected):
        return {
            "events": len(selected),
            "accuracy": statistics.fmean(item[0] for item in selected) if selected else 0.0,
            "net_utility_pct": statistics.fmean(item[1] for item in selected) if selected else 0.0,
        }

    return {
        "all_directional": metrics(baseline),
        "hierarchy_confirmed": metrics(selected),
        "coverage": len(selected) / len(baseline) if baseline else 0.0,
        "tier_counts": tier_counts,
        "regime_counts": regime_counts,
        "positive_utility_capture": (
            sum(max(0.0, item[1]) for item in selected)
            / max(sum(max(0.0, item[1]) for item in baseline), 1e-12)
        ),
    }


def _compare_horizon(rows, end, horizon):
    blocks = []
    for offset in reversed(range(BLOCK_COUNT)):
        start = end - timedelta(days=(offset + 1) * BLOCK_DAYS)
        stop = start + timedelta(days=BLOCK_DAYS)
        test = [
            row for row in rows
            if int(row["horizon"]) == int(horizon)
            and start <= row["as_of"] < stop
            and row["due_time"] < stop
        ]
        test = _non_overlapping(test, horizon)
        old_model, training_rows = _fit(rows, start, horizon, False)
        new_model, _ = _fit(rows, start, horizon, True)
        blocks.append({
            "start": core.iso(start), "end": core.iso(stop),
            "training_rows": training_rows,
            "advanced_1h_only": _score(old_model, test, False),
            "top_down_multi_timeframe": _score(new_model, test, True),
            "hierarchical_confirmation": _hierarchy_score(old_model, test),
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
    hierarchy_events = sum(
        block["hierarchical_confirmation"]["hierarchy_confirmed"]["events"]
        for block in blocks
    )
    directional_events = sum(
        block["hierarchical_confirmation"]["all_directional"]["events"]
        for block in blocks
    )
    hierarchy_accuracy = sum(
        block["hierarchical_confirmation"]["hierarchy_confirmed"]["accuracy"]
        * block["hierarchical_confirmation"]["hierarchy_confirmed"]["events"]
        for block in blocks
    ) / hierarchy_events if hierarchy_events else 0.0
    baseline_accuracy = sum(
        block["hierarchical_confirmation"]["all_directional"]["accuracy"]
        * block["hierarchical_confirmation"]["all_directional"]["events"]
        for block in blocks
    ) / directional_events if directional_events else 0.0
    hierarchy_utility = sum(
        block["hierarchical_confirmation"]["hierarchy_confirmed"]["net_utility_pct"]
        * block["hierarchical_confirmation"]["hierarchy_confirmed"]["events"]
        for block in blocks
    ) / hierarchy_events if hierarchy_events else 0.0
    baseline_utility = sum(
        block["hierarchical_confirmation"]["all_directional"]["net_utility_pct"]
        * block["hierarchical_confirmation"]["all_directional"]["events"]
        for block in blocks
    ) / directional_events if directional_events else 0.0
    hierarchy_accuracy_wins = sum(
        block["hierarchical_confirmation"]["hierarchy_confirmed"]["accuracy"]
        > block["hierarchical_confirmation"]["all_directional"]["accuracy"]
        for block in blocks
    )
    hierarchy_utility_wins = sum(
        block["hierarchical_confirmation"]["hierarchy_confirmed"]["net_utility_pct"]
        > block["hierarchical_confirmation"]["all_directional"]["net_utility_pct"]
        for block in blocks
    )
    hierarchy_selected = bool(
        hierarchy_events >= 100
        and hierarchy_events / max(directional_events, 1) >= 0.05
        and hierarchy_accuracy > baseline_accuracy
        and hierarchy_utility > baseline_utility
        and hierarchy_accuracy_wins >= 4
        and hierarchy_utility_wins >= 4
    )
    return {
        "blocks": blocks,
        "summary": {
            "advanced_1h_only_accuracy": old_accuracy,
            "top_down_multi_timeframe_accuracy": new_accuracy,
            "advanced_1h_only_brier": old_brier,
            "top_down_multi_timeframe_brier": new_brier,
            "accuracy_block_wins": accuracy_wins,
            "brier_block_wins": brier_wins,
            "selected_for_forward_shadow": selected,
            "hierarchy_directional_events": directional_events,
            "hierarchy_confirmed_events": hierarchy_events,
            "hierarchy_coverage": hierarchy_events / max(directional_events, 1),
            "hierarchy_baseline_accuracy": baseline_accuracy,
            "hierarchy_confirmed_accuracy": hierarchy_accuracy,
            "hierarchy_baseline_net_utility_pct": baseline_utility,
            "hierarchy_confirmed_net_utility_pct": hierarchy_utility,
            "hierarchy_accuracy_block_wins": hierarchy_accuracy_wins,
            "hierarchy_utility_block_wins": hierarchy_utility_wins,
            "hierarchy_selected_for_forward_shadow": hierarchy_selected,
            "production_eligible": False,
            "action_authority": False,
        },
    }


def compare(rows, end):
    return {
        "version": VERSION,
        "claim": "HISTORICAL_DEVELOPMENT_EVIDENCE_NOT_FORWARD_PROOF",
        "horizons": {
            str(horizon): _compare_horizon(rows, end, horizon)
            for horizon in HORIZONS
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
    from analysis import hierarchical_market_context
    invalid_ids = set()
    btc_candles = series["BTCUSDT"]["candles"]
    btc_indexes = series["BTCUSDT"]["by_close"]
    for symbol, item in series.items():
        indexes = item["by_close"]
        candles = item["candles"]
        for row in built["rows"]:
            if row["symbol"] == symbol and "multi_timeframe_features" not in row:
                try:
                    row["multi_timeframe_features"] = multi_timeframe_features.vector(
                        candles, indexes[row["as_of"]]
                    )
                    row["hierarchical_context"] = hierarchical_market_context.build(
                        candles, indexes[row["as_of"]],
                        btc_candles, btc_indexes[row["as_of"]],
                        bool(row["risk_eligible"]),
                    )
                except ValueError:
                    invalid_ids.add(id(row))
    eligible = [
        row for row in built["rows"]
        if id(row) not in invalid_ids and "multi_timeframe_features" in row
    ]
    report = compare(eligible, end)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=core.DB_NAME)
    args = parser.parse_args()
    report = run(args.database)
    print(json.dumps({
        horizon: result["summary"]
        for horizon, result in report["horizons"].items()
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
