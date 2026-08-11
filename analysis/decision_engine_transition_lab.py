"""Sparse transition-event replay for the Decision Engine research layer.

The rejected policy treated persistent BULLISH/BEARISH states as fresh signals
every hour. This final price/technical hypothesis tests only discrete changes:
score crossings, MACD/EMA zero crossings and closed-price 24h breakouts.

All features are point-in-time. Entry is the next candle open, a symbol enters
at most once per 24 hours for each fixed configuration, and outcomes include
the replay's round-trip cost. Candidate selection is LEARN -> VALIDATION ->
mechanically sealed EXAM with seven-day block confidence bounds.

Nothing in this lab changes the live Decision Engine or product state.
"""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from analysis import decision_engine_challenger_lab as statistics_lab
from analysis import decision_engine_historical_replay as replay


DB_NAME = replay.DB_NAME
LAB_VERSION = "DECISION_TRANSITION_LAB_V1"
SOURCE_VERSION = replay.REPLAY_VERSION
DETAIL_PATH = Path("reports/decision_engine_transition_lab.csv")
SELECTION_PATH = Path("reports/decision_engine_transition_selection.csv")
HORIZONS = tuple(replay.HORIZONS)
COOLDOWN_HOURS = max(replay.HORIZONS.values())
MIN_LEARN_EVENTS = 100
MIN_VALIDATION_EVENTS = 30
MIN_EXAM_EVENTS = 30
MIN_LEARN_BLOCKS = 20
MIN_VALIDATION_BLOCKS = 8
MIN_EXAM_BLOCKS = 8
SHORTLIST_SIZE = 3
LEARN_Z = 3.25
VALIDATION_Z = 2.40
EXAM_Z = 1.96

TRIGGER_FAMILIES = (
    "SCORE_CROSS_2",
    "SCORE_CROSS_3",
    "MACD_ZERO_CROSS",
    "EMA_ZERO_CROSS",
    "BREAKOUT_24H",
    "BREAKOUT_SCORE_2",
)
VOLUME_MINIMUMS = (0.0, 1.5, 2.0)


def fixed_configs():
    configs = []
    for trigger in TRIGGER_FAMILIES:
        for volume_min in VOLUME_MINIMUMS:
            for normal_only in (False, True):
                volume_label = str(volume_min).replace(".", "_")
                market_label = "NORMAL" if normal_only else "ANY"
                configs.append({
                    "name": (
                        f"{trigger}|vol>={volume_label}|market={market_label}"
                    ),
                    "trigger": trigger,
                    "volume_ratio_min": volume_min,
                    "normal_market_only": normal_only,
                })
    return configs


CONFIGS = tuple(fixed_configs())


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def split_for(timestamp, boundaries):
    if timestamp < boundaries["learn_end"]:
        return "LEARN"
    if timestamp < boundaries["validation_end"]:
        return "VALIDATION"
    return "EXAM"


def trigger_action(context, trigger):
    score = context["technical_score"]
    previous_score = context["previous_technical_score"]
    histogram = context["histogram"]
    previous_histogram = context["previous_histogram"]
    ema_spread = context["ema_spread_pct"]
    previous_ema_spread = context["previous_ema_spread_pct"]
    breakout_up = context["breakout_up"]
    breakout_down = context["breakout_down"]

    if trigger == "SCORE_CROSS_2":
        if score >= 2 and previous_score < 2:
            return "AL"
        if score <= -2 and previous_score > -2:
            return "SAT"
    elif trigger == "SCORE_CROSS_3":
        if score >= 3 and previous_score < 3:
            return "AL"
        if score <= -3 and previous_score > -3:
            return "SAT"
    elif trigger == "MACD_ZERO_CROSS":
        if histogram > 0 and previous_histogram <= 0:
            return "AL"
        if histogram < 0 and previous_histogram >= 0:
            return "SAT"
    elif trigger == "EMA_ZERO_CROSS":
        if ema_spread > 0 and previous_ema_spread <= 0:
            return "AL"
        if ema_spread < 0 and previous_ema_spread >= 0:
            return "SAT"
    elif trigger == "BREAKOUT_24H":
        if breakout_up:
            return "AL"
        if breakout_down:
            return "SAT"
    elif trigger == "BREAKOUT_SCORE_2":
        if breakout_up and score >= 2:
            return "AL"
        if breakout_down and score <= -2:
            return "SAT"
    else:
        raise ValueError(f"unsupported trigger: {trigger}")
    return None


def build_contexts(series, replay_start, replay_end):
    btc = series["BTCUSDT"]["candles"]
    boundaries = replay.split_boundaries(replay_start, replay_end)
    contexts = []
    decision_points = 0
    risk_blocks = 0
    source_errors = 0
    max_horizon = max(replay.HORIZONS.values())

    for btc_index in range(
        replay.MIN_CLOSED_CANDLES - 1,
        len(btc) - max_horizon,
    ):
        decision_time = btc[btc_index]["close_time"]
        if decision_time < replay_start or decision_time >= replay_end:
            continue
        decision_points += 1
        policy, assets, indexes, errors = replay.risk_for_timestamp(
            series,
            decision_time,
        )
        source_errors += len(errors)

        for symbol in replay.SYMBOLS:
            asset = assets.get(symbol)
            index = indexes.get(symbol)
            if asset is None or index is None:
                continue
            risk = replay.decision_engine.risk_guard(
                policy,
                asset,
                decision_time,
            )
            if risk["decision"] != "ALLOW":
                risk_blocks += 1
                continue
            candles = series[symbol]["candles"]
            try:
                technical = replay.technical_features_at(candles, index)
                previous = replay.technical_features_at(
                    candles,
                    index - 1,
                )
                replay.require_hourly_continuity(
                    candles,
                    index - 24,
                    index,
                )
                outcomes = replay.candidate_outcomes(
                    candles,
                    index,
                    "AL",
                )
            except (ValueError, IndexError):
                source_errors += 1
                continue
            if outcomes is None:
                continue

            previous_window = candles[index - 24:index]
            previous_high = max(row["high"] for row in previous_window)
            previous_low = min(row["low"] for row in previous_window)
            close = candles[index]["close"]
            contexts.append({
                "decision_time": decision_time,
                "entry_time": outcomes["entry_time"],
                "split": split_for(outcomes["entry_time"], boundaries),
                "symbol": symbol,
                "technical_score": technical["score"],
                "previous_technical_score": previous["score"],
                "histogram": technical["histogram"],
                "previous_histogram": previous["histogram"],
                "ema_spread_pct": technical["ema_spread_pct"],
                "previous_ema_spread_pct": previous["ema_spread_pct"],
                "ret6_pct": technical["ret6"],
                "volume_ratio": technical["volume_ratio"],
                "breakout_up": close > previous_high,
                "breakout_down": close < previous_low,
                "market_return_24h_pct": asset["return_24h_pct"],
                "risk_market_state": policy["market_state"],
                "risk_tier": asset["risk_tier"],
                "risk_cap_pct": risk["max_position_pct"],
                "market_outcomes": {
                    horizon: {
                        "exit_time": outcomes[horizon]["exit_time"],
                        "exit_price": outcomes[horizon]["exit_price"],
                        "market_return_pct":
                            outcomes[horizon]["market_return_pct"],
                    }
                    for horizon in HORIZONS
                },
            })

    contexts.sort(
        key=lambda row: (row["entry_time"], row["symbol"])
    )
    return {
        "contexts": contexts,
        "decision_points": decision_points,
        "risk_blocks": risk_blocks,
        "source_errors": source_errors,
        "boundaries": boundaries,
    }


def event_from_context(context, action):
    event = {
        "split": context["split"],
        "entry_time": iso(context["entry_time"]),
        "entry_timestamp": context["entry_time"].timestamp(),
        "symbol": context["symbol"],
        "action": action,
        "technical_score": context["technical_score"],
        "market_return_24h_pct":
            context["market_return_24h_pct"],
        "risk_market_state": context["risk_market_state"],
    }
    cost = replay.ROUND_TRIP_COST_BPS / 100.0
    for horizon in HORIZONS:
        market_return = context["market_outcomes"][horizon][
            "market_return_pct"
        ]
        gross = market_return if action == "AL" else -market_return
        event[horizon] = {
            "recorded_gross": gross,
            "recorded_net": gross - cost,
            "cost": cost,
        }
    return event


def events_for_config(contexts, config):
    events = []
    next_allowed = {
        symbol: datetime.min.replace(tzinfo=timezone.utc)
        for symbol in replay.SYMBOLS
    }
    for context in contexts:
        if (
            config["normal_market_only"]
            and context["risk_market_state"] != "NORMAL"
        ):
            continue
        if context["volume_ratio"] < config["volume_ratio_min"]:
            continue
        action = trigger_action(context, config["trigger"])
        if action is None:
            continue
        symbol = context["symbol"]
        if context["decision_time"] < next_allowed[symbol]:
            continue
        event = event_from_context(context, action)
        events.append(event)
        next_allowed[symbol] = context["market_outcomes"]["24h"][
            "exit_time"
        ]
    return events


def metric(events, config, split, horizon, z):
    passthrough = {
        "name": config["name"],
        "orientation": "FOLLOW",
        "context_action": "ALL",
        "family": config["trigger"],
        "min_abs_score": 0,
        "min_abs_momentum_24h_pct": 0.0,
        "normal_market_only": False,
    }
    row = statistics_lab.metric(
        events,
        passthrough,
        split,
        horizon,
        z,
    )
    row.update({
        "lab_version": LAB_VERSION,
        "source_replay_version": SOURCE_VERSION,
        "config": config["name"],
        "trigger": config["trigger"],
        "volume_ratio_min": config["volume_ratio_min"],
        "normal_market_only": int(config["normal_market_only"]),
        "cooldown_hours": COOLDOWN_HOURS,
    })
    return row


def eligible(row, minimum_events, minimum_blocks):
    return (
        row["events"] >= minimum_events
        and row["time_blocks"] >= minimum_blocks
        and row["block_mean_lower_bound_pct"] > 0.0
    )


def rank_pair(learn, validation):
    return (
        min(
            learn["block_mean_lower_bound_pct"],
            validation["block_mean_lower_bound_pct"],
        ),
        validation["block_mean_lower_bound_pct"],
        validation["events"],
    )


def select_horizon(events_by_config, horizon, configs=CONFIGS):
    by_name = {config["name"]: config for config in configs}
    detail = []
    learn_rows = []
    for config in configs:
        row = metric(
            events_by_config[config["name"]],
            config,
            "LEARN",
            horizon,
            LEARN_Z,
        )
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

    validation_pairs = []
    for learn in learn_rows[:SHORTLIST_SIZE]:
        config = by_name[learn["config"]]
        row = metric(
            events_by_config[config["name"]],
            config,
            "VALIDATION",
            horizon,
            VALIDATION_Z,
        )
        detail.append(row)
        if eligible(
            row,
            MIN_VALIDATION_EVENTS,
            MIN_VALIDATION_BLOCKS,
        ):
            validation_pairs.append((learn, row))

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
            "status": "NO_VALIDATED_TRANSITION",
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
        key=lambda pair: rank_pair(pair[0], pair[1]),
    )
    config = by_name[learn["config"]]

    # Transition-specific EXAM is touched only after this freeze.
    exam = metric(
        events_by_config[config["name"]],
        config,
        "EXAM",
        horizon,
        EXAM_Z,
    )
    detail.append(exam)
    if (
        exam["events"] < MIN_EXAM_EVENTS
        or exam["time_blocks"] < MIN_EXAM_BLOCKS
    ):
        status = "EXAM_INSUFFICIENT"
    elif exam["block_mean_lower_bound_pct"] > 0.0:
        status = "HISTORICAL_TRANSITION_PASS"
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


def selection_row(result):
    row = {
        "lab_version": LAB_VERSION,
        "source_replay_version": SOURCE_VERSION,
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
        value = result[split]
        row[f"{split}_events"] = value["events"] if value else 0
        row[f"{split}_time_blocks"] = (
            value["time_blocks"] if value else 0
        )
        row[f"{split}_average_net_return_pct"] = (
            value["average_net_return_pct"] if value else 0.0
        )
        row[f"{split}_block_mean_lower_bound_pct"] = (
            value["block_mean_lower_bound_pct"] if value else 0.0
        )
    return row


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
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            extrasaction="ignore",
        )
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


def render(
    context_result,
    events_by_config,
    results,
    fetch_result,
):
    fetched = sum(item.get("fetched", 0) for item in fetch_result.values())
    fetch_errors = sum(
        item.get("status") == "ERROR" for item in fetch_result.values()
    )
    event_counts = [len(rows) for rows in events_by_config.values()]
    print("AG Crypto Analyst - Sparse Transition Replay Lab v1.0.0")
    print(
        f"Source: {SOURCE_VERSION} | fixed_configs={len(CONFIGS)}/horizon | "
        f"cooldown={COOLDOWN_HOURS}h | cost={replay.ROUND_TRIP_COST_BPS:.1f}bps"
    )
    print(
        f"Data: new_candles={fetched} | fetch_errors={fetch_errors} | "
        f"decision_points={context_result['decision_points']} | "
        f"risk_allowed_contexts={len(context_result['contexts'])}"
    )
    print(
        f"Risk blocks={context_result['risk_blocks']} | "
        f"point-in-time gaps={context_result['source_errors']} | "
        f"events/config={min(event_counts)}..{max(event_counts)}"
    )
    print(
        "Triggers: score cross, MACD/EMA zero cross, closed-price 24h "
        "breakout; volume and NORMAL-market guards are fixed variants."
    )
    print(
        "Discipline: LEARN z=3.25 -> top 3 -> VALIDATION z=2.40 "
        "-> frozen winner -> transition-specific EXAM z=1.96"
    )

    for result in results:
        print(
            f"\n{result['horizon'].upper()} | "
            f"qualified learn={result['learn_qualified']} "
            f"validation={result['validation_qualified']}"
        )
        if result["selected_config"] is None:
            print(f"result: {result['status']}")
            continue
        print(f"winner: {result['selected_config']['name']}")
        print(f"LEARN      {short_metric(result['learn'])}")
        print(f"VALIDATION {short_metric(result['validation'])}")
        print(f"EXAM       {short_metric(result['exam'])}")
        print(f"result: {result['status']}")

    passes = sum(
        result["status"] == "HISTORICAL_TRANSITION_PASS"
        for result in results
    )
    if passes:
        conclusion = (
            f"HISTORICAL_TRANSITION_CANDIDATE ({passes}/{len(results)})"
        )
        next_decision = "KEEP_SHADOW_AND_REQUIRE_FRESH_FORWARD"
    else:
        conclusion = "NO_CONFIRMED_PRICE_TECHNICAL_EDGE"
        next_decision = "STOP_PRICE_TECHNICAL_RULE_SEARCH"
    print(
        f"\nCONCLUSION: {conclusion} | product_state=SHADOW_ONLY"
    )
    print(f"NEXT_RESEARCH_DECISION: {next_decision}")
    print(
        "No live policy changed. A historical pass is not a product alert "
        "and still requires fresh forward evidence."
    )
    print(f"Details: {DETAIL_PATH}")
    print(f"Selection: {SELECTION_PATH}")


def main():
    replay_start, fetch_start, replay_end = replay.requested_range()
    replay.historical_market_data.DB_NAME = DB_NAME
    con = sqlite3.connect(DB_NAME)
    try:
        fetch_result = replay.ensure_historical_coverage(
            con,
            fetch_start,
            replay_end,
        )
        series = replay.build_series(con, fetch_start, replay_end)
        context_result = build_contexts(
            series,
            replay_start,
            replay_end,
        )
        contexts = context_result["contexts"]
        if not contexts:
            raise RuntimeError("No risk-allowed transition contexts")
        events_by_config = {
            config["name"]: events_for_config(contexts, config)
            for config in CONFIGS
        }
        results = [
            select_horizon(events_by_config, horizon)
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
        render(
            context_result,
            events_by_config,
            results,
            fetch_result,
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()
