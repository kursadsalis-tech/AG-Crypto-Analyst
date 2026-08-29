"""Fail-closed, top-down market context for forecast confirmation."""
from __future__ import annotations

import math

from analysis import multi_timeframe_features as mtf


VERSION = "REGIME_AWARE_HIERARCHICAL_CONTEXT_V2_SHADOW"


def _direction(value, deadband=0.0):
    value = float(value)
    if value > deadband:
        return "UP"
    if value < -deadband:
        return "DOWN"
    return "NEUTRAL"


def _feature_map(values):
    return dict(zip(mtf.FEATURE_NAMES, map(float, values)))


def _signed(direction):
    return 1.0 if direction == "UP" else (-1.0 if direction == "DOWN" else 0.0)


def _flow_context(candles, index):
    selected = candles[max(0, index - 23):index + 1]
    if len(selected) < 24:
        return {"state": "MISSING", "direction": "NEUTRAL"}
    quote = []
    buy = []
    for row in selected:
        total = row.get("quote_volume")
        taker = row.get("taker_buy_quote_volume")
        if total is None or taker is None:
            return {"state": "MISSING", "direction": "NEUTRAL"}
        total, taker = float(total), float(taker)
        if (
            not math.isfinite(total) or not math.isfinite(taker)
            or total < 0 or taker < 0 or taker > total
        ):
            return {"state": "INVALID", "direction": "NEUTRAL"}
        quote.append(total)
        buy.append(taker)
    total_quote = sum(quote)
    if total_quote <= 0:
        return {"state": "MISSING", "direction": "NEUTRAL"}
    imbalance = (2.0 * sum(buy) / total_quote) - 1.0
    price_return = (
        float(selected[-1]["close"]) / float(selected[0]["open"]) - 1.0
    )
    direction = _direction(imbalance, 0.02)
    confirms_price = bool(
        direction == "NEUTRAL"
        or (direction == "UP" and price_return > 0)
        or (direction == "DOWN" and price_return < 0)
    )
    return {
        "state": "OBSERVED",
        "direction": direction,
        "imbalance": imbalance,
        "price_return": price_return,
        "price_confirms": confirms_price,
    }


def build(candles, index, btc_candles, btc_index, risk_allowed):
    asset = _feature_map(mtf.vector(candles, index))
    btc = _feature_map(mtf.vector(btc_candles, btc_index))
    market_direction = _direction(
        0.7 * btc["higher_timeframe_score"]
        + 0.3 * btc["lower_timeframe_score"],
        0.10,
    )
    big_picture = _direction(asset["higher_timeframe_score"], 0.10)
    setup = _direction(
        asset["4h_long_return"] + asset["4h_mean_spread"], 0.001
    )
    timing = _direction(
        asset["1h_latest_return"] + asset["1h_mean_spread"], 0.0005
    )
    flow = _flow_context(candles, index)
    asset_shock = abs(asset["1h_latest_return"]) > max(
        3.0 * asset["1h_return_volatility"], 0.015
    )
    btc_shock = abs(btc["1h_latest_return"]) > max(
        3.0 * btc["1h_return_volatility"], 0.012
    )
    if asset_shock or btc_shock:
        regime = "SHOCK"
    elif (
        abs(asset["higher_timeframe_score"]) <= 0.20
        and abs(asset["4h_long_return"]) <= max(
            2.0 * asset["4h_return_volatility"], 0.01
        )
    ):
        regime = "RANGE"
    elif big_picture != "NEUTRAL" and big_picture == setup:
        regime = "TREND"
    else:
        regime = "TRANSITION"

    range_position = asset["4h_range_position"]
    range_signal = (
        "DOWN" if range_position >= 0.70 else (
            "UP" if range_position <= -0.70 else timing
        )
    )
    layers = {
        "market_ground": market_direction,
        "big_picture": big_picture,
        "setup_4h": setup,
        "timing_1h": timing,
        "spot_flow": flow["direction"],
        "range_signal": range_signal,
    }
    reasons = []
    if not risk_allowed:
        reasons.append("RISK_BLOCK")
    if big_picture == "NEUTRAL" or setup == "NEUTRAL":
        reasons.append("CORE_DIRECTION_MISSING")
    if big_picture != "NEUTRAL" and setup != "NEUTRAL" and big_picture != setup:
        reasons.append("BIG_PICTURE_SETUP_CONFLICT")
    if market_direction not in {"NEUTRAL", big_picture}:
        reasons.append("MARKET_GROUND_CONFLICT")
    if timing not in {"NEUTRAL", setup}:
        reasons.append("TIMING_CONFLICT")
    if (
        flow["state"] == "OBSERVED"
        and flow["price_confirms"]
        and flow["direction"] not in {"NEUTRAL", setup}
    ):
        reasons.append("FLOW_CONFLICT")
    aligned = not reasons
    direction = big_picture if aligned else "NEUTRAL"
    confirmations = sum((
        market_direction == direction,
        setup == direction,
        timing == direction,
        flow["state"] == "OBSERVED"
        and flow["price_confirms"] and flow["direction"] == direction,
    )) if aligned else 0
    return {
        "version": VERSION,
        "state": "ALIGNED" if aligned else "CONFLICT_OR_BLOCKED",
        "regime": regime,
        "direction": direction,
        "confirmations": confirmations,
        "layers": layers,
        "diagnostics": {
            "asset_shock": asset_shock,
            "btc_shock": btc_shock,
            "higher_timeframe_score": asset["higher_timeframe_score"],
            "top_down_agreement": asset["top_down_agreement"],
            "four_hour_range_position": range_position,
        },
        "flow": flow,
        "reasons": reasons,
        "production_eligible": False,
        "action_authority": False,
        "policy_mutation_allowed": False,
    }


def assess_forecast(context, predicted_class):
    """Grade evidence softly; only risk/data safety is a hard block."""
    predicted_class = str(predicted_class).upper()
    if predicted_class not in {"UP", "DOWN"}:
        return {"tier": "LOW", "support": 0.0, "reason": "NEUTRAL_FORECAST"}
    if "RISK_BLOCK" in context.get("reasons", []):
        return {"tier": "BLOCK", "support": -1.0, "reason": "RISK_BLOCK"}

    regime = context.get("regime", "TRANSITION")
    weights = {
        "TREND": {
            "market_ground": .15, "big_picture": .35, "setup_4h": .25,
            "timing_1h": .10, "spot_flow": .15,
        },
        "RANGE": {
            "market_ground": .10, "big_picture": .05, "range_signal": .40,
            "timing_1h": .20, "spot_flow": .25,
        },
        "TRANSITION": {
            "market_ground": .15, "big_picture": .15, "setup_4h": .20,
            "timing_1h": .20, "spot_flow": .30,
        },
        "SHOCK": {
            "market_ground": .10, "big_picture": .05, "setup_4h": .10,
            "timing_1h": .30, "spot_flow": .45,
        },
    }[regime]
    predicted_sign = _signed(predicted_class)
    support = sum(
        weight * predicted_sign * _signed(context["layers"].get(layer, "NEUTRAL"))
        for layer, weight in weights.items()
    )
    if support >= .55 and regime != "SHOCK":
        tier = "HIGH"
    elif support >= .15:
        tier = "MEDIUM"
    else:
        tier = "LOW"
    return {
        "tier": tier,
        "support": support,
        "reason": f"{regime}_WEIGHTED_EVIDENCE",
        "regime": regime,
        "production_eligible": False,
        "action_authority": False,
    }


def confirms_forecast(context, predicted_class):
    predicted_class = str(predicted_class).upper()
    return bool(
        context.get("state") == "ALIGNED"
        and predicted_class in {"UP", "DOWN"}
        and context.get("direction") == predicted_class
    )
