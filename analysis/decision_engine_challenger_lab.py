"""Conservative challenger search over mechanically sealed replay events.

Candidate rules are fixed in code before evaluation. Every candidate is first
measured on LEARN with a multiple-testing-adjusted lower confidence bound. Only
the three strongest survivors may enter VALIDATION. EXAM is evaluated only for
the single winner selected without EXAM data.

The lab never updates the live Decision Engine policy. Even an EXAM pass remains
historical SHADOW_ONLY evidence and must later earn forward evidence.

Integrity disclosure: the aggregate current-policy EXAM result was observed
before this challenger family was defined. Candidate selection cannot read EXAM
rows, but a pass is historical corroboration rather than pristine blind proof.
"""
from __future__ import annotations

import csv
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path


LAB_VERSION = "DECISION_CHALLENGER_LAB_V1"
SOURCE_REPLAY_VERSION = "DECISION_REPLAY_V1"
SOURCE_PATH = Path("reports/decision_engine_historical_replay.csv")
DETAIL_PATH = Path("reports/decision_engine_challenger_lab.csv")
SELECTION_PATH = Path("reports/decision_engine_challenger_selection.csv")
HORIZONS = ("1h", "4h", "24h")
MIN_LEARN_EVENTS = 300
MIN_VALIDATION_EVENTS = 100
MIN_EXAM_EVENTS = 100
MIN_LEARN_BLOCKS = 20
MIN_VALIDATION_BLOCKS = 8
MIN_EXAM_BLOCKS = 8
BLOCK_DAYS = 7
SHORTLIST_SIZE = 3
LEARN_Z = 3.20
VALIDATION_Z = 2.40
EXAM_Z = 1.96


FILTER_FAMILIES = (
    ("BASE", 2, 0.0, False),
    ("STRONG", 3, 0.0, False),
    ("MOM1", 2, 1.0, False),
    ("STRONG_MOM1", 3, 1.0, False),
    ("STRONG_MOM2", 3, 2.0, False),
    ("STRONG_MOM2_NORMAL", 3, 2.0, True),
)


def fixed_configs():
    configs = []
    for orientation in ("FOLLOW", "CONTRARIAN"):
        for context_action in ("ALL", "AL", "SAT"):
            for family, min_score, min_momentum, normal_only in FILTER_FAMILIES:
                configs.append({
                    "name": (
                        f"{orientation}_{context_action}_{family}"
                    ),
                    "orientation": orientation,
                    "context_action": context_action,
                    "family": family,
                    "min_abs_score": min_score,
                    "min_abs_momentum_24h_pct": min_momentum,
                    "normal_market_only": normal_only,
                })
    return configs


CONFIGS = tuple(fixed_configs())


def parse_float(value, field):
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid numeric field {field}: {value}") from exc


def parse_time(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def block_key(event):
    timestamp = event.get("entry_timestamp")
    if timestamp is None:
        timestamp = parse_time(event["entry_time"]).timestamp()
    return int(timestamp // (BLOCK_DAYS * 24 * 60 * 60))


def load_events(path=SOURCE_PATH):
    if not path.exists():
        raise FileNotFoundError(
            f"Replay event file not found: {path}. Run historical replay first."
        )
    events = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "replay_version",
            "split",
            "entry_time",
            "symbol",
            "action",
            "technical_score",
            "market_return_24h_pct",
            "risk_market_state",
        }
        for horizon in HORIZONS:
            required.update({
                f"{horizon}_gross_directional_return_pct",
                f"{horizon}_net_directional_return_pct",
            })
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(
                "Replay event file is missing fields: "
                + ", ".join(sorted(missing))
            )
        for raw in reader:
            if raw["replay_version"] != SOURCE_REPLAY_VERSION:
                continue
            split = raw["split"].strip().upper()
            action = raw["action"].strip().upper()
            if split not in {"LEARN", "VALIDATION", "EXAM"}:
                continue
            if action not in {"AL", "SAT"}:
                continue
            event = {
                "split": split,
                "entry_time": raw["entry_time"],
                "entry_timestamp": parse_time(
                    raw["entry_time"]
                ).timestamp(),
                "symbol": raw["symbol"],
                "action": action,
                "technical_score": int(float(raw["technical_score"])),
                "market_return_24h_pct": parse_float(
                    raw["market_return_24h_pct"],
                    "market_return_24h_pct",
                ),
                "risk_market_state": raw["risk_market_state"].strip().upper(),
            }
            for horizon in HORIZONS:
                gross = parse_float(
                    raw[f"{horizon}_gross_directional_return_pct"],
                    f"{horizon}_gross_directional_return_pct",
                )
                net = parse_float(
                    raw[f"{horizon}_net_directional_return_pct"],
                    f"{horizon}_net_directional_return_pct",
                )
                cost = gross - net
                if cost < -1e-9:
                    raise ValueError(
                        f"{horizon}: net return exceeds gross return"
                    )
                event[horizon] = {
                    "recorded_gross": gross,
                    "recorded_net": net,
                    "cost": max(0.0, cost),
                }
            events.append(event)
    events.sort(key=lambda row: (row["entry_time"], row["symbol"]))
    if not events:
        raise ValueError("Replay event file contains no usable events")
    return events


def config_matches(event, config):
    if (
        config["context_action"] != "ALL"
        and event["action"] != config["context_action"]
    ):
        return False
    if abs(event["technical_score"]) < config["min_abs_score"]:
        return False
    if (
        abs(event["market_return_24h_pct"])
        < config["min_abs_momentum_24h_pct"]
    ):
        return False
    if (
        config["normal_market_only"]
        and event["risk_market_state"] != "NORMAL"
    ):
        return False
    return True


def net_return(event, config, horizon):
    outcome = event[horizon]
    gross = outcome["recorded_gross"]
    if config["orientation"] == "CONTRARIAN":
        gross = -gross
    return gross - outcome["cost"]


def wilson_lower(hits, count, z=1.96):
    if count <= 0:
        return 0.0
    proportion = hits / count
    denominator = 1.0 + z * z / count
    centre = proportion + z * z / (2.0 * count)
    margin = z * math.sqrt(
        (
            proportion * (1.0 - proportion)
            + z * z / (4.0 * count)
        ) / count
    )
    return max(0.0, (centre - margin) / denominator) * 100.0


def metric(events, config, split, horizon, z):
    selected = [
        event
        for event in events
        if event["split"] == split and config_matches(event, config)
    ]
    values = [net_return(event, config, horizon) for event in selected]
    by_block = {}
    for event, value in zip(selected, values):
        by_block.setdefault(block_key(event), []).append(value)
    block_means = [
        statistics.fmean(by_block[key])
        for key in sorted(by_block)
    ]
    count = len(values)
    hits = sum(value > 0 for value in values)
    average = statistics.fmean(values) if values else 0.0
    deviation = statistics.stdev(values) if len(values) > 1 else 0.0
    block_average = (
        statistics.fmean(block_means) if block_means else 0.0
    )
    block_deviation = (
        statistics.stdev(block_means) if len(block_means) > 1 else 0.0
    )
    block_standard_error = (
        block_deviation / math.sqrt(len(block_means))
        if block_means
        else 0.0
    )
    lower_bound = (
        block_average - z * block_standard_error
        if block_means
        else 0.0
    )
    positives = sum(value for value in values if value > 0)
    negatives = abs(sum(value for value in values if value < 0))

    running = 0.0
    peak = 0.0
    sequence_drawdown = 0.0
    for value in values:
        running += value
        peak = max(peak, running)
        sequence_drawdown = min(sequence_drawdown, running - peak)

    return {
        "lab_version": LAB_VERSION,
        "source_replay_version": SOURCE_REPLAY_VERSION,
        "config": config["name"],
        "orientation": config["orientation"],
        "context_action": config["context_action"],
        "family": config["family"],
        "min_abs_score": config["min_abs_score"],
        "min_abs_momentum_24h_pct":
            config["min_abs_momentum_24h_pct"],
        "normal_market_only": int(config["normal_market_only"]),
        "split": split,
        "horizon": horizon,
        "events": count,
        "time_blocks": len(block_means),
        "block_days": BLOCK_DAYS,
        "hits": hits,
        "hit_rate_pct": hits / count * 100.0 if count else 0.0,
        "wilson_lower_pct": wilson_lower(hits, count, z),
        "average_net_return_pct": average,
        "median_net_return_pct":
            statistics.median(values) if values else 0.0,
        "standard_deviation_pct": deviation,
        "block_mean_net_return_pct": block_average,
        "block_standard_deviation_pct": block_deviation,
        "block_mean_lower_bound_pct": lower_bound,
        "cumulative_net_return_pct_points": sum(values),
        "profit_factor": positives / negatives if negatives else (
            None if not positives else float("inf")
        ),
        "sequence_max_drawdown_pct_points": sequence_drawdown,
        "confidence_z": z,
    }


def eligible(row, minimum_events, minimum_blocks):
    return (
        row["events"] >= minimum_events
        and row["time_blocks"] >= minimum_blocks
        and row["block_mean_lower_bound_pct"] > 0.0
    )


def conservative_rank(learn, validation):
    return (
        min(
            learn["block_mean_lower_bound_pct"],
            validation["block_mean_lower_bound_pct"],
        ),
        validation["block_mean_lower_bound_pct"],
        validation["events"],
    )


def select_horizon(events, horizon, configs=CONFIGS):
    detail = []
    learn_rows = []
    by_name = {config["name"]: config for config in configs}
    for config in configs:
        row = metric(events, config, "LEARN", horizon, LEARN_Z)
        detail.append(row)
        if eligible(row, MIN_LEARN_EVENTS, MIN_LEARN_BLOCKS):
            learn_rows.append(row)

    learn_rows.sort(
        key=lambda row: (
            row["block_mean_lower_bound_pct"],
            row["events"],
        ),
        reverse=True,
    )
    shortlist = learn_rows[:SHORTLIST_SIZE]
    validation_pairs = []
    for learn in shortlist:
        config = by_name[learn["config"]]
        validation = metric(
            events,
            config,
            "VALIDATION",
            horizon,
            VALIDATION_Z,
        )
        detail.append(validation)
        if eligible(
            validation,
            MIN_VALIDATION_EVENTS,
            MIN_VALIDATION_BLOCKS,
        ):
            validation_pairs.append((learn, validation))

    if not learn_rows:
        return {
            "horizon": horizon,
            "status": "NO_LEARN_EDGE",
            "learn_qualified": 0,
            "validation_qualified": 0,
            "selected_config": None,
            "learn": None,
            "validation": None,
            "exam": None,
            "detail": detail,
        }
    if not validation_pairs:
        return {
            "horizon": horizon,
            "status": "NO_VALIDATED_CHALLENGER",
            "learn_qualified": len(learn_rows),
            "validation_qualified": 0,
            "selected_config": None,
            "learn": None,
            "validation": None,
            "exam": None,
            "detail": detail,
        }

    learn, validation = max(
        validation_pairs,
        key=lambda pair: conservative_rank(pair[0], pair[1]),
    )
    config = by_name[learn["config"]]

    # EXAM is touched only after the winner has been frozen above.
    exam = metric(events, config, "EXAM", horizon, EXAM_Z)
    detail.append(exam)
    if (
        exam["events"] < MIN_EXAM_EVENTS
        or exam["time_blocks"] < MIN_EXAM_BLOCKS
    ):
        status = "EXAM_INSUFFICIENT"
    elif exam["block_mean_lower_bound_pct"] > 0.0:
        status = "HISTORICAL_EXAM_PASS"
    elif exam["average_net_return_pct"] > 0.0:
        status = "EXAM_INCONCLUSIVE"
    else:
        status = "EXAM_FAIL"
    return {
        "horizon": horizon,
        "status": status,
        "learn_qualified": len(learn_rows),
        "validation_qualified": len(validation_pairs),
        "selected_config": config,
        "learn": learn,
        "validation": validation,
        "exam": exam,
        "detail": detail,
    }


def baseline_exam(events, horizon):
    config = next(
        item
        for item in CONFIGS
        if item["name"] == "FOLLOW_ALL_BASE"
    )
    return metric(events, config, "EXAM", horizon, EXAM_Z)


def selection_row(result):
    row = {
        "lab_version": LAB_VERSION,
        "source_replay_version": SOURCE_REPLAY_VERSION,
        "horizon": result["horizon"],
        "status": result["status"],
        "learn_qualified": result["learn_qualified"],
        "validation_qualified": result["validation_qualified"],
        "selected_config": (
            result["selected_config"]["name"]
            if result["selected_config"]
            else ""
        ),
    }
    for split in ("learn", "validation", "exam"):
        metric_row = result[split]
        row[f"{split}_events"] = metric_row["events"] if metric_row else 0
        row[f"{split}_hit_rate_pct"] = (
            metric_row["hit_rate_pct"] if metric_row else 0.0
        )
        row[f"{split}_average_net_return_pct"] = (
            metric_row["average_net_return_pct"] if metric_row else 0.0
        )
        row[f"{split}_time_blocks"] = (
            metric_row["time_blocks"] if metric_row else 0
        )
        row[f"{split}_block_mean_lower_bound_pct"] = (
            metric_row["block_mean_lower_bound_pct"]
            if metric_row
            else 0.0
        )
    return row


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def short_metric(row):
    if row is None:
        return "n=0"
    return (
        f"n={row['events']} | blocks={row['time_blocks']} | "
        f"hit={row['hit_rate_pct']:.1f}% | "
        f"net={row['average_net_return_pct']:+.3f}% | "
        f"block_LB={row['block_mean_lower_bound_pct']:+.3f}%"
    )


def render(events, results):
    print("AG Crypto Analyst - Decision Engine Challenger Lab v1.0.0")
    print(
        f"Source: {SOURCE_REPLAY_VERSION} | events={len(events)} | "
        f"fixed_configs={len(CONFIGS)}/horizon"
    )
    print(
        "Discipline: LEARN z=3.20 -> top 3 -> VALIDATION z=2.40 "
        "-> frozen winner -> EXAM z=1.96"
    )
    print(
        "Integrity: EXAM is mechanically hidden from selection; its prior "
        "current-policy aggregate was already observed."
    )
    print(
        "Selection rule: minimum samples and 7-day blocks plus positive "
        "block-mean lower bound in both LEARN and VALIDATION."
    )

    for result in results:
        horizon = result["horizon"]
        baseline = baseline_exam(events, horizon)
        print(f"\n{horizon.upper()} | current-policy EXAM: {short_metric(baseline)}")
        print(
            f"qualified: learn={result['learn_qualified']} | "
            f"validation={result['validation_qualified']}"
        )
        if not result["selected_config"]:
            print(f"result: {result['status']}")
            continue
        print(f"winner: {result['selected_config']['name']}")
        print(f"LEARN      {short_metric(result['learn'])}")
        print(f"VALIDATION {short_metric(result['validation'])}")
        print(f"EXAM       {short_metric(result['exam'])}")
        print(f"result: {result['status']}")

    passes = sum(
        result["status"] == "HISTORICAL_EXAM_PASS"
        for result in results
    )
    if passes:
        conclusion = f"HISTORICAL_CHALLENGER_FOUND ({passes}/{len(results)})"
    else:
        conclusion = "NO_HISTORICALLY_CONFIRMED_CHALLENGER"
    print(f"\nCONCLUSION: {conclusion} | product_state=SHADOW_ONLY")
    print(
        "No live threshold or policy changed. Any historical pass still "
        "requires fresh forward validation."
    )
    print(
        "sequence drawdown fields are additive signal diagnostics, not "
        "portfolio drawdown."
    )
    print(f"Details: {DETAIL_PATH}")
    print(f"Selection: {SELECTION_PATH}")


def main():
    events = load_events()
    results = [
        select_horizon(events, horizon)
        for horizon in HORIZONS
    ]
    detail = [
        row
        for result in results
        for row in result["detail"]
    ]
    selections = [selection_row(result) for result in results]
    write_csv(DETAIL_PATH, detail)
    write_csv(SELECTION_PATH, selections)
    render(events, results)


if __name__ == "__main__":
    main()
