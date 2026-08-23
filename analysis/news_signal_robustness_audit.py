"""Cost-aware, chronological robustness audit for News Signal outcomes.

This module is research-only.  It does not alter Decision Engine policy and it
never enables live orders.  Score bands are diagnostics, not tuned selectors.
"""

import json
import statistics
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from database.runtime_store import connect as runtime_connect

from analysis import walk_forward_backtest as walk_forward
from analysis.news_signal_backtest import resolve_backtest_spot_symbols


DB_NAME = "crypto_data.db"
OUTPUT_PATH = Path("reports/news_signal_robustness_audit.json")
FEE_BPS = 10.0
SLIPPAGE_BPS = 5.0
TOTAL_COST_PCT = (FEE_BPS + SLIPPAGE_BPS) / 100.0
TRAIN_SHARE = 0.70
MIN_VALIDATION_ROWS = 30
MIN_REGIME_ROWS = 10
MIN_DIRECTION_ROWS = 10
REGIME_THRESHOLD_PCT = 1.0


def chronological_split(rows, train_share=TRAIN_SHARE):
    times = sorted({row["news_time"] for row in rows})
    if len(times) < 2:
        return list(rows), []
    split_index = max(1, min(len(times) - 1, int(len(times) * train_share)))
    split_time = times[split_index]
    return (
        [row for row in rows if row["news_time"] < split_time],
        [row for row in rows if row["news_time"] >= split_time],
    )


def raw_return(row):
    value = float(row["directional_return"])
    return value if row["direction"] == "positive" else -value


def cost_adjust(rows, total_cost_pct=TOTAL_COST_PCT):
    adjusted = []
    for row in rows:
        item = dict(row)
        gross = float(row["directional_return"])
        market = raw_return(row)
        item["gross_directional_return_pct"] = gross
        item["net_directional_return_pct"] = gross - total_cost_pct
        item["always_long_net_return_pct"] = market - total_cost_pct
        item["net_hit"] = int(item["net_directional_return_pct"] > 0.0)
        adjusted.append(item)
    return adjusted


def btc_regime(connection, entry_time):
    rows = connection.execute(
        """
        SELECT close
        FROM market_klines
        WHERE symbol = 'BTCUSDT'
          AND interval = '1h'
          AND close_time <= ?
        ORDER BY close_time DESC
        LIMIT 25
        """,
        (entry_time.isoformat(),),
    ).fetchall()
    if len(rows) < 25:
        return "UNKNOWN"
    latest = float(rows[0][0])
    oldest = float(rows[-1][0])
    if oldest <= 0:
        return "UNKNOWN"
    trailing_return = (latest / oldest - 1.0) * 100.0
    if trailing_return > REGIME_THRESHOLD_PCT:
        return "UP"
    if trailing_return < -REGIME_THRESHOLD_PCT:
        return "DOWN"
    return "SIDEWAYS"


def add_point_in_time_regimes(connection, rows):
    cache = {}
    enriched = []
    for row in rows:
        entry_time = row["entry_time"]
        if entry_time not in cache:
            cache[entry_time] = btc_regime(connection, entry_time)
        item = dict(row)
        item["regime"] = cache[entry_time]
        enriched.append(item)
    return enriched


def metrics(rows):
    if not rows:
        return {
            "n": 0,
            "net_hit_rate_pct": None,
            "gross_directional_return_pct": None,
            "net_directional_return_pct": None,
            "always_long_net_return_pct": None,
            "net_edge_vs_always_long_pct": None,
        }
    gross = statistics.fmean(row["gross_directional_return_pct"] for row in rows)
    net = statistics.fmean(row["net_directional_return_pct"] for row in rows)
    baseline = statistics.fmean(row["always_long_net_return_pct"] for row in rows)
    return {
        "n": len(rows),
        "net_hit_rate_pct": 100.0 * statistics.fmean(row["net_hit"] for row in rows),
        "gross_directional_return_pct": gross,
        "net_directional_return_pct": net,
        "always_long_net_return_pct": baseline,
        "net_edge_vs_always_long_pct": net - baseline,
    }


def score_band(value):
    value = float(value)
    if value < 50:
        return "0-49"
    if value < 70:
        return "50-69"
    if value < 85:
        return "70-84"
    return "85-100"


def slice_metrics(rows, field, transform=None):
    grouped = defaultdict(list)
    for row in rows:
        value = transform(row[field]) if transform else row[field]
        grouped[str(value)].append(row)
    return {key: metrics(value) for key, value in sorted(grouped.items())}


def horizon_report(train_rows, validation_rows, horizon):
    train = [row for row in train_rows if row["horizon"] == horizon]
    validation = [row for row in validation_rows if row["horizon"] == horizon]
    validation_metrics = metrics(validation)
    regime_metrics = slice_metrics(validation, "regime")
    direction_metrics = slice_metrics(validation, "direction")
    supported_regimes = [
        regime
        for regime, result in regime_metrics.items()
        if regime != "UNKNOWN" and result["n"] >= MIN_REGIME_ROWS
    ]
    supported_directions = [
        direction
        for direction in ("positive", "negative")
        if direction_metrics.get(direction, {}).get("n", 0) >= MIN_DIRECTION_ROWS
    ]
    train_metrics = metrics(train)
    reasons = []
    if validation_metrics["n"] < MIN_VALIDATION_ROWS:
        reasons.append("VALIDATION_SAMPLE_TOO_SMALL")
    if len(supported_directions) < 2:
        reasons.append("DIRECTION_IMBALANCE")
    if len(supported_regimes) < 2:
        reasons.append("REGIME_COVERAGE_INSUFFICIENT")
    for label, result in (("TRAIN", train_metrics), ("VALIDATION", validation_metrics)):
        if (result["net_directional_return_pct"] is None
                or result["net_directional_return_pct"] <= 0.0):
            reasons.append(f"{label}_NET_RETURN_NONPOSITIVE")
        if (result["net_edge_vs_always_long_pct"] is None
                or result["net_edge_vs_always_long_pct"] <= 0.0):
            reasons.append(f"{label}_EDGE_VS_ALWAYS_LONG_NONPOSITIVE")
    eligible = not reasons
    return {
        "train": train_metrics,
        "validation": validation_metrics,
        "validation_by_direction": direction_metrics,
        "validation_by_regime": regime_metrics,
        "validation_by_impact_score": slice_metrics(validation, "impact", score_band),
        "validation_by_confidence_score": slice_metrics(validation, "confidence", score_band),
        "supported_regimes": supported_regimes,
        "supported_directions": supported_directions,
        "rejection_reasons": reasons,
        "production_eligible": False,
        "paper_signal_supported": eligible,
        "decision": "PAPER_ONLY_OBSERVE" if eligible else "REJECT_NO_STABLE_NET_EDGE",
    }


def entry_delay_metrics(rows):
    unique_pairs = {
        (row["news_time"], row["entry_time"])
        for row in rows
    }
    delays = [
        (entry_time - news_time).total_seconds() / 60.0
        for news_time, entry_time in unique_pairs
    ]
    if not delays:
        return {"n": 0}
    ordered = sorted(delays)
    p90_index = min(len(ordered) - 1, int(0.90 * len(ordered)))
    return {
        "n": len(delays),
        "mean_minutes": statistics.fmean(delays),
        "median_minutes": statistics.median(delays),
        "p90_minutes": ordered[p90_index],
        "max_minutes": max(delays),
        "over_30m_share_pct": 100.0 * sum(value > 30.0 for value in delays) / len(delays),
    }


def build_report(rows):
    adjusted = cost_adjust(rows)
    train, validation = chronological_split(adjusted)
    return {
        "scope": "PAPER_ONLY",
        "real_orders_allowed": False,
        "method": "chronological_70_30_holdout_no_threshold_tuning",
        "costs": {
            "fee_bps_round_trip": FEE_BPS,
            "slippage_bps_round_trip": SLIPPAGE_BPS,
            "total_bps_round_trip": FEE_BPS + SLIPPAGE_BPS,
        },
        "leakage_controls": {
            "entry": "first_1h_open_at_or_after_news",
            "exit": "first_completed_1h_close_at_or_after_horizon",
            "regime": "BTC_24h_return_using_only_closes_known_at_entry",
            "score_bands": "diagnostic_only_not_selected",
        },
        "entry_delay": entry_delay_metrics(rows),
        "train_rows": len(train),
        "validation_rows": len(validation),
        "horizons": {
            horizon: horizon_report(train, validation, horizon)
            for horizon in walk_forward.HORIZONS
        },
    }


def main():
    connection = runtime_connect(DB_NAME)
    try:
        symbols, symbol_rule = resolve_backtest_spot_symbols(connection)
        evaluated = []
        for row in walk_forward.load_rows(connection):
            evaluated.extend(walk_forward.evaluate_row(connection, row, symbols))
        evaluated = add_point_in_time_regimes(connection, evaluated)
        report = build_report(evaluated)
        report["symbol_rule"] = symbol_rule
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

        print("AG Crypto Analyst - News Signal Robustness Audit")
        print("Scope: PAPER_ONLY | real_orders_allowed=False")
        print(
            f"Chronological split: train={report['train_rows']} | "
            f"validation={report['validation_rows']} | cost=15bps"
        )
        for horizon, result in report["horizons"].items():
            value = result["validation"]
            print(
                f"{horizon}: n={value['n']} | net={value['net_directional_return_pct']} | "
                f"edge_vs_long={value['net_edge_vs_always_long_pct']} | "
                f"decision={result['decision']}"
            )
        print(f"Result: {OUTPUT_PATH}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
