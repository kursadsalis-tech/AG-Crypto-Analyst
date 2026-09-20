"""Outcome-blind PAPER risk tiers from independent directional evidence."""
from __future__ import annotations

from dataclasses import dataclass


MAX_PAPER_LEVERAGE = 5.0


@dataclass(frozen=True)
class RiskTier:
    leverage: float
    margin_fraction: float
    independent_signal_count: int
    signal_sources: tuple[str, ...]
    tier: str
    market_type: str


def _directional_sources(candidate):
    direction = str(candidate["direction"]).upper()
    evidence = candidate.get("evidence") or {}
    sources = {str(evidence.get("primary_signal_source") or "MARKET_BEHAVIOR").upper()}
    for signal in evidence.get("directional_signals") or []:
        if not isinstance(signal, dict):
            continue
        source = str(signal.get("source", "")).strip().upper()
        signal_direction = str(signal.get("direction", "")).upper()
        if source and signal_direction == direction:
            sources.add(source)
    event = evidence.get("event") or {}
    event_direction = str(event.get("reported_direction", "")).upper()
    if event.get("kind") != "MARKET_STRUCTURE" and event_direction == direction:
        sources.add("CAUSAL_EXTERNAL_EVENT")
    return tuple(sorted(sources))


def tier_for(candidate) -> RiskTier:
    """Map evidence breadth to size; one valid signal is enough to experiment."""
    sources = _directional_sources(candidate)
    count = len(sources)
    promoted = candidate.get("experiment_tier") == "PROMOTION_READY"
    # Evidence breadth is not leverage evidence. Correlated signals can raise
    # confidence in direction without proving that a larger liquidation-sensitive
    # position has positive net expectancy. Keep exploratory hypotheses at 1x;
    # only independently promoted execution evidence may unlock one bounded step.
    conviction_steps = 2 if promoted and count >= 2 else 1
    leverage = float(min(int(MAX_PAPER_LEVERAGE), conviction_steps))
    margin_fraction = {
        1: 0.005,
        2: 0.010,
    }[int(leverage)]
    return RiskTier(
        leverage=leverage,
        margin_fraction=margin_fraction,
        independent_signal_count=count,
        signal_sources=sources,
        tier=f"PAPER_RISK_TIER_{int(leverage)}",
        # Spot is a genuine lower-risk route for a small LONG hypothesis.
        # SHORT exposure requires a derivative even at 1x.
        market_type=(
            "SPOT" if leverage == 1.0 and candidate["direction"] == "LONG"
            else "USD_M_PERPETUAL"
        ),
    )
