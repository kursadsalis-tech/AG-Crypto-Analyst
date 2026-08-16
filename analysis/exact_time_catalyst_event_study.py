"""Frozen exact-time Binance listing/delisting catalyst event study.

The hypothesis is fixed before reading returns:

* STRICT_LISTING -> AL candidate.
* STRICT_DELISTING -> SAT/REDUCE/AVOID candidate (not a short trade).

There is no model family, feature search, threshold grid or EXAM reopening.
Events are split by article code so multi-coin announcements cannot cross the
chronological 70/30 DEVELOPMENT/VALIDATION boundary.  Validation uncertainty
is clustered by article and week.  A pass remains SHADOW_ONLY and requires a
fresh forward probability calibration before any product claim.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import math
import random
import sqlite3
import statistics
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from data import exact_time_catalyst_market_data as market_data


VERSION = "EXACT_TIME_CATALYST_EVENT_STUDY_V1"
DB_NAME = market_data.DB_NAME
HORIZONS = {"1h": 1, "4h": 4, "24h": 24}
PRIMARY_HORIZON = "4h"
ROUND_TRIP_COST_BPS = 10.0
DEVELOPMENT_FRACTION = 0.70
BOOTSTRAP_SAMPLES = 4000
BOOTSTRAP_SEED = 20260816
DETAIL_PATH = Path("reports/exact_time_catalyst_event_study.csv")
SELECTION_PATH = Path("reports/exact_time_catalyst_event_selection.csv")


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def load_candles(con, symbols):
    output = {}
    for symbol in symbols:
        rows = con.execute("""
            SELECT open_time, close_time, open, high, low, close,
                   volume, quote_volume
            FROM catalyst_spot_hourly
            WHERE symbol=? ORDER BY open_time
        """, (symbol,)).fetchall()
        candles = [{
            "open_time": market_data.parse_time(row[0]),
            "close_time": market_data.parse_time(row[1]),
            "open": float(row[2]), "high": float(row[3]),
            "low": float(row[4]), "close": float(row[5]),
            "volume": float(row[6]), "quote_volume": float(row[7]),
        } for row in rows]
        output[symbol] = {
            "candles": candles,
            "open_times": [row["open_time"] for row in candles],
        }
    return output


def action_for_event(event_type):
    if event_type == "STRICT_LISTING":
        return "AL"
    if event_type == "STRICT_DELISTING":
        return "SAT"
    raise ValueError("unsupported event type")


def causal_entry_index(item, event_time):
    times = item["open_times"]
    index = bisect.bisect_left(times, event_time)
    if index >= len(times):
        return None
    # An event exactly at candle open cannot use that same opening print.
    if abs((times[index] - event_time).total_seconds()) <= 1.0:
        index += 1
    if index >= len(times):
        return None
    # If this is the pair's first observed tradable candle or follows a gap,
    # wait one complete price-discovery hour before entering.
    previous = index - 1
    if previous < 0 or (
        times[index] - times[previous]
    ).total_seconds() > 3601.0:
        index += 1
    if index >= len(times):
        return None
    delay = (times[index] - event_time).total_seconds() / 3600.0
    return index if 0.0 < delay <= 49.0 else None


def contiguous(candles, start, end):
    if start < 0 or end >= len(candles):
        return False
    for index in range(start + 1, end + 1):
        delta = (
            candles[index]["open_time"] - candles[index - 1]["open_time"]
        ).total_seconds()
        if not 3599.0 <= delta <= 3601.0:
            return False
    return True


def build_rows(con):
    events, cutoff, latest = market_data.load_events()
    coin_events = market_data.event_coin_rows(events)
    series = load_candles(con, sorted({row["symbol"] for row in coin_events}))
    output = []
    source_gaps = 0
    for row in sorted(coin_events, key=lambda item: (
        item["event_time"], item["article_code"], item["symbol"]
    )):
        item = series[row["symbol"]]
        entry_index = causal_entry_index(item, row["event_time"])
        if entry_index is None:
            source_gaps += 1
            continue
        candles = item["candles"]
        final_index = entry_index + max(HORIZONS.values()) - 1
        if not contiguous(candles, entry_index, final_index):
            source_gaps += 1
            continue
        action = action_for_event(row["event_type"])
        entry = candles[entry_index]
        event_row = {
            "version": VERSION,
            "article_code": row["article_code"],
            "event_type": row["event_type"],
            "event_time": row["event_time"],
            "title": row["title"],
            "ticker": row["ticker"],
            "symbol": row["symbol"],
            "action": action,
            "entry_time": entry["open_time"],
            "entry_price": entry["open"],
            "entry_delay_hours": (
                entry["open_time"] - row["event_time"]
            ).total_seconds() / 3600.0,
        }
        for label, hours in HORIZONS.items():
            exit_candle = candles[entry_index + hours - 1]
            market_return = (exit_candle["close"] / entry["open"] - 1.0) * 100.0
            gross = market_return if action == "AL" else -market_return
            event_row.update({
                f"{label}_exit_time": exit_candle["close_time"],
                f"{label}_exit_price": exit_candle["close"],
                f"{label}_market_return_pct": market_return,
                f"{label}_directional_utility_pct": (
                    gross - ROUND_TRIP_COST_BPS / 100.0
                ),
                f"{label}_double_cost_utility_pct": (
                    gross - 2.0 * ROUND_TRIP_COST_BPS / 100.0
                ),
            })
        output.append(event_row)
    return output, source_gaps, cutoff, latest


def remove_overlap(rows):
    output = []
    blocked = 0
    next_allowed = {}
    for row in rows:
        allowed = next_allowed.get(
            row["symbol"], datetime.min.replace(tzinfo=timezone.utc)
        )
        if row["entry_time"] < allowed:
            blocked += 1
            continue
        output.append(row)
        next_allowed[row["symbol"]] = row["entry_time"] + timedelta(hours=24)
    return output, blocked


def assign_splits(rows):
    article_times = {}
    for row in rows:
        article_times.setdefault(row["article_code"], row["event_time"])
    ordered = sorted(article_times, key=lambda code: (article_times[code], code))
    if len(ordered) < 2:
        raise RuntimeError("insufficient independent articles for split")
    cut = max(1, min(len(ordered) - 1, int(len(ordered) * DEVELOPMENT_FRACTION)))
    development = set(ordered[:cut])
    for row in rows:
        row["split"] = (
            "DEVELOPMENT" if row["article_code"] in development else "VALIDATION"
        )
    return article_times[ordered[cut]]


def article_clusters(rows, horizon, double_cost=False):
    key = (
        f"{horizon}_double_cost_utility_pct" if double_cost
        else f"{horizon}_directional_utility_pct"
    )
    grouped = defaultdict(list)
    metadata = {}
    for row in rows:
        grouped[row["article_code"]].append(float(row[key]))
        metadata[row["article_code"]] = row
    return [{
        "article_code": code,
        "utility": statistics.fmean(values),
        "week": metadata[code]["event_time"].date().toordinal() // 7,
        "event_time": metadata[code]["event_time"],
        "event_type": metadata[code]["event_type"],
    } for code, values in grouped.items()]


def percentile(values, probability):
    values = sorted(values)
    if not values:
        return float("-inf")
    index = max(0, min(len(values) - 1, math.ceil(probability * len(values)) - 1))
    return values[index]


def weekly_cluster_lower_bound(clusters):
    weeks = defaultdict(list)
    for row in clusters:
        weeks[row["week"]].append(row["utility"])
    keys = sorted(weeks)
    if len(keys) < 8:
        return float("-inf"), len(keys)
    rng = random.Random(BOOTSTRAP_SEED)
    estimates = []
    for _ in range(BOOTSTRAP_SAMPLES):
        sampled = [rng.choice(keys) for _ in keys]
        values = [value for week in sampled for value in weeks[week]]
        estimates.append(statistics.fmean(values))
    return percentile(estimates, 0.025), len(keys)


def wilson_lower(hits, count, z=1.959963984540054):
    if count <= 0:
        return 0.0
    rate = hits / count
    denominator = 1.0 + z * z / count
    centre = rate + z * z / (2.0 * count)
    adjustment = z * math.sqrt(
        (rate * (1.0 - rate) + z * z / (4.0 * count)) / count
    )
    return max(0.0, (centre - adjustment) / denominator)


def subwindow_positive(clusters):
    ordered = sorted(
        clusters, key=lambda row: (row["event_time"], row["article_code"])
    )
    size = len(ordered)
    if size < 3:
        return 0
    boundaries = (size // 3, 2 * size // 3)
    slices = (
        ordered[:boundaries[0]],
        ordered[boundaries[0]:boundaries[1]],
        ordered[boundaries[1]:],
    )
    return sum(bool(part) and statistics.fmean(row["utility"] for row in part) > 0
               for part in slices)


def evaluate(rows, split, horizon):
    selected = [row for row in rows if row["split"] == split]
    clusters = article_clusters(selected, horizon)
    double_clusters = article_clusters(selected, horizon, double_cost=True)
    values = [row["utility"] for row in clusters]
    double_values = [row["utility"] for row in double_clusters]
    hits = sum(value > 0 for value in values)
    block_lb, weeks = weekly_cluster_lower_bound(clusters)
    type_values = defaultdict(list)
    for row in clusters:
        type_values[row["event_type"]].append(row["utility"])
    coins = Counter(row["ticker"] for row in selected)
    result = {
        "version": VERSION,
        "split": split,
        "horizon": horizon,
        "coin_events": len(selected),
        "articles": len(clusters),
        "weeks": weeks,
        "coins": len(coins),
        "listing_articles": sum(row["event_type"] == "STRICT_LISTING" for row in clusters),
        "delisting_articles": sum(row["event_type"] == "STRICT_DELISTING" for row in clusters),
        "article_hit_rate": hits / len(values) if values else 0.0,
        "article_wilson_lower": wilson_lower(hits, len(values)),
        "average_utility_pct": statistics.fmean(values) if values else 0.0,
        "median_utility_pct": statistics.median(values) if values else 0.0,
        "double_cost_average_pct": statistics.fmean(double_values) if double_values else 0.0,
        "weekly_cluster_lb_pct": block_lb,
        "listing_average_pct": (
            statistics.fmean(type_values["STRICT_LISTING"])
            if type_values["STRICT_LISTING"] else None
        ),
        "delisting_average_pct": (
            statistics.fmean(type_values["STRICT_DELISTING"])
            if type_values["STRICT_DELISTING"] else None
        ),
        "largest_coin_share": max(coins.values(), default=0) / len(selected)
        if selected else 0.0,
        "positive_subwindows": subwindow_positive(clusters),
    }
    if split == "VALIDATION":
        gates = {
            "articles_gte_20": result["articles"] >= 20,
            "coin_events_gte_40": result["coin_events"] >= 40,
            "weeks_gte_8": weeks >= 8,
            "coins_gte_20": result["coins"] >= 20,
            "listing_articles_gte_5": result["listing_articles"] >= 5,
            "delisting_articles_gte_5": result["delisting_articles"] >= 5,
            "article_hit_rate_gte_60pct": result["article_hit_rate"] >= 0.60,
            "average_utility_positive": result["average_utility_pct"] > 0,
            "double_cost_average_positive": result["double_cost_average_pct"] > 0,
            "weekly_cluster_lb_positive": block_lb > 0,
            "listing_average_positive": (
                result["listing_average_pct"] is not None
                and result["listing_average_pct"] > 0
            ),
            "delisting_average_positive": (
                result["delisting_average_pct"] is not None
                and result["delisting_average_pct"] > 0
            ),
            "largest_coin_share_lte_10pct": result["largest_coin_share"] <= 0.10,
            "positive_subwindows_gte_2": result["positive_subwindows"] >= 2,
        }
        result["gates"] = gates
        result["passed"] = all(gates.values())
    return result


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
        for row in rows:
            writer.writerow({
                key: iso(value) if isinstance(value, datetime) else value
                for key, value in row.items()
            })


def render(rows, metrics, source_gaps, overlap, split_time):
    print("AG Crypto Analyst - Exact-Time Catalyst Event Study v1.0.0")
    print(
        "Frozen rule: STRICT_LISTING=AL | STRICT_DELISTING=SAT/REDUCE/AVOID | "
        f"cost={ROUND_TRIP_COST_BPS:.1f}bps"
    )
    print(
        f"Rows={len(rows)} | articles={len({row['article_code'] for row in rows})} | "
        f"source_gaps={source_gaps} | overlap_blocks={overlap} | "
        f"VALIDATION starts={iso(split_time)}"
    )
    for result in metrics:
        lb = result["weekly_cluster_lb_pct"]
        lb_text = "N/A" if not math.isfinite(lb) else f"{lb:+.3f}%"
        status = "PASS" if result.get("passed") else (
            "REJECT" if result["split"] == "VALIDATION" else "DIAGNOSTIC"
        )
        print(
            f"{result['split']:<11} {result['horizon']:<3} | {status:<10} | "
            f"articles={result['articles']:<3} coin_events={result['coin_events']:<3} | "
            f"hit={100.0 * result['article_hit_rate']:.1f}% | "
            f"utility={result['average_utility_pct']:+.3f}% | block_LB={lb_text}"
        )
        if result["split"] == "VALIDATION" and not result["passed"]:
            failed = [key for key, value in result["gates"].items() if not value]
            print("failed: " + ",".join(failed))
    primary = next(row for row in metrics if (
        row["split"] == "VALIDATION" and row["horizon"] == PRIMARY_HORIZON
    ))
    conclusion = (
        "HISTORICAL_CATALYST_EDGE_CANDIDATE"
        if primary["passed"] else "NO_VALIDATED_CATALYST_EDGE"
    )
    print(f"CONCLUSION: {conclusion} | product_state=SHADOW_ONLY")
    print(
        "A historical pass is not a calibrated confidence claim. Fresh forward "
        "events remain mandatory before alerts can be promoted."
    )
    print(f"Details: {DETAIL_PATH}")
    print(f"Selection: {SELECTION_PATH}")


def self_test():
    assert action_for_event("STRICT_LISTING") == "AL"
    assert action_for_event("STRICT_DELISTING") == "SAT"
    clusters = [
        {"article_code": str(index), "utility": 1.0, "week": index,
         "event_type": "STRICT_LISTING"}
        for index in range(10)
    ]
    lower, weeks = weekly_cluster_lower_bound(clusters)
    assert weeks == 10 and lower == 1.0
    print("Exact-time catalyst event study self-test: PASS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    con = sqlite3.connect(DB_NAME)
    try:
        market_data.ensure_tables(con)
        rows, source_gaps, _, _ = build_rows(con)
        rows, overlap = remove_overlap(rows)
        split_time = assign_splits(rows)
        metrics = [
            evaluate(rows, split, horizon)
            for split in ("DEVELOPMENT", "VALIDATION")
            for horizon in HORIZONS
        ]
        write_csv(DETAIL_PATH, rows)
        flat_metrics = []
        for row in metrics:
            flat = {key: value for key, value in row.items() if key != "gates"}
            if "gates" in row:
                flat["failed_gates"] = ",".join(
                    key for key, value in row["gates"].items() if not value
                )
            flat_metrics.append(flat)
        write_csv(SELECTION_PATH, flat_metrics)
        render(rows, metrics, source_gaps, overlap, split_time)
    finally:
        con.close()


if __name__ == "__main__":
    main()
