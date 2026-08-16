"""Unified probabilistic forecast runtime for AG Crypto Analyst.

This is the product-facing shadow engine, not another research lab.  It freezes
one existing audited model specification, emits 13 symbols x 3 horizons, stores
every forecast, and settles due forecasts without using future data at creation.
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


DB_NAME = "crypto_data.db"
ENGINE_VERSION = "UNIFIED_FORECAST_ENGINE_V1"
MODEL_VERSION = "POOLED_RIDGE_DISTRIBUTION_RUNTIME_V1_FIXED"
MODEL_PATH = Path("reports/unified_forecast_model.pkl")
MODEL_TTL = timedelta(days=30)
MAX_FORECAST_AGE = timedelta(minutes=15)
HORIZONS = (1, 4, 24)
MIN_TRAIN_ROWS = 1000


def utcnow():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def parse_time(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def connect():
    con = sqlite3.connect(DB_NAME, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def ensure_tables(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS forecast_engine_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            model_version TEXT NOT NULL,
            artifact_hash TEXT NOT NULL,
            training_end TEXT NOT NULL,
            training_rows INTEGER NOT NULL,
            forecast_count INTEGER NOT NULL,
            no_forecast_count INTEGER NOT NULL,
            source_errors_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS forecast_engine_predictions (
            forecast_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            model_version TEXT NOT NULL,
            artifact_hash TEXT NOT NULL,
            symbol TEXT NOT NULL,
            as_of TEXT NOT NULL,
            feature_cutoff TEXT NOT NULL,
            due_time TEXT NOT NULL,
            horizon_hours INTEGER NOT NULL,
            current_price REAL NOT NULL,
            target_price REAL NOT NULL,
            price_p10 REAL NOT NULL,
            price_p90 REAL NOT NULL,
            p_up REAL NOT NULL,
            p_down REAL NOT NULL,
            p_neutral REAL NOT NULL,
            predicted_return_pct REAL NOT NULL,
            expected_market_return_pct REAL NOT NULL,
            action_utility_pct REAL NOT NULL,
            shadow_action TEXT NOT NULL,
            risk_adjusted_action TEXT NOT NULL,
            risk_eligible INTEGER NOT NULL,
            risk_state TEXT NOT NULL,
            risk_cap_pct REAL NOT NULL,
            calibration_state TEXT NOT NULL,
            production_eligible INTEGER NOT NULL,
            reason_codes_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            entry_price REAL,
            exit_price REAL,
            actual_return_pct REAL,
            actual_class TEXT,
            absolute_error_pct REAL,
            interval_hit INTEGER,
            brier_score REAL,
            direction_hit INTEGER,
            action_hit INTEGER,
            action_net_utility_pct REAL,
            settled_at TEXT,
            UNIQUE(model_version, artifact_hash, symbol, as_of, horizon_hours)
        );

        CREATE INDEX IF NOT EXISTS idx_forecast_open_due
        ON forecast_engine_predictions(status, due_time);
        CREATE INDEX IF NOT EXISTS idx_forecast_symbol_horizon
        ON forecast_engine_predictions(symbol, horizon_hours, as_of);
    """)
    con.commit()


def artifact_hash(payload):
    raw = pickle.dumps(payload, protocol=4)
    return hashlib.sha256(raw).hexdigest()


def load_cached_model(now):
    if not MODEL_PATH.exists():
        return None
    try:
        payload = pickle.loads(MODEL_PATH.read_bytes())
        created = parse_time(payload["created_at"])
        expected = payload.get("artifact_hash")
        unsigned = dict(payload)
        unsigned.pop("artifact_hash", None)
        if now - created >= MODEL_TTL or artifact_hash(unsigned) != expected:
            return None
        if set(map(int, payload["models"].keys())) != set(HORIZONS):
            return None
        return payload
    except Exception:
        return None


def save_model(payload):
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    unsigned = dict(payload)
    unsigned.pop("artifact_hash", None)
    payload["artifact_hash"] = artifact_hash(unsigned)
    temporary = MODEL_PATH.with_suffix(".tmp")
    temporary.write_bytes(pickle.dumps(payload, protocol=4))
    temporary.replace(MODEL_PATH)
    return payload


def train_model(series, start, end, now):
    from analysis import forecast_core_lab as core

    built = core.build_rows(series, start, end)
    rows = sorted(built["rows"], key=lambda row: (row["as_of"], row["symbol"], row["horizon"]))
    if not rows:
        raise RuntimeError("NO_TRAINING_ROWS")
    cutoff = start + (end - start) * 0.80
    fit_rows = [row for row in rows if row["due_time"] < cutoff]
    calibration_rows = [row for row in rows if row["as_of"] >= cutoff]
    if len(fit_rows) < MIN_TRAIN_ROWS or len(calibration_rows) < MIN_TRAIN_ROWS:
        raise RuntimeError("INSUFFICIENT_CHRONOLOGICAL_TRAINING_ROWS")
    models = {horizon: core.fit_model(fit_rows, calibration_rows, horizon)
              for horizon in HORIZONS}
    payload = {
        "engine_version": ENGINE_VERSION,
        "model_version": MODEL_VERSION,
        "created_at": iso(now),
        "training_start": iso(start),
        "training_end": iso(end),
        "fit_rows": len(fit_rows),
        "calibration_rows": len(calibration_rows),
        "source_gaps": int(built["source_gaps"]),
        "models": models,
    }
    return save_model(payload)


def latest_common_close(series, end):
    common = None
    for item in series.values():
        times = {value for value in item["by_close"] if value <= end}
        common = times if common is None else common.intersection(times)
    if not common:
        raise RuntimeError("NO_COMMON_CLOSED_1H_CANDLE")
    return max(common)


def validate_forecast_freshness(decision_time, now):
    age = now - decision_time
    if age < timedelta(0) or age > MAX_FORECAST_AGE:
        raise RuntimeError(
            f"STALE_COMMON_CANDLE:{age.total_seconds() / 60.0:.1f}m>15m; "
            "next cycle will retry after a fresh hourly close"
        )
    if decision_time + timedelta(hours=1) <= now:
        raise RuntimeError("STALE_COMMON_CANDLE_WOULD_CREATE_RETROSPECTIVE_FORECAST")
    return age


def latest_risk_snapshot(con, now, symbols):
    """Read the Risk Manager result; never recreate or loosen it here."""
    policy = con.execute("""
        SELECT id, created_at, market_state, new_positions_allowed
        FROM risk_policy_snapshots ORDER BY id DESC LIMIT 1
    """).fetchone()
    blocked = {
        symbol: {"new_position_allowed": False, "max_position_pct": 0.0}
        for symbol in symbols
    }
    if not policy:
        return {"market_state": "DATA_BLOCKED", "new_positions_allowed": False}, blocked, "NO_RISK_SNAPSHOT"
    age = now - parse_time(policy[1])
    if age < timedelta(0) or age > timedelta(minutes=30):
        return {"market_state": "DATA_BLOCKED", "new_positions_allowed": False}, blocked, "STALE_RISK_SNAPSHOT"
    rows = con.execute("""
        SELECT symbol, new_position_allowed, max_position_pct
        FROM asset_risk_snapshots WHERE policy_snapshot_id=?
    """, (policy[0],)).fetchall()
    assets = {row[0]: {"new_position_allowed": bool(row[1]), "max_position_pct": float(row[2])}
              for row in rows}
    if set(assets) != set(symbols):
        return {"market_state": "DATA_BLOCKED", "new_positions_allowed": False}, blocked, "INCOMPLETE_RISK_SNAPSHOT"
    return {"market_state": str(policy[2]), "new_positions_allowed": bool(policy[3])}, assets, None


def live_rows(con, series, decision_time, now):
    from analysis import forecast_core_lab as core

    policy, assets, risk_error = latest_risk_snapshot(con, now, core.SYMBOLS)
    indexes = {symbol: item["by_close"].get(decision_time) for symbol, item in series.items()}
    errors = {symbol: "NO_EXACT_CLOSED_CANDLE" for symbol, index in indexes.items() if index is None}
    btc = series["BTCUSDT"]["candles"]
    btc_index = indexes.get("BTCUSDT")
    rows = []
    no_forecast = []
    for symbol in core.SYMBOLS:
        index = indexes.get(symbol)
        asset = assets.get(symbol)
        if index is None or asset is None or btc_index is None:
            no_forecast.extend({"symbol": symbol, "horizon": h, "reason": errors.get(symbol, "NO_EXACT_DATA")}
                               for h in HORIZONS)
            continue
        try:
            candles = series[symbol]["candles"]
            features = core.feature_values(symbol, candles, index, btc, btc_index)
            current = float(candles[index]["close"])
            risk_eligible = bool(not risk_error and policy["new_positions_allowed"] and asset["new_position_allowed"]
                                 and float(asset["max_position_pct"]) > 0)
            btc_threshold = max(0.50, float(features[11]) * math.sqrt(24.0))
            regime = "UP" if features[10] > btc_threshold else (
                "DOWN" if features[10] < -btc_threshold else "SIDEWAYS")
            for horizon in HORIZONS:
                rows.append({
                    "symbol": symbol,
                    "as_of": decision_time,
                    "feature_cutoff": decision_time,
                    "due_time": decision_time + timedelta(hours=horizon),
                    "horizon": horizon,
                    "current_price": current,
                    "trade_return_pct": 0.0,
                    "scale_pct": max(float(features[4]) * math.sqrt(float(horizon)), 0.10),
                    "features": features,
                    "risk_state": str(policy["market_state"]),
                    "risk_cap_pct": float(asset["max_position_pct"]),
                    "risk_eligible": risk_eligible,
                    "regime": regime,
                })
        except Exception as exc:
            no_forecast.extend({"symbol": symbol, "horizon": h, "reason": str(exc)}
                               for h in HORIZONS)
    return rows, no_forecast


def validate_contract(prediction, model_payload):
    from analysis import forecast_core_lab as core
    from decision_engine.forecast_contract import (
        ENTRY_RULE, PROBABILITY_EVENT, REFERENCE_PRICE_BASIS, build_forecast_record,
    )

    action = prediction["action"]
    reasons = ["FORWARD_SHADOW", "HISTORICALLY_UNVALIDATED_MODEL"]
    if action == "BEKLE":
        reasons.append("ACTION_PROBABILITY_BELOW_70PCT")
    if action != prediction["risk_adjusted_action"]:
        reasons.append("RISK_VETO")
    record = build_forecast_record(
        symbol=prediction["symbol"],
        as_of=iso(prediction["as_of"]),
        feature_cutoff=iso(prediction["feature_cutoff"]),
        due_time=iso(prediction["due_time"]),
        horizon_hours=int(prediction["horizon"]),
        current_price=float(prediction["current_price"]),
        target_price=float(prediction["target_price"]),
        price_p10=float(prediction["price_p10"]),
        price_p90=float(prediction["price_p90"]),
        interval_probability=core.INTERVAL_PROBABILITY,
        round_trip_cost_bps=core.ROUND_TRIP_COST_BPS,
        minimum_move_bps=core.MINIMUM_MOVE_BPS,
        action_probability_threshold=core.ACTION_PROBABILITY,
        target_reference=REFERENCE_PRICE_BASIS,
        entry_rule=ENTRY_RULE,
        probability_event=PROBABILITY_EVENT,
        p_up_net=float(prediction["p_up"]),
        p_down_net=float(prediction["p_down"]),
        p_neutral=float(prediction["p_neutral"]),
        expected_net_return_pct=float(prediction["expected_net_return_pct"]),
        shadow_action=action,
        risk_adjusted_action=prediction["risk_adjusted_action"],
        risk_eligible=bool(prediction["risk_eligible"]),
        risk_state=str(prediction["risk_state"]),
        risk_cap_pct=float(prediction["risk_cap_pct"]),
        model_version=MODEL_VERSION,
        artifact_hash=model_payload["artifact_hash"],
        calibration_state="FORWARD_SHADOW",
        production_eligible=False,
        reason_codes=reasons,
    )
    return record, reasons


def create_forecasts(con, model_payload, series, decision_time, now):
    from analysis import forecast_core_lab as core

    rows, no_forecast = live_rows(con, series, decision_time, now)
    forecasts = []
    for row in rows:
        try:
            prediction = core.forecast_row(model_payload["models"][row["horizon"]], row)
            record, reasons = validate_contract(prediction, model_payload)
            signed_expected = float(prediction["expected_trade_return_pct"])
            action_utility = float(prediction["expected_net_return_pct"])
            values = record.to_dict()
            con.execute("""
                INSERT OR IGNORE INTO forecast_engine_predictions(
                    created_at, model_version, artifact_hash, symbol, as_of,
                    feature_cutoff, due_time, horizon_hours, current_price,
                    target_price, price_p10, price_p90, p_up, p_down, p_neutral,
                    predicted_return_pct, expected_market_return_pct,
                    action_utility_pct, shadow_action, risk_adjusted_action,
                    risk_eligible, risk_state, risk_cap_pct, calibration_state,
                    production_eligible, reason_codes_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                iso(now), MODEL_VERSION, model_payload["artifact_hash"],
                values["symbol"], values["as_of"], values["feature_cutoff"],
                values["due_time"], values["horizon_hours"], values["current_price"],
                values["target_price"], values["price_p10"], values["price_p90"],
                values["p_up_net"], values["p_down_net"], values["p_neutral"],
                float(prediction["predicted_return_pct"]), signed_expected,
                action_utility, values["shadow_action"], values["risk_adjusted_action"],
                int(values["risk_eligible"]), values["risk_state"], values["risk_cap_pct"],
                values["calibration_state"], 0, json.dumps(reasons, ensure_ascii=False),
            ))
            forecasts.append({**prediction, "reasons": reasons})
        except Exception as exc:
            no_forecast.append({"symbol": row["symbol"], "horizon": row["horizon"], "reason": str(exc)})
    expected_pairs = {(symbol, horizon) for symbol in core.SYMBOLS for horizon in HORIZONS}
    actual_pairs = {(row["symbol"], int(row["horizon"])) for row in forecasts}
    if not no_forecast and actual_pairs != expected_pairs:
        raise RuntimeError("FORECAST_BUNDLE_NOT_13X3")
    con.commit()
    stored = con.execute("""
        SELECT COUNT(*) FROM forecast_engine_predictions
        WHERE model_version=? AND artifact_hash=? AND as_of=?
    """, (MODEL_VERSION, model_payload["artifact_hash"], iso(decision_time))).fetchone()[0]
    if not no_forecast and int(stored) != len(expected_pairs):
        raise RuntimeError(f"PERSISTED_FORECAST_BUNDLE_INCOMPLETE:{stored}/39")
    return forecasts, no_forecast, int(stored)


def _price_for_settlement(con, symbol, as_of, due):
    as_of_time = parse_time(as_of)
    due_time = parse_time(due)
    expected_open = as_of_time + timedelta(milliseconds=1)
    candidates = con.execute("""
        SELECT open_time, close_time, open, close FROM market_klines
        WHERE symbol=? AND interval='1h' AND close_time>=? AND open_time<=?
        ORDER BY open_time
    """, (symbol, iso(expected_open - timedelta(seconds=2)),
          iso(due_time + timedelta(seconds=2)))).fetchall()
    entry = next((row for row in candidates
                  if abs((parse_time(row[0]) - expected_open).total_seconds()) <= 1.0), None)
    exit_row = next((row for row in candidates
                     if abs((parse_time(row[1]) - due_time).total_seconds()) <= 1.0), None)
    if entry is None or exit_row is None:
        return None
    return float(entry[2]), float(exit_row[3])


def settle_due(con, now):
    from analysis import forecast_core_lab as core
    rows = con.execute("""
        SELECT forecast_id, symbol, as_of, due_time, current_price, target_price,
               price_p10, price_p90, p_up, p_down, p_neutral, shadow_action
        FROM forecast_engine_predictions
        WHERE status='OPEN' AND due_time<=?
        ORDER BY due_time
    """, (iso(now),)).fetchall()
    settled = 0
    meaningful = (core.ROUND_TRIP_COST_BPS + core.MINIMUM_MOVE_BPS) / 100.0
    cost = core.ROUND_TRIP_COST_BPS / 100.0
    for row in rows:
        prices = _price_for_settlement(con, row[1], row[2], row[3])
        if prices is None:
            continue
        entry, exit_price = prices
        actual_return = (exit_price / entry - 1.0) * 100.0
        actual_class = "UP" if actual_return > meaningful else (
            "DOWN" if actual_return < -meaningful else "NEUTRAL")
        probabilities = {"UP": row[8], "DOWN": row[9], "NEUTRAL": row[10]}
        brier = sum((float(probabilities[name]) - (1.0 if name == actual_class else 0.0)) ** 2
                    for name in probabilities)
        predicted_class = max(probabilities, key=probabilities.get)
        action = row[11]
        action_hit = None if action == "BEKLE" else int(
            (action == "AL" and actual_class == "UP") or
            (action == "SAT" and actual_class == "DOWN"))
        utility = None
        if action == "AL":
            utility = actual_return - cost
        elif action == "SAT":
            utility = -actual_return - cost
        error = abs((float(row[5]) / exit_price - 1.0) * 100.0)
        interval_hit = int(float(row[6]) <= exit_price <= float(row[7]))
        con.execute("""
            UPDATE forecast_engine_predictions SET
                status='SETTLED', entry_price=?, exit_price=?, actual_return_pct=?,
                actual_class=?, absolute_error_pct=?, interval_hit=?, brier_score=?,
                direction_hit=?, action_hit=?, action_net_utility_pct=?, settled_at=?
            WHERE forecast_id=? AND status='OPEN'
        """, (entry, exit_price, actual_return, actual_class, error, interval_hit,
              brier, int(predicted_class == actual_class), action_hit, utility,
              iso(now), row[0]))
        settled += 1
    con.commit()
    return settled


def performance(con, artifact):
    output = {}
    for horizon in HORIZONS:
        rows = con.execute("""
            SELECT absolute_error_pct, interval_hit, brier_score, direction_hit,
                   action_hit, action_net_utility_pct
            FROM forecast_engine_predictions
            WHERE status='SETTLED' AND horizon_hours=? AND artifact_hash=?
        """, (horizon, artifact)).fetchall()
        actions = [row for row in rows if row[4] is not None]
        output[horizon] = {
            "settled": len(rows),
            "mae_pct": statistics.fmean(row[0] for row in rows) if rows else 0.0,
            "interval_hit_pct": 100 * statistics.fmean(row[1] for row in rows) if rows else 0.0,
            "direction_hit_pct": 100 * statistics.fmean(row[3] for row in rows) if rows else 0.0,
            "brier": statistics.fmean(row[2] for row in rows) if rows else 0.0,
            "actions": len(actions),
            "action_hit_pct": 100 * statistics.fmean(row[4] for row in actions) if actions else 0.0,
            "utility_pct": statistics.fmean(row[5] for row in actions) if actions else 0.0,
        }
    return output


def render(result):
    print("AG Crypto Analyst - Unified Forecast Engine v1.0.0 | FORWARD_SHADOW")
    print(f"Model: {MODEL_VERSION} | artifact={result['artifact_hash'][:12]}... | as_of={iso(result['as_of'])}")
    print("Tahmin: q50 hedef + %80 aralık | güven: maliyet+anlamlı hareket sonrası yön olasılığı")
    print("SYMBOL       HZN       NOW      TARGET          80% RANGE   UP%  DOWN%  SHADOW  RISK  BEN_OLSAM")
    for row in sorted(result["forecasts"], key=lambda item: (item["symbol"], item["horizon"])):
        opinion = row["risk_adjusted_action"]
        print(f"{row['symbol']:<12} {row['horizon']:>2}h {row['current_price']:>10,.4f} "
              f"{row['target_price']:>10,.4f} {row['price_p10']:>10,.4f}-{row['price_p90']:<10,.4f} "
              f"{100*row['p_up']:>5.1f} {100*row['p_down']:>6.1f} "
              f"{row['action']:<7} {row['risk_state']:<6} {opinion}")
    if result["no_forecast"]:
        print("NO_FORECAST:")
        for item in result["no_forecast"]:
            print(f"- {item['symbol']} {item['horizon']}h | {item['reason']}")
    print("FORWARD PERFORMANCE")
    for horizon, score in result["performance"].items():
        print(f"{horizon:>2}h | settled={score['settled']} | direction={score['direction_hit_pct']:.1f}% | "
              f"MAE={score['mae_pct']:.3f}% | interval={score['interval_hit_pct']:.1f}% | "
              f"actions={score['actions']} | action_hit={score['action_hit_pct']:.1f}% | utility={score['utility_pct']:+.3f}%")
    print("Not: BEN_OLSAM gölge görüştür; ürün alarmı veya otomatik işlem değildir. SAT=spot azalt/çık/kaçın, short değildir.")


def run(now=None, force_retrain=False):
    from analysis import forecast_core_lab as core

    now = now or utcnow()
    start, fetch_start, end = core.requested_range(now)
    con = connect()
    try:
        ensure_tables(con)
        coverage = core.ensure_historical_coverage(con, fetch_start, end)
        errors = {symbol: value for symbol, value in coverage.items()
                  if value["status"] not in {"READY", "FETCHED_READY"}}
        if errors:
            raise RuntimeError("MARKET_DATA_NOT_READY: " + json.dumps(errors, ensure_ascii=False))
        series = core.load_series(con, fetch_start, end)
        model = None if force_retrain else load_cached_model(now)
        if model is None:
            model = train_model(series, start, end, now)
        settled = settle_due(con, now)
        decision_time = latest_common_close(series, end)
        validate_forecast_freshness(decision_time, now)
        forecasts, no_forecast, stored = create_forecasts(con, model, series, decision_time, now)
        if any(row["due_time"] <= now for row in forecasts):
            raise RuntimeError("FORECAST_DUE_TIME_NOT_FORWARD")
        scores = performance(con, model["artifact_hash"])
        con.execute("""
            INSERT INTO forecast_engine_runs(
                created_at, engine_version, model_version, artifact_hash,
                training_end, training_rows, forecast_count, no_forecast_count,
                source_errors_json
            ) VALUES (?,?,?,?,?,?,?,?,?)
        """, (iso(now), ENGINE_VERSION, MODEL_VERSION, model["artifact_hash"],
              model["training_end"], model["fit_rows"] + model["calibration_rows"],
              stored, len(no_forecast), json.dumps(no_forecast, ensure_ascii=False)))
        con.commit()
        return {"status": "SUCCESS" if not no_forecast else "PARTIAL",
                "as_of": decision_time, "artifact_hash": model["artifact_hash"],
                "forecasts": forecasts, "no_forecast": no_forecast, "stored": stored,
                "settled": settled, "performance": scores,
                "model_retrained": model["created_at"] == iso(now)}
    finally:
        con.close()


def self_test():
    assert len(HORIZONS) == 3
    assert parse_time("2026-01-01T00:00:00+00:00").tzinfo is not None
    payload = {"x": 1, "models": {1: {}, 4: {}, 24: {}}}
    assert artifact_hash(payload) == artifact_hash(payload)
    reference = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert validate_forecast_freshness(reference, reference + timedelta(minutes=10)) == timedelta(minutes=10)
    try:
        validate_forecast_freshness(reference, reference + timedelta(minutes=16))
    except RuntimeError:
        pass
    else:
        raise AssertionError("stale forecast guard failed")
    print("Unified Forecast Engine self-test: PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--retrain", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run(force_retrain=args.retrain)
    render(result)
    return result


if __name__ == "__main__":
    main()
