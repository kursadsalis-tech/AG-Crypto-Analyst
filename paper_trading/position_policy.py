"""Outcome-blind event policy for continuously managed PAPER positions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


MIN_STOP_PCT = 0.50
MAX_STOP_PCT = 5.00
TP1_R = 1.0
TP2_R = 2.0
TP1_FRACTION = 0.25
TP2_FRACTION = 0.25
TRAIL_R = 0.75
THESIS_BREAK_EVENTS = 3


@dataclass(frozen=True)
class PositionState:
    direction: str
    entry_price: float
    remaining_fraction: float
    stop_distance_pct: float
    stop_price: float
    best_price: float
    tp1_done: bool = False
    tp2_done: bool = False
    opposing_events: int = 0


@dataclass(frozen=True)
class Decision:
    action: str
    close_fraction: float
    reason: str
    stop_price: float
    best_price: float
    opposing_events: int


def _validate(direction, price):
    if direction not in {"LONG", "SHORT"} or float(price) <= 0:
        raise ValueError("invalid PAPER position state")


def initialize(direction: str, entry_price: float, stop_reference_pct: float):
    _validate(direction, entry_price)
    distance = min(MAX_STOP_PCT, max(MIN_STOP_PCT, float(stop_reference_pct)))
    multiplier = 1.0 - distance / 100.0 if direction == "LONG" else 1.0 + distance / 100.0
    return PositionState(
        direction=direction, entry_price=float(entry_price), remaining_fraction=1.0,
        stop_distance_pct=distance, stop_price=float(entry_price) * multiplier,
        best_price=float(entry_price),
    )


def evaluate(state: PositionState, price: float, *, thesis_supported: bool | None,
             now: datetime, maximum_exit_at: datetime, round_trip_cost_pct: float = 0.20):
    _validate(state.direction, price)
    price = float(price)
    favorable = ((price / state.entry_price) - 1.0) * 100.0
    if state.direction == "SHORT":
        favorable = -favorable
    best = max(state.best_price, price) if state.direction == "LONG" else min(state.best_price, price)
    if thesis_supported is False:
        opposing = state.opposing_events + 1
    elif thesis_supported is True:
        opposing = 0
    else:
        opposing = state.opposing_events
    stop_hit = price <= state.stop_price if state.direction == "LONG" else price >= state.stop_price
    if stop_hit:
        return Decision("CLOSE", state.remaining_fraction, "PROTECTIVE_STOP", state.stop_price,
                        best, opposing)
    if opposing >= THESIS_BREAK_EVENTS:
        return Decision("CLOSE", state.remaining_fraction, "THESIS_INVALIDATED", state.stop_price,
                        best, opposing)
    if now >= maximum_exit_at:
        return Decision("CLOSE", state.remaining_fraction, "MAXIMUM_HOLD_TIMEOUT", state.stop_price,
                        best, opposing)

    risk = state.stop_distance_pct
    if not state.tp2_done and state.tp1_done and favorable >= TP2_R * risk:
        trail = price * (1.0 - TRAIL_R * risk / 100.0) if state.direction == "LONG" \
            else price * (1.0 + TRAIL_R * risk / 100.0)
        tightened = max(state.stop_price, trail) if state.direction == "LONG" \
            else min(state.stop_price, trail)
        return Decision("REDUCE", min(TP2_FRACTION, state.remaining_fraction),
                        "TAKE_PROFIT_2R", tightened, best, opposing)
    if not state.tp1_done and favorable >= TP1_R * risk:
        breakeven = state.entry_price * (1.0 + round_trip_cost_pct / 100.0) \
            if state.direction == "LONG" else state.entry_price * (1.0 - round_trip_cost_pct / 100.0)
        tightened = max(state.stop_price, breakeven) if state.direction == "LONG" \
            else min(state.stop_price, breakeven)
        return Decision("REDUCE", min(TP1_FRACTION, state.remaining_fraction),
                        "TAKE_PROFIT_1R", tightened, best, opposing)

    if state.tp1_done:
        trail = best * (1.0 - TRAIL_R * risk / 100.0) if state.direction == "LONG" \
            else best * (1.0 + TRAIL_R * risk / 100.0)
        tightened = max(state.stop_price, trail) if state.direction == "LONG" \
            else min(state.stop_price, trail)
        if tightened != state.stop_price:
            return Decision("UPDATE_STOP", 0.0, "TRAILING_STOP", tightened, best, opposing)
    return Decision("HOLD", 0.0, "THESIS_INTACT", state.stop_price, best, opposing)
