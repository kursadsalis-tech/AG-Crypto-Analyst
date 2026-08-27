"""Point-in-time class-prior drift challenger for Unified Forecast.

The frozen incumbent can become directionally collapsed when the live class
mix differs from its old calibration window.  This challenger corrects only
the three class probabilities using already-settled, equally weighted hourly
batches.  It has no action, allocation, promotion, or real-order authority.
"""
from __future__ import annotations

import json
from collections import defaultdict


VERSION = "CLASS_PRIOR_DRIFT_CHALLENGER_V1"
CLASSES = ("UP", "DOWN", "NEUTRAL")
MIN_SETTLED_BATCHES = 10
WINDOW_BATCHES = 24
PRIOR_SMOOTHING = 0.02
MIN_RATIO = 0.50
MAX_RATIO = 2.00


def ensure_table(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS forecast_drift_challenger_predictions (
            challenger_id INTEGER PRIMARY KEY AUTOINCREMENT,
            forecast_id INTEGER NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            challenger_version TEXT NOT NULL,
            horizon_hours INTEGER NOT NULL,
            evidence_cutoff TEXT NOT NULL,
            settled_batches INTEGER NOT NULL,
            status TEXT NOT NULL,
            p_up REAL NOT NULL,
            p_down REAL NOT NULL,
            p_neutral REAL NOT NULL,
            predicted_class TEXT NOT NULL,
            production_eligible INTEGER NOT NULL DEFAULT 0,
            action_authority INTEGER NOT NULL DEFAULT 0,
            evidence_json TEXT NOT NULL,
            actual_class TEXT,
            direction_hit INTEGER,
            brier_score REAL,
            settled_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_forecast_drift_horizon_status
        ON forecast_drift_challenger_predictions(horizon_hours, status);
    """)


def _settled_batches(con, horizon, issued_at):
    rows = con.execute("""
        SELECT as_of, actual_class
        FROM forecast_engine_predictions
        WHERE status='SETTLED' AND horizon_hours=? AND settled_at<=?
          AND actual_class IN ('UP','DOWN','NEUTRAL')
        ORDER BY as_of DESC, symbol
    """, (int(horizon), issued_at)).fetchall()
    grouped = defaultdict(list)
    for as_of, actual_class in rows:
        grouped[str(as_of)].append(str(actual_class))
    keys = sorted(grouped, reverse=True)[:WINDOW_BATCHES]
    return [(key, grouped[key]) for key in reversed(keys)]


def _batch_prior(batches):
    if not batches:
        return {name: 1.0 / 3.0 for name in CLASSES}
    return {
        name: sum(values.count(name) / len(values) for _, values in batches) / len(batches)
        for name in CLASSES
    }


def probabilities(con, horizon, issued_at, incumbent, training_prior):
    batches = _settled_batches(con, horizon, issued_at)
    recent = _batch_prior(batches)
    if len(batches) < MIN_SETTLED_BATCHES:
        corrected = {name: float(incumbent[name]) for name in CLASSES}
        status = "WARMING_UP"
        ratios = {name: 1.0 for name in CLASSES}
    else:
        ratios = {
            name: max(MIN_RATIO, min(MAX_RATIO,
                (recent[name] + PRIOR_SMOOTHING) /
                (float(training_prior[name]) + PRIOR_SMOOTHING)))
            for name in CLASSES
        }
        raw = {name: float(incumbent[name]) * ratios[name] for name in CLASSES}
        total = sum(raw.values())
        corrected = {name: raw[name] / total for name in CLASSES}
        status = "FORWARD_SHADOW"
    evidence = {
        "schema": VERSION,
        "batch_weighting": "ONE_AS_OF_BATCH_ONE_VOTE",
        "lookahead_safe": True,
        "window_batches": WINDOW_BATCHES,
        "minimum_batches": MIN_SETTLED_BATCHES,
        "recent_prior": recent,
        "training_prior": {name: float(training_prior[name]) for name in CLASSES},
        "prior_ratio": ratios,
        "policy_mutation": False,
        "production_eligible": False,
        "action_authority": False,
    }
    return corrected, status, len(batches), evidence


def record(con, forecast_id, created_at, horizon, incumbent, training_prior):
    ensure_table(con)
    corrected, status, count, evidence = probabilities(
        con, horizon, created_at, incumbent, training_prior
    )
    predicted = max(CLASSES, key=lambda name: corrected[name])
    con.execute("""
        INSERT OR IGNORE INTO forecast_drift_challenger_predictions(
            forecast_id, created_at, challenger_version, horizon_hours,
            evidence_cutoff, settled_batches, status, p_up, p_down, p_neutral,
            predicted_class, production_eligible, action_authority, evidence_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        int(forecast_id), created_at, VERSION, int(horizon), created_at, count,
        status, corrected["UP"], corrected["DOWN"], corrected["NEUTRAL"],
        predicted, 0, 0, json.dumps(evidence, ensure_ascii=False, sort_keys=True),
    ))


def settle(con, forecast_id, actual_class, settled_at):
    row = con.execute("""
        SELECT p_up, p_down, p_neutral, predicted_class
        FROM forecast_drift_challenger_predictions WHERE forecast_id=?
    """, (int(forecast_id),)).fetchone()
    if not row:
        return False
    probabilities_by_class = dict(zip(CLASSES, map(float, row[:3])))
    brier = sum(
        (probabilities_by_class[name] - (1.0 if name == actual_class else 0.0)) ** 2
        for name in CLASSES
    )
    con.execute("""
        UPDATE forecast_drift_challenger_predictions
        SET actual_class=?, direction_hit=?, brier_score=?, settled_at=?, status='SETTLED'
        WHERE forecast_id=? AND settled_at IS NULL
    """, (actual_class, int(str(row[3]) == actual_class), brier, settled_at, int(forecast_id)))
    return True
