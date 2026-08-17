"""Leakage-safe historical replay for the frozen Decision Engine policy.

The replay walks closed one-hour candles in chronological order. At every
decision timestamp it exposes only candles that were already closed, applies
the current Risk Manager and Decision Engine rules, then enters at the next
candle open. Outcomes are measured at 1h, 4h and 24h after estimated round-trip
costs.

This first version is a measurement lab. It does not tune thresholds, change
the live policy, create product alerts or invent historical social, on-chain,
macro and whale observations. Chronological LEARN, VALIDATION and EXAM slices
show whether the frozen price/risk policy is stable outside one period.
"""
from __future__ import annotations

import bisect
import csv
import math
import os
import sqlite3
from database.runtime_store import connect as runtime_connect
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analysis import forward_shadow_validator
from data import historical_market_data
from decision_engine import decision_engine
from risk_manager import risk_manager


DB_NAME = "crypto_data.db"
REPLAY_VERSION = "DECISION_REPLAY_V1"
POLICY_VERSION = decision_engine.POLICY_VERSION
SYMBOLS = tuple(risk_manager.SYMBOLS)
HORIZONS = {"1h": 1, "4h": 4, "24h": 24}
LOOKBACK_DAYS = int(os.getenv("AGCA_REPLAY_DAYS", "400"))
ROUND_TRIP_COST_BPS = float(os.getenv("AGCA_REPLAY_COST_BPS", "10"))
FETCH_HISTORY = os.getenv("AGCA_REPLAY_FETCH", "1").strip() != "0"
REPORT_PATH = Path("reports/decision_engine_historical_replay.csv")
SUMMARY_PATH = Path("reports/decision_engine_historical_summary.csv")
MIN_CLOSED_CANDLES = risk_manager.MIN_CLOSED_CANDLES
WARMUP_HOURS = MIN_CLOSED_CANDLES + 24
MAX_STALE_MINUTES = 90.0


def utcnow():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def parse_time(value):
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def pct_change(new_value, old_value):
    if float(old_value) <= 0:
        raise ValueError("percentage change requires a positive base")
    return (float(new_value) / float(old_value) - 1.0) * 100.0


def requested_range(now=None):
    now = (now or utcnow()).astimezone(timezone.utc)
    replay_end = now.replace(minute=0, second=0, microsecond=0)
    replay_start = replay_end - timedelta(days=LOOKBACK_DAYS)
    fetch_start = replay_start - timedelta(hours=WARMUP_HOURS)
    return replay_start, fetch_start, replay_end


def ensure_fetch_state(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS historical_replay_fetch_state (
            symbol TEXT PRIMARY KEY,
            requested_start TEXT NOT NULL,
            requested_end TEXT NOT NULL,
            fetched_rows INTEGER NOT NULL,
            completed_at TEXT NOT NULL
        )
    """)
    con.commit()


def fetch_state_covers(con, symbol, start, end):
    row = con.execute("""
        SELECT requested_start, requested_end
        FROM historical_replay_fetch_state
        WHERE symbol=?
    """, (symbol,)).fetchone()
    if not row:
        return False
    return parse_time(row[0]) <= start and parse_time(row[1]) >= end


def save_fetch_state(con, symbol, start, end, fetched_rows):
    con.execute("""
        INSERT INTO historical_replay_fetch_state (
            symbol, requested_start, requested_end, fetched_rows, completed_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            requested_start=excluded.requested_start,
            requested_end=excluded.requested_end,
            fetched_rows=excluded.fetched_rows,
            completed_at=excluded.completed_at
    """, (symbol, iso(start), iso(end), int(fetched_rows), iso(utcnow())))
    con.commit()


def stored_coverage(con, symbol):
    row = con.execute("""
        SELECT MIN(open_time), MAX(close_time), COUNT(*),
               SUM(CASE WHEN quote_volume IS NULL THEN 1 ELSE 0 END)
        FROM market_klines
        WHERE symbol=? AND interval='1h'
    """, (symbol,)).fetchone()
    if not row or not row[0] or not row[1]:
        return None
    return {
        "start": parse_time(row[0]),
        "end": parse_time(row[1]),
        "rows": int(row[2]),
        "missing_quote_volume": int(row[3] or 0),
    }


def ensure_historical_coverage(con, fetch_start, replay_end):
    historical_market_data.DB_NAME = DB_NAME
    historical_market_data.create_market_klines_table()
    ensure_fetch_state(con)
    results = {}
    if not FETCH_HISTORY:
        for symbol in SYMBOLS:
            results[symbol] = {"status": "FETCH_DISABLED", "fetched": 0}
        return results

    start_ms = int(fetch_start.timestamp() * 1000)
    end_ms = int(replay_end.timestamp() * 1000)
    tolerance = timedelta(hours=2)
    for symbol in SYMBOLS:
        coverage = stored_coverage(con, symbol)
        already_complete = (
            coverage
            and coverage["start"] <= fetch_start + tolerance
            and coverage["end"] >= replay_end - tolerance
            and coverage["missing_quote_volume"] == 0
        )
        state_complete = fetch_state_covers(
            con,
            symbol,
            fetch_start,
            replay_end,
        )
        if already_complete or (
            state_complete
            and (
                coverage is None
                or coverage["missing_quote_volume"] == 0
            )
        ):
            results[symbol] = {"status": "READY", "fetched": 0}
            continue
        try:
            fetched = historical_market_data.fetch_symbol_history(
                symbol,
                start_ms,
                end_ms,
            )
            save_fetch_state(
                con,
                symbol,
                fetch_start,
                replay_end,
                fetched,
            )
            results[symbol] = {"status": "FETCHED", "fetched": fetched}
        except Exception as exc:
            results[symbol] = {
                "status": "ERROR",
                "fetched": 0,
                "error": str(exc),
            }
    return results


def load_candles(con, symbol, start, end):
    rows = con.execute("""
        SELECT open_time, close_time, open, high, low, close, volume,
               quote_volume
        FROM market_klines
        WHERE symbol=?
          AND interval='1h'
          AND close_time>=?
          AND close_time<=?
        ORDER BY open_time
    """, (symbol, iso(start), iso(end))).fetchall()
    candles = []
    for row in rows:
        candle = {
            "open_time": parse_time(row[0]),
            "close_time": parse_time(row[1]),
            "open": float(row[2]),
            "high": float(row[3]),
            "low": float(row[4]),
            "close": float(row[5]),
            "volume": float(row[6]),
            "quote_volume": (
                float(row[7]) if row[7] is not None else None
            ),
        }
        if (
            candle["open"] > 0
            and candle["high"] > 0
            and candle["low"] > 0
            and candle["close"] > 0
            and candle["volume"] >= 0
        ):
            candles.append(candle)
    return candles


def build_series(con, fetch_start, replay_end):
    output = {}
    for symbol in SYMBOLS:
        candles = load_candles(con, symbol, fetch_start, replay_end)
        output[symbol] = {
            "candles": candles,
            "close_times": [row["close_time"] for row in candles],
        }
    return output


def coverage_summary(series):
    result = []
    for symbol in SYMBOLS:
        candles = series[symbol]["candles"]
        result.append({
            "symbol": symbol,
            "candles": len(candles),
            "start": iso(candles[0]["open_time"]) if candles else None,
            "end": iso(candles[-1]["close_time"]) if candles else None,
        })
    return result


def aligned_index(series_item, decision_time):
    close_times = series_item["close_times"]
    index = bisect.bisect_right(close_times, decision_time) - 1
    if index < 0:
        return None
    stale_minutes = (
        decision_time - close_times[index]
    ).total_seconds() / 60.0
    if stale_minutes < 0 or stale_minutes > MAX_STALE_MINUTES:
        return None
    return index


def require_hourly_continuity(candles, start_index, end_index):
    if start_index < 0 or end_index >= len(candles):
        raise ValueError("candle window is outside stored history")
    for position in range(start_index + 1, end_index + 1):
        gap = (
            candles[position]["open_time"]
            - candles[position - 1]["open_time"]
        ).total_seconds()
        if not 3599.0 <= gap <= 3601.0:
            raise ValueError("non-contiguous hourly candle window")


def historical_metrics(symbol, candles, index):
    first = index - MIN_CLOSED_CANDLES + 1
    if first < 0:
        raise ValueError("insufficient closed candles for risk")
    require_hourly_continuity(candles, first, index)
    window = candles[first:index + 1]
    closes = [row["close"] for row in window]
    last_price = closes[-1]
    recent_24 = window[-24:]
    return_24h = pct_change(last_price, closes[-25])
    high_24h = max(row["high"] for row in recent_24)
    low_24h = min(row["low"] for row in recent_24)
    range_24h = pct_change(high_24h, low_24h)
    hourly_returns = [
        pct_change(closes[position], closes[position - 1])
        for position in range(len(closes) - 24, len(closes))
    ]
    hourly_volatility = statistics.pstdev(hourly_returns)
    peak_7d = max(closes[-168:])
    drawdown_7d = pct_change(last_price, peak_7d)

    quote_volume_complete = all(
        row.get("quote_volume") is not None for row in recent_24
    )
    if quote_volume_complete:
        quote_volume = sum(row["quote_volume"] for row in recent_24)
        quote_volume_method = "BINANCE_QUOTE_VOLUME"
    else:
        # Backward-compatible fallback for old rows not refreshed yet.
        quote_volume = sum(
            ((row["high"] + row["low"] + row["close"]) / 3.0)
            * row["volume"]
            for row in recent_24
        )
        quote_volume_method = "TYPICAL_PRICE_X_BASE_VOLUME_PROXY"
    return {
        "symbol": symbol,
        "as_of": iso(window[-1]["close_time"]),
        "price": last_price,
        "return_24h_pct": return_24h,
        "range_24h_pct": range_24h,
        "hourly_volatility_pct": hourly_volatility,
        "drawdown_7d_pct": drawdown_7d,
        "quote_volume_24h": quote_volume,
        "quote_volume_method": quote_volume_method,
    }


def technical_features_at(candles, index):
    first = max(0, index - 99)
    window = candles[first:index + 1]
    if len(window) < 60:
        raise ValueError("insufficient closed candles for technical features")
    closes = [row["close"] for row in window]
    volumes = [row["volume"] for row in window]

    ema12 = forward_shadow_validator.ema_series(closes, 12)
    ema26 = forward_shadow_validator.ema_series(closes, 26)
    common = min(len(ema12), len(ema26))
    macd_line = [
        ema12[-common + position] - ema26[-common + position]
        for position in range(common)
    ]
    signal = forward_shadow_validator.ema_series(macd_line, 9)
    histogram = macd_line[-1] - signal[-1] if signal else 0.0
    ema20 = forward_shadow_validator.ema_series(closes, 20)
    ema50 = forward_shadow_validator.ema_series(closes, 50)
    ema_spread_pct = pct_change(ema20[-1], ema50[-1])
    ret6 = pct_change(closes[-1], closes[-7])
    previous_volume = volumes[-21:-1]
    average_volume = (
        sum(previous_volume) / len(previous_volume)
        if previous_volume and sum(previous_volume)
        else 0.0
    )
    volume_ratio = volumes[-1] / average_volume if average_volume else 1.0
    features = {
        "rsi": forward_shadow_validator.rsi(closes),
        "histogram": histogram,
        "ema_spread_pct": ema_spread_pct,
        "ret6": ret6,
        "volume_ratio": volume_ratio,
    }
    return decision_engine.classify_technical_features(features)


def risk_for_timestamp(series, decision_time):
    assets = []
    source_errors = {}
    indexes = {}
    for symbol in SYMBOLS:
        item = series[symbol]
        index = aligned_index(item, decision_time)
        indexes[symbol] = index
        if index is None:
            source_errors[symbol] = "NO_ALIGNED_CLOSED_CANDLE"
            continue
        try:
            metrics = historical_metrics(symbol, item["candles"], index)
            assets.append(risk_manager.classify_asset_risk(metrics))
        except Exception as exc:
            source_errors[symbol] = str(exc)

    policy = risk_manager.build_global_policy(
        assets,
        source_errors,
        created_at=iso(decision_time),
    )
    return policy, {row["symbol"]: row for row in assets}, indexes, source_errors


def candidate_outcomes(candles, index, action):
    entry_index = index + 1
    final_index = index + max(HORIZONS.values())
    if entry_index >= len(candles) or final_index >= len(candles):
        return None
    require_hourly_continuity(
        candles,
        index,
        final_index,
    )
    entry = candles[entry_index]
    entry_price = entry["open"]
    output = {
        "entry_time": entry["open_time"],
        "entry_price": entry_price,
    }
    for label, hours in HORIZONS.items():
        exit_index = index + hours
        if exit_index >= len(candles):
            return None
        exit_candle = candles[exit_index]
        market_return = pct_change(exit_candle["close"], entry_price)
        gross_directional = (
            market_return if action == "AL" else -market_return
        )
        net_directional = gross_directional - ROUND_TRIP_COST_BPS / 100.0
        output[label] = {
            "exit_time": exit_candle["close_time"],
            "exit_price": exit_candle["close"],
            "market_return_pct": market_return,
            "gross_directional_return_pct": gross_directional,
            "net_directional_return_pct": net_directional,
            "hit_after_cost": int(net_directional > 0),
        }
    return output


def replay(series, replay_start, replay_end):
    btc = series.get("BTCUSDT", {}).get("candles", [])
    if len(btc) < MIN_CLOSED_CANDLES + max(HORIZONS.values()):
        return {
            "rows": [],
            "decision_points": 0,
            "risk_blocks": 0,
            "overlap_blocks": 0,
            "source_errors": 1,
        }

    rows = []
    decision_points = 0
    risk_blocks = 0
    overlap_blocks = 0
    source_error_count = 0
    next_allowed = {symbol: datetime.min.replace(tzinfo=timezone.utc)
                    for symbol in SYMBOLS}
    max_horizon = max(HORIZONS.values())

    for btc_index in range(
        MIN_CLOSED_CANDLES - 1,
        len(btc) - max_horizon,
    ):
        decision_time = btc[btc_index]["close_time"]
        if decision_time < replay_start or decision_time >= replay_end:
            continue
        decision_points += 1
        policy, assets, indexes, errors = risk_for_timestamp(
            series,
            decision_time,
        )
        source_error_count += len(errors)

        for symbol in SYMBOLS:
            asset = assets.get(symbol)
            index = indexes.get(symbol)
            if asset is None or index is None:
                continue
            risk = decision_engine.risk_guard(
                policy,
                asset,
                decision_time,
            )
            if risk["decision"] != "ALLOW":
                risk_blocks += 1
                continue
            try:
                technical = technical_features_at(
                    series[symbol]["candles"],
                    index,
                )
            except Exception:
                source_error_count += 1
                continue
            action, reasons = decision_engine.choose_shadow_action(
                asset,
                technical,
                None,
                None,
                risk,
            )
            if action not in {"AL", "SAT"}:
                continue
            if decision_time < next_allowed[symbol]:
                overlap_blocks += 1
                continue
            try:
                outcomes = candidate_outcomes(
                    series[symbol]["candles"],
                    index,
                    action,
                )
            except ValueError:
                source_error_count += 1
                continue
            if not outcomes:
                continue

            next_allowed[symbol] = outcomes["1h"]["exit_time"]
            row = {
                "replay_version": REPLAY_VERSION,
                "policy_version": POLICY_VERSION,
                "decision_time": decision_time,
                "entry_time": outcomes["entry_time"],
                "symbol": symbol,
                "action": action,
                "technical_direction": technical["direction"],
                "technical_score": technical["score"],
                "market_return_24h_pct": asset["return_24h_pct"],
                "risk_market_state": policy["market_state"],
                "risk_tier": asset["risk_tier"],
                "risk_cap_pct": risk["max_position_pct"],
                "quote_volume_24h": asset["quote_volume_24h"],
                "quote_volume_method": asset["quote_volume_method"],
                "action_reasons": ",".join(reasons),
                "entry_price": outcomes["entry_price"],
            }
            for label in HORIZONS:
                outcome = outcomes[label]
                row.update({
                    f"{label}_exit_time": outcome["exit_time"],
                    f"{label}_exit_price": outcome["exit_price"],
                    f"{label}_market_return_pct":
                        outcome["market_return_pct"],
                    f"{label}_gross_directional_return_pct":
                        outcome["gross_directional_return_pct"],
                    f"{label}_net_directional_return_pct":
                        outcome["net_directional_return_pct"],
                    f"{label}_hit_after_cost":
                        outcome["hit_after_cost"],
                })
            rows.append(row)

    return {
        "rows": rows,
        "decision_points": decision_points,
        "risk_blocks": risk_blocks,
        "overlap_blocks": overlap_blocks,
        "source_errors": source_error_count,
    }


def split_boundaries(replay_start, replay_end):
    span = replay_end - replay_start
    return {
        "learn_end": replay_start + span * 0.60,
        "validation_end": replay_start + span * 0.80,
    }


def assign_splits(rows, replay_start, replay_end):
    boundaries = split_boundaries(replay_start, replay_end)
    for row in rows:
        timestamp = row["entry_time"]
        if timestamp < boundaries["learn_end"]:
            row["split"] = "LEARN"
        elif timestamp < boundaries["validation_end"]:
            row["split"] = "VALIDATION"
        else:
            row["split"] = "EXAM"
    return boundaries


def wilson_lower(hits, count, z=1.959963984540054):
    if count <= 0:
        return 0.0
    proportion = hits / count
    denominator = 1.0 + z * z / count
    centre = proportion + z * z / (2.0 * count)
    adjustment = z * math.sqrt(
        (proportion * (1.0 - proportion) + z * z / (4.0 * count))
        / count
    )
    return max(0.0, (centre - adjustment) / denominator) * 100.0


def metric_row(rows, split, action, horizon):
    selected = [
        row for row in rows
        if (split == "ALL" or row["split"] == split)
        and (action == "ALL" or row["action"] == action)
    ]
    values = [
        float(row[f"{horizon}_net_directional_return_pct"])
        for row in selected
    ]
    hits = sum(int(value > 0) for value in values)
    positives = sum(value for value in values if value > 0)
    negatives = abs(sum(value for value in values if value < 0))
    running = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        running += value
        peak = max(peak, running)
        max_drawdown = min(max_drawdown, running - peak)
    return {
        "replay_version": REPLAY_VERSION,
        "policy_version": POLICY_VERSION,
        "split": split,
        "action": action,
        "horizon": horizon,
        "events": len(values),
        "hits": hits,
        "hit_rate_pct": hits / len(values) * 100.0 if values else 0.0,
        "wilson_lower_pct": wilson_lower(hits, len(values)),
        "average_net_return_pct":
            statistics.fmean(values) if values else 0.0,
        "median_net_return_pct":
            statistics.median(values) if values else 0.0,
        "cumulative_net_return_pct_points": sum(values),
        "profit_factor": positives / negatives if negatives else (
            None if not positives else float("inf")
        ),
        "max_drawdown_pct_points": max_drawdown,
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
    }


def build_summary(rows):
    output = []
    for split in ("LEARN", "VALIDATION", "EXAM", "ALL"):
        for action in ("ALL", "AL", "SAT"):
            for horizon in HORIZONS:
                output.append(metric_row(rows, split, action, horizon))
    return output


def serializable(value):
    if isinstance(value, datetime):
        return iso(value)
    return value


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({
            key: serializable(value)
            for key, value in row.items()
        } for row in rows)


def display_metric(row, display_group=None):
    display_group = display_group or row["split"]
    return (
        f"{display_group:<10} | {row['horizon']:<3} | "
        f"n={row['events']:<5} | hit={row['hit_rate_pct']:>5.1f}% | "
        f"wilson={row['wilson_lower_pct']:>5.1f}% | "
        f"net={row['average_net_return_pct']:+.3f}% | "
        f"seqDD={row['max_drawdown_pct_points']:+.2f}pp"
    )


def render(result, coverage, summary, fetch_result, boundaries):
    print("AG Crypto Analyst - Decision Engine Historical Replay v1.0.0")
    print(
        f"Policy: {POLICY_VERSION} | replay={REPLAY_VERSION} | "
        f"cost={ROUND_TRIP_COST_BPS:.1f} bps | candles=CLOSED_1H_ONLY"
    )
    fetched = sum(item.get("fetched", 0) for item in fetch_result.values())
    fetch_errors = sum(
        item.get("status") == "ERROR" for item in fetch_result.values()
    )
    ready_symbols = sum(
        item["candles"] >= MIN_CLOSED_CANDLES + max(HORIZONS.values())
        for item in coverage
    )
    print(
        f"Data: ready={ready_symbols}/{len(SYMBOLS)} | "
        f"new_candles={fetched} | fetch_errors={fetch_errors}"
    )
    print(
        "Splits: "
        f"LEARN < {iso(boundaries['learn_end'])} | "
        f"VALIDATION < {iso(boundaries['validation_end'])} | "
        "EXAM = remaining chronology"
    )
    print(
        f"Replay: decision_points={result['decision_points']} | "
        f"candidates={len(result['rows'])} | "
        f"AL={sum(row['action'] == 'AL' for row in result['rows'])} | "
        f"SAT={sum(row['action'] == 'SAT' for row in result['rows'])} | "
        f"risk_blocks={result['risk_blocks']} | "
        f"overlap_blocks={result['overlap_blocks']}"
    )
    print(f"Point-in-time source gaps: {result['source_errors']}")
    print(
        "Volume: Binance quote volume; unavailable legacy rows use an "
        "explicit typical-price x base-volume fallback."
    )
    if not summary:
        print("No measurable historical candidate was produced.")
        return

    print("\nCHRONOLOGICAL OUT-OF-SAMPLE VIEW | action=ALL")
    for split in ("LEARN", "VALIDATION", "EXAM"):
        for horizon in HORIZONS:
            row = next(
                item for item in summary
                if item["split"] == split
                and item["action"] == "ALL"
                and item["horizon"] == horizon
            )
            print(display_metric(row))

    print("\nFULL WINDOW ACTION VIEW")
    for action in ("AL", "SAT"):
        for horizon in HORIZONS:
            row = next(
                item for item in summary
                if item["split"] == "ALL"
                and item["action"] == action
                and item["horizon"] == horizon
            )
            print(display_metric(row, action))
    print(
        "\nStatus: SHADOW_ONLY. No threshold was tuned and the live policy "
        "was not changed."
    )
    print(
        "seqDD is additive signal-sequence drawdown, not portfolio "
        "drawdown."
    )
    print(f"Events: {REPORT_PATH}")
    print(f"Summary: {SUMMARY_PATH}")


def main():
    if LOOKBACK_DAYS < 30:
        raise ValueError("AGCA_REPLAY_DAYS must be at least 30")
    if ROUND_TRIP_COST_BPS < 0:
        raise ValueError("AGCA_REPLAY_COST_BPS cannot be negative")

    replay_start, fetch_start, replay_end = requested_range()
    historical_market_data.DB_NAME = DB_NAME
    con = runtime_connect(DB_NAME)
    try:
        fetch_result = ensure_historical_coverage(
            con,
            fetch_start,
            replay_end,
        )
        series = build_series(con, fetch_start, replay_end)
        coverage = coverage_summary(series)
        result = replay(series, replay_start, replay_end)
        boundaries = assign_splits(
            result["rows"],
            replay_start,
            replay_end,
        )
        summary = build_summary(result["rows"]) if result["rows"] else []
        write_csv(REPORT_PATH, result["rows"])
        write_csv(SUMMARY_PATH, summary)
        render(result, coverage, summary, fetch_result, boundaries)
    finally:
        con.close()


if __name__ == "__main__":
    main()
