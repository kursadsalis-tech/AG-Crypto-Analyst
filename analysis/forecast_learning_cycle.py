"""Evidence-gated learning review for settled Unified Forecast predictions.

This module never changes live model weights after an individual error. It
looks for recurring, cross-time error patterns and opens a bounded challenger
hypothesis only when minimum sample and independent-week requirements are met.
"""
from __future__ import annotations

import json
import sqlite3
from database.runtime_store import connect as runtime_connect
import statistics
from collections import defaultdict
from datetime import datetime, timezone


DB_NAME = "crypto_data.db"
LEARNING_VERSION = "FORECAST_LEARNING_REVIEW_V1"
MIN_TOTAL_SETTLED = 100
MIN_PATTERN_EVENTS = 30
MIN_PATTERN_WEEKS = 3


def utcnow():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def connect():
    con = runtime_connect(DB_NAME, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def ensure_tables(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS forecast_learning_reviews (
            review_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            learning_version TEXT NOT NULL,
            model_version TEXT NOT NULL,
            artifact_hash TEXT NOT NULL,
            settled_count INTEGER NOT NULL,
            independent_weeks INTEGER NOT NULL,
            status TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            UNIQUE(learning_version, artifact_hash, settled_count)
        );

        CREATE TABLE IF NOT EXISTS forecast_learning_hypotheses (
            hypothesis_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            model_version TEXT NOT NULL,
            artifact_hash TEXT NOT NULL,
            horizon_hours INTEGER NOT NULL,
            pattern_key TEXT NOT NULL,
            candidate_type TEXT NOT NULL,
            event_count INTEGER NOT NULL,
            independent_weeks INTEGER NOT NULL,
            evidence_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'EVIDENCE_READY',
            UNIQUE(artifact_hash, horizon_hours, pattern_key, candidate_type)
        );
    """)
    con.commit()


def latest_artifact(con):
    row = con.execute("""
        SELECT model_version, artifact_hash
        FROM forecast_engine_runs
        ORDER BY run_id DESC LIMIT 1
    """).fetchone()
    return row if row else (None, None)


def parse_json(value):
    try:
        parsed = json.loads(value) if value else {}
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def load_settled(con, artifact_hash):
    rows = con.execute("""
        SELECT symbol, horizon_hours, as_of, actual_return_pct,
               absolute_error_pct, interval_hit, brier_score, direction_hit,
               diagnosis_code, diagnosis_json, feature_snapshot_json, regime
        FROM forecast_engine_predictions
        WHERE status='SETTLED' AND artifact_hash=?
        ORDER BY as_of, symbol, horizon_hours
    """, (artifact_hash,)).fetchall()
    output = []
    for row in rows:
        diagnosis = parse_json(row[9])
        snapshot = parse_json(row[10])
        output.append({
            "symbol": row[0],
            "horizon": int(row[1]),
            "as_of": str(row[2]),
            "actual_return_pct": float(row[3]),
            "absolute_error_pct": float(row[4]),
            "interval_hit": int(row[5]),
            "brier_score": float(row[6]),
            "class_hit": int(row[7]),
            "diagnosis_code": str(row[8] or "UNKNOWN"),
            "tags": list(diagnosis.get("tags") or []),
            "features": dict(snapshot.get("model_features") or {}),
            "regime": str(row[11] or "UNKNOWN"),
        })
    return output


def week_key(as_of):
    value = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    year, week, _ = value.isocalendar()
    return f"{year}-W{week:02d}"


def metrics(rows):
    if not rows:
        return {"events": 0, "weeks": 0, "class_hit_pct": 0.0,
                "mae_pct": 0.0, "interval_hit_pct": 0.0, "brier": 0.0}
    return {
        "events": len(rows),
        "weeks": len({week_key(row["as_of"]) for row in rows}),
        "class_hit_pct": 100.0 * statistics.fmean(row["class_hit"] for row in rows),
        "mae_pct": statistics.fmean(row["absolute_error_pct"] for row in rows),
        "interval_hit_pct": 100.0 * statistics.fmean(row["interval_hit"] for row in rows),
        "brier": statistics.fmean(row["brier_score"] for row in rows),
    }


def cohort_rows(rows):
    cohorts = defaultdict(list)
    for row in rows:
        horizon = row["horizon"]
        cohorts[(horizon, f"REGIME:{row['regime']}")].append(row)
        cohorts[(horizon, f"DIAG:{row['diagnosis_code']}")].append(row)
        for tag in row["tags"]:
            if tag != "ACTION_ABSTAINED":
                cohorts[(horizon, f"TAG:{tag}")].append(row)
    return cohorts


def candidate_type(pattern_key):
    if "DIRECTION_CLASS_MISS" in pattern_key:
        return "DIRECTION_CALIBRATION_CHALLENGER"
    if "INTERVAL_MISS" in pattern_key:
        return "INTERVAL_RECALIBRATION_CHALLENGER"
    if "TARGET_MAGNITUDE_MISS" in pattern_key:
        return "TARGET_SCALE_CHALLENGER"
    return "CONTEXT_FEATURE_CHALLENGER"


def discover_patterns(rows):
    horizon_baselines = {
        horizon: metrics([row for row in rows if row["horizon"] == horizon])
        for horizon in (1, 4, 24)
    }
    patterns = []
    for (horizon, key), subset in cohort_rows(rows).items():
        score = metrics(subset)
        if score["events"] < MIN_PATTERN_EVENTS or score["weeks"] < MIN_PATTERN_WEEKS:
            continue
        baseline = horizon_baselines[horizon]
        repeated_failure = (
            score["class_hit_pct"] <= 45.0
            or score["interval_hit_pct"] < 70.0
            or score["mae_pct"] >= max(0.10, baseline["mae_pct"] * 1.25)
        )
        if not repeated_failure:
            continue
        patterns.append({
            "horizon": horizon,
            "pattern_key": key,
            "candidate_type": candidate_type(key),
            "metrics": score,
            "horizon_baseline": baseline,
            "claim_level": "STATISTICAL_ASSOCIATION_NOT_CAUSATION",
        })
    return sorted(
        patterns,
        key=lambda item: (-item["metrics"]["events"], item["horizon"], item["pattern_key"]),
    )


def run(now=None):
    now = now or utcnow()
    con = connect()
    try:
        ensure_tables(con)
        model_version, artifact_hash = latest_artifact(con)
        if not artifact_hash:
            return {"status": "DATA_BLOCKED", "reason": "NO_FORECAST_ARTIFACT"}
        rows = load_settled(con, artifact_hash)
        weeks = len({week_key(row["as_of"]) for row in rows})
        patterns = discover_patterns(rows)
        if len(rows) < MIN_TOTAL_SETTLED:
            status = "COLLECTING_EVIDENCE"
        elif patterns:
            status = "PATTERN_EVIDENCE_READY"
        else:
            status = "NO_STABLE_ERROR_PATTERN"
        evidence = {
            "policy": {
                "coin_blacklist": False,
                "single_error_weight_update": False,
                "automatic_champion_replacement": False,
                "minimum_total_settled": MIN_TOTAL_SETTLED,
                "minimum_pattern_events": MIN_PATTERN_EVENTS,
                "minimum_pattern_weeks": MIN_PATTERN_WEEKS,
            },
            "horizons": {
                str(horizon): metrics([row for row in rows if row["horizon"] == horizon])
                for horizon in (1, 4, 24)
            },
            "patterns": patterns,
        }
        con.execute("""
            INSERT OR IGNORE INTO forecast_learning_reviews(
                created_at, learning_version, model_version, artifact_hash,
                settled_count, independent_weeks, status, evidence_json
            ) VALUES (?,?,?,?,?,?,?,?)
        """, (iso(now), LEARNING_VERSION, model_version, artifact_hash,
              len(rows), weeks, status, json.dumps(evidence, ensure_ascii=False, sort_keys=True)))
        if status == "PATTERN_EVIDENCE_READY":
            for pattern in patterns:
                score = pattern["metrics"]
                con.execute("""
                    INSERT OR IGNORE INTO forecast_learning_hypotheses(
                        created_at, model_version, artifact_hash, horizon_hours,
                        pattern_key, candidate_type, event_count,
                        independent_weeks, evidence_json
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                """, (iso(now), model_version, artifact_hash, pattern["horizon"],
                      pattern["pattern_key"], pattern["candidate_type"],
                      score["events"], score["weeks"],
                      json.dumps(pattern, ensure_ascii=False, sort_keys=True)))
        con.commit()
        return {"status": status, "settled": len(rows), "weeks": weeks,
                "patterns": patterns, "evidence": evidence}
    finally:
        con.close()


def render(result):
    print("AG Crypto Analyst - Forecast Learning Review v1.0.0")
    if result["status"] == "DATA_BLOCKED":
        print(f"Learning: DATA_BLOCKED | reason={result['reason']}")
        return
    print(f"Learning: {result['status']} | settled={result['settled']} | "
          f"independent_weeks={result['weeks']} | stable_patterns={len(result['patterns'])}")
    for horizon in (1, 4, 24):
        score = result["evidence"]["horizons"][str(horizon)]
        print(f"{horizon:>2}h | n={score['events']} | weeks={score['weeks']} | "
              f"class_hit={score['class_hit_pct']:.1f}% | MAE={score['mae_pct']:.3f}% | "
              f"interval={score['interval_hit_pct']:.1f}% | Brier={score['brier']:.3f}")
    for pattern in result["patterns"][:5]:
        score = pattern["metrics"]
        print(f"CANDIDATE | {pattern['horizon']}h | {pattern['pattern_key']} | "
              f"n={score['events']} weeks={score['weeks']} | {pattern['candidate_type']}")
    print("Policy: coin blacklist yok; tek hata sonrası ağırlık değişimi yok; "
          "yalnız doğrulanmış challenger champion olabilir.")


def main():
    result = run()
    render(result)
    return result


def self_test():
    rows = []
    for index in range(30):
        rows.append({
            "symbol": "BTCUSDT", "horizon": 4,
            "as_of": f"2026-07-{1 + index % 21:02d}T00:00:00+00:00",
            "actual_return_pct": -1.0, "absolute_error_pct": 0.5,
            "interval_hit": 1, "brier_score": 0.8, "class_hit": 0,
            "diagnosis_code": "DIRECTION_CLASS_MISS",
            "tags": ["REGIME_SIDEWAYS"], "features": {}, "regime": "SIDEWAYS",
        })
    assert metrics(rows)["events"] == 30
    assert discover_patterns(rows)[0]["candidate_type"] == "DIRECTION_CALIBRATION_CHALLENGER"
    print("Forecast Learning Review self-test: PASS")


if __name__ == "__main__":
    main()
