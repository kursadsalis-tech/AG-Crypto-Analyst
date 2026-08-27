"""Forward-only persistence for the frozen spot-flow feature challenger."""
from __future__ import annotations

import json

from analysis import forecast_core_lab as v1
from analysis import forecast_core_v2_lab as booster
from analysis import forecast_feature_challenger_lab as lab


def ensure_table(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS forecast_feature_challenger_predictions (
            challenger_id INTEGER PRIMARY KEY AUTOINCREMENT,
            forecast_id INTEGER NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            model_version TEXT NOT NULL,
            model_hash TEXT NOT NULL,
            horizon_hours INTEGER NOT NULL,
            status TEXT NOT NULL,
            p_up REAL NOT NULL,
            p_down REAL NOT NULL,
            p_neutral REAL NOT NULL,
            predicted_return_pct REAL NOT NULL,
            predicted_class TEXT NOT NULL,
            shadow_action TEXT NOT NULL,
            production_eligible INTEGER NOT NULL DEFAULT 0,
            action_authority INTEGER NOT NULL DEFAULT 0,
            feature_json TEXT NOT NULL,
            actual_class TEXT,
            actual_return_pct REAL,
            direction_hit INTEGER,
            action_hit INTEGER,
            action_net_utility_pct REAL,
            brier_score REAL,
            settled_at TEXT
        );
    """)


def record(con, forecast_id, created_at, base_row, series, payload):
    ensure_table(con)
    horizon = int(base_row["horizon"])
    row = lab.live_row(base_row, series)
    prediction = booster.forecast_row(payload["models"][horizon], row)
    selection = {
        int(item["horizon_hours"]): item["status"]
        for item in payload["development_selection"]
    }
    probabilities = {
        "UP": float(prediction["p_up"]), "DOWN": float(prediction["p_down"]),
        "NEUTRAL": float(prediction["p_neutral"]),
    }
    predicted_class = max(probabilities, key=probabilities.get)
    features = {
        name: float(value) for name, value in zip(lab.FEATURE_NAMES, row["features"])
    }
    con.execute("""
        INSERT OR IGNORE INTO forecast_feature_challenger_predictions(
            forecast_id,created_at,model_version,model_hash,horizon_hours,status,
            p_up,p_down,p_neutral,predicted_return_pct,predicted_class,
            shadow_action,production_eligible,action_authority,feature_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        int(forecast_id), created_at, lab.MODEL_VERSION, payload["model_hash"], horizon,
        "FORWARD_SHADOW_" + selection.get(horizon, "UNKNOWN"),
        probabilities["UP"], probabilities["DOWN"], probabilities["NEUTRAL"],
        float(prediction["predicted_return_pct"]), predicted_class,
        str(prediction["action"]), 0, 0,
        json.dumps({
            "schema": lab.LAB_VERSION, "feature_cutoff": v1.iso(row["feature_cutoff"]),
            "lookahead_safe": True, "features": features,
            "production_eligible": False, "action_authority": False,
        }, sort_keys=True),
    ))


def settle(con, forecast_id, actual_class, actual_return, settled_at):
    row = con.execute("""
        SELECT p_up,p_down,p_neutral,predicted_class,shadow_action
        FROM forecast_feature_challenger_predictions WHERE forecast_id=?
    """, (int(forecast_id),)).fetchone()
    if not row:
        return False
    probabilities = dict(zip(("UP", "DOWN", "NEUTRAL"), map(float, row[:3])))
    brier = sum(
        (probabilities[name] - int(name == actual_class)) ** 2
        for name in probabilities
    )
    action = str(row[4])
    action_hit = None if action == "BEKLE" else int(
        (action == "AL" and actual_class == "UP")
        or (action == "SAT" and actual_class == "DOWN")
    )
    cost = v1.ROUND_TRIP_COST_BPS / 100.0
    utility = None
    if action == "AL":
        utility = float(actual_return) - cost
    elif action == "SAT":
        utility = -float(actual_return) - cost
    con.execute("""
        UPDATE forecast_feature_challenger_predictions SET
          status='SETTLED',actual_class=?,actual_return_pct=?,direction_hit=?,
          action_hit=?,action_net_utility_pct=?,brier_score=?,settled_at=?
        WHERE forecast_id=? AND settled_at IS NULL
    """, (actual_class, float(actual_return), int(str(row[3]) == actual_class),
          action_hit, utility, brier, settled_at, int(forecast_id)))
    return True
