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
    """Map independent evidence + proven execution quality to bounded PAPER leverage."""
    sources = _directional_sources(candidate)
    count = len(sources)
    promoted = candidate.get("experiment_tier") == "PROMOTION_READY"
    feedback = ((candidate.get("evidence") or {}).get("execution_calibration") or {}).get("feedback")
    learned = (candidate.get("evidence") or {}).get("learned_pattern") or {}

    # Breadth matters, but only after own forward execution has confirmed that
    # the setup remains profitable after costs.  This prevents correlated raw
    # signals from manufacturing leverage while still allowing stronger,
    # independently confirmed opportunities to earn more capital efficiency.
    leverage = 1
    if promoted and isinstance(feedback, dict):
        closed = int(feedback.get("closed_count") or 0)
        days = int(feedback.get("independent_days") or 0)
        mean_net = float(feedback.get("mean_net_return_pct") or 0.0)
        win_rate = float(feedback.get("cost_after_win_rate") or 0.0)
        calibrated_edge = float(
            learned.get("calibrated_net_edge_pct",
                        candidate.get("expected_net_edge_pct") or 0.0)
        )
        calibrated_win = float(
            learned.get("calibrated_win_rate",
                        candidate.get("confidence") or 0.0)
        )
        if closed >= 10 and days >= 3 and mean_net > 0 and win_rate >= 0.55:
            leverage = 2
        if (count >= 3 and closed >= 20 and days >= 5 and mean_net >= 0.10
                and win_rate >= 0.58 and calibrated_edge >= 0.10
                and calibrated_win >= 0.58):
            leverage = 3
        if (count >= 4 and closed >= 30 and days >= 7 and mean_net >= 0.15
                and win_rate >= 0.60 and calibrated_edge >= 0.15
                and calibrated_win >= 0.60):
            leverage = 4
        if (count >= 5 and closed >= 50 and days >= 10 and mean_net >= 0.20
                and win_rate >= 0.62 and calibrated_edge >= 0.20
                and calibrated_win >= 0.62):
            leverage = 5

    leverage = float(min(int(MAX_PAPER_LEVERAGE), leverage))
    margin_fraction = {
        1: 0.005,
        2: 0.010,
        3: 0.015,
        4: 0.020,
        5: 0.020,
    }[int(leverage)]
    return RiskTier(
        leverage=leverage,
        margin_fraction=margin_fraction,
        independent_signal_count=count,
        signal_sources=sources,
        tier=f"PAPER_RISK_TIER_{int(leverage)}",
        market_type=(
            "SPOT" if leverage == 1.0 and candidate["direction"] == "LONG"
            else "USD_M_PERPETUAL"
        ),
    )
