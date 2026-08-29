"""Fail-closed, top-down market context for forecast confirmation."""
from __future__ import annotations

import math

from analysis import multi_timeframe_features as mtf


VERSION = "HIERARCHICAL_MARKET_CONTEXT_V1_SHADOW"


def _direction(value, deadband=0.0):
    value = float(value)
    if value > deadband:
        return "UP"
    if value < -deadband:
        return "DOWN"
    return "NEUTRAL"


def _feature_map(values):
    return dict(zip(mtf.FEATURE_NAMES, map(float, values)))


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
    layers = {
        "market_ground": market_direction,
        "big_picture": big_picture,
        "setup_4h": setup,
        "timing_1h": timing,
        "spot_flow": flow["direction"],
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
        "direction": direction,
        "confirmations": confirmations,
        "layers": layers,
        "flow": flow,
        "reasons": reasons,
        "production_eligible": False,
        "action_authority": False,
        "policy_mutation_allowed": False,
    }


def confirms_forecast(context, predicted_class):
    predicted_class = str(predicted_class).upper()
    return bool(
        context.get("state") == "ALIGNED"
        and predicted_class in {"UP", "DOWN"}
        and context.get("direction") == predicted_class
    )
