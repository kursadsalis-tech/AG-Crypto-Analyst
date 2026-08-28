"""Fixed rolling 1h direction ensemble validated against the frozen incumbent."""
from __future__ import annotations

import math
import pickle
import hashlib
from datetime import timedelta


VERSION = "ROLLING_1H_ADVANCED_TECHNICAL_ENSEMBLE_V2_FIXED"
TRAINING_DAYS = 120
HALF_LIFE_DAYS = 45.0
BLEND_WEIGHT = 0.50
FEATURE_COUNT = 12
RANDOM_STATE = 20260828
CLASSES = ("UP", "DOWN", "NEUTRAL")


def fit(rows, cutoff):
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier

    selected = [
        row for row in rows
        if int(row["horizon"]) == 1
        and cutoff - timedelta(days=TRAINING_DAYS) <= row["as_of"]
        and row["due_time"] < cutoff
    ]
    if len(selected) < 1000:
        raise ValueError("insufficient rolling 1h direction rows")
    meaningful = 0.15
    labels = np.asarray([
        "UP" if row["trade_return_pct"] > meaningful else (
            "DOWN" if row["trade_return_pct"] < -meaningful else "NEUTRAL"
        ) for row in selected
    ])
    from analysis import advanced_technical_features
    matrix = np.asarray([
        row["features"][:FEATURE_COUNT] + row["technical_features"]
        for row in selected
    ], dtype=float)
    if not np.isfinite(matrix).all():
        raise ValueError("non-finite rolling direction features")
    ages = np.asarray([
        (cutoff - row["as_of"]).total_seconds() / 86400.0 for row in selected
    ])
    weights = np.exp(-math.log(2.0) * ages / HALF_LIFE_DAYS)
    model = HistGradientBoostingClassifier(
        max_iter=100, max_leaf_nodes=15, max_depth=3,
        min_samples_leaf=100, l2_regularization=2.0,
        learning_rate=0.05, random_state=RANDOM_STATE,
    )
    model.fit(matrix, labels, sample_weight=weights)
    model_bytes = pickle.dumps(model, protocol=4)
    return {
        "version": VERSION, "model_bytes": model_bytes,
        "model_sha256": hashlib.sha256(model_bytes).hexdigest(),
        "classes": tuple(str(value) for value in model.classes_),
        "training_rows": len(selected),
        "training_start": min(row["as_of"] for row in selected),
        "training_end": max(row["due_time"] for row in selected),
        "feature_count": FEATURE_COUNT, "blend_weight": BLEND_WEIGHT,
        "technical_feature_count": len(advanced_technical_features.FEATURE_NAMES),
        "technical_feature_version": advanced_technical_features.VERSION,
        "synthesized_indicator": "NONLINEAR_TECHNICAL_DIRECTION_COMPOSITE",
    }


def blend(rolling, row, incumbent):
    import numpy as np

    matrix = np.asarray([row["features"][:rolling["feature_count"]]
                         + row["technical_features"]], dtype=float)
    model = rolling.get("model")
    if model is None:
        model_bytes = rolling["model_bytes"]
        if hashlib.sha256(model_bytes).hexdigest() != rolling["model_sha256"]:
            raise ValueError("rolling direction model hash mismatch")
        model = pickle.loads(model_bytes)
    raw = model.predict_proba(matrix)[0]
    rolling_probabilities = {
        name: float(raw[rolling["classes"].index(name)]) for name in CLASSES
    }
    output = dict(incumbent)
    for name, field in (("UP", "p_up"), ("DOWN", "p_down"), ("NEUTRAL", "p_neutral")):
        output[field] = (
            (1.0 - rolling["blend_weight"]) * float(incumbent[field])
            + rolling["blend_weight"] * rolling_probabilities[name]
        )
    expected = float(incumbent["expected_trade_return_pct"])
    cost = 0.10
    if output["p_up"] >= 0.70 and expected - cost > 0.0:
        output["action"], output["expected_net_return_pct"] = "AL", expected - cost
    elif output["p_down"] >= 0.70 and -expected - cost > 0.0:
        output["action"], output["expected_net_return_pct"] = "SAT", -expected - cost
    else:
        output["action"], output["expected_net_return_pct"] = "BEKLE", 0.0
    output["risk_adjusted_action"] = (
        output["action"] if row["risk_eligible"] else "BEKLE"
    )
    output["direction_ensemble_version"] = VERSION
    output["synthesized_indicator"] = rolling["synthesized_indicator"]
    output["rolling_probabilities"] = rolling_probabilities
    return output
