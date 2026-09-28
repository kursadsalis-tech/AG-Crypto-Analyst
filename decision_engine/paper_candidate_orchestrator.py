"""Shared PAPER candidate orchestration without trade/execution authority.

This module combines eligible decision evidence for the canonical realtime PAPER
path. It may read/persist decision evidence through the existing behavior core,
but it never creates positions, fills orders, manages open positions, or grants
real-order authority. The caller remains the single trade authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable

from analysis import realtime_technical_context, technical_trade_plan
from paper_trading import behavior_decision_core, risk_tiering
from paper_trading.execution_model import taker_fee_rate


@dataclass(frozen=True)
class OrchestratedCandidate:
    candidate: dict[str, Any]
    risk_tier: risk_tiering.RiskTier
    fee_rate: float
    raw_stop_pct: float
    effective_stop_pct: float


@dataclass(frozen=True)
class SizedCandidate:
    notional: float
    expected_profit_usdt: float
    expected_loss_budget_usdt: float
    expected_profit_per_risk: float


def validated_stop_geometry(candidate: dict[str, Any]):
    """Return bounded stop geometry or None when required evidence is invalid."""
    try:
        raw_stop_pct = float(candidate["stop_reference_pct"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(raw_stop_pct) or raw_stop_pct <= 0:
        return None
    return raw_stop_pct, min(5.0, max(0.5, raw_stop_pct))


def _call(measure: Callable | None, name: str, function: Callable, *args, **kwargs):
    if measure is None:
        return function(*args, **kwargs)
    return measure(name, function, *args, **kwargs)


def orchestrate(
    con, now, *, behavior_result=None, measure: Callable | None = None
):
    """Combine behavior, technical, risk-tier and cost evidence for selection.

    Returned candidates are execution-ready inputs only. Portfolio capacity,
    idempotency, persistence and order simulation remain caller responsibilities.
    """
    if behavior_result is None:
        behavior_result = _call(
            measure, "behavior_evaluation",
            behavior_decision_core.evaluate, con, now,
        )
    ready: list[OrchestratedCandidate] = []
    rejections: dict[str, int] = {}
    technical_plan_errors: list[dict[str, str]] = []

    def reject(reason: str):
        rejections[reason] = rejections.get(reason, 0) + 1

    for source in behavior_result["candidates"]:
        candidate = dict(source)
        initial_tier = risk_tiering.tier_for(candidate)
        try:
            plan = _call(
                measure,
                "technical_planning",
                technical_trade_plan.build_for_candidate,
                con,
                candidate,
                now=now,
                market_type=initial_tier.market_type,
            )
        except Exception as exc:
            technical_plan_errors.append({
                "symbol": str(candidate.get("symbol") or ""),
                "error_type": type(exc).__name__,
                "detail": str(exc)[:160],
            })
            reject("TECHNICAL_PLAN_UNAVAILABLE")
            continue

        if not bool(plan["risk"]["risk_compatible"]):
            reject("TECHNICAL_RISK_GEOMETRY_INFEASIBLE")
            continue

        candidate["technical_plan"] = plan
        candidate["technical_context"] = realtime_technical_context.load_latest(
            con, candidate["symbol"], now=now
        )
        candidate["stop_reference_pct"] = float(
            plan["risk"]["stop_distance_pct"]
        )

        stop_geometry = validated_stop_geometry(candidate)
        if stop_geometry is None:
            reject("TECHNICAL_STOP_GEOMETRY_INVALID")
            continue

        raw_stop_pct, effective_stop_pct = stop_geometry
        tier = risk_tiering.tier_for(candidate)
        ready.append(OrchestratedCandidate(
            candidate=candidate,
            risk_tier=tier,
            fee_rate=taker_fee_rate(tier.market_type),
            raw_stop_pct=raw_stop_pct,
            effective_stop_pct=effective_stop_pct,
        ))

    return {
        "behavior_result": behavior_result,
        "ready": ready,
        "rejections": rejections,
        "technical_plan_errors": technical_plan_errors,
    }


def size_candidate(
    orchestrated: OrchestratedCandidate,
    *,
    equity: float,
    exposure_cap: float,
    available_cash: float,
) -> SizedCandidate:
    """Apply the shared PAPER sizing/economic contract to one candidate."""
    candidate = orchestrated.candidate
    tier = orchestrated.risk_tier
    fee_rate = orchestrated.fee_rate

    base_margin = (
        float(equity)
        * float(tier.margin_fraction)
        * float(candidate.get("execution_size_multiplier", 1.0))
        * float(candidate["technical_plan"]["quality_multiplier"])
    )
    position_cap = (
        float(equity) * float(candidate["risk_cap_pct"]) / 100.0
    )
    trade_risk_budget = (
        float(equity)
        * float(candidate.get("risk_per_trade_pct") or 0.0)
        / 100.0
    )
    denominator = orchestrated.effective_stop_pct / 100.0 + 2.0 * fee_rate
    risk_sized_notional = (
        trade_risk_budget / denominator
        if trade_risk_budget > 0.0 and denominator > 0.0
        else 0.0
    )
    cash_cap = (
        float(available_cash)
        * float(tier.leverage)
        / (1.0 + fee_rate * float(tier.leverage))
    )
    notional = min(
        base_margin * float(tier.leverage),
        float(exposure_cap),
        position_cap,
        risk_sized_notional,
        cash_cap,
    )
    notional = max(0.0, float(notional))

    expected_profit_usdt = (
        notional
        * max(0.0, float(candidate["expected_net_edge_pct"]))
        / 100.0
    )
    stop_loss_usdt = (
        notional * orchestrated.effective_stop_pct / 100.0
    )
    estimated_round_trip_cost_usdt = notional * (2.0 * fee_rate)
    expected_loss_budget_usdt = (
        stop_loss_usdt + estimated_round_trip_cost_usdt
    )
    expected_profit_per_risk = (
        expected_profit_usdt / expected_loss_budget_usdt
        if expected_loss_budget_usdt > 0.0
        else 0.0
    )
    return SizedCandidate(
        notional=notional,
        expected_profit_usdt=expected_profit_usdt,
        expected_loss_budget_usdt=expected_loss_budget_usdt,
        expected_profit_per_risk=expected_profit_per_risk,
    )


def execution_risk(orchestrated: OrchestratedCandidate, notional: float):
    """Build frozen execution-risk evidence from the same orchestrated inputs."""
    candidate = orchestrated.candidate
    tier = orchestrated.risk_tier
    plan = candidate["technical_plan"]
    notional = float(notional)
    return {
        "risk_tier": tier.tier,
        "leverage": tier.leverage,
        "margin_allocated": notional / float(tier.leverage),
        "gross_exposure": notional,
        "planned_stop_distance_pct": orchestrated.effective_stop_pct,
        "planned_stop_reference_pct": orchestrated.raw_stop_pct,
        "risk_per_trade_pct": float(
            candidate.get("risk_per_trade_pct") or 0.0
        ),
        "planned_stop_and_fee_risk_usdt": notional * (
            orchestrated.effective_stop_pct / 100.0
            + 2.0 * orchestrated.fee_rate
        ),
        "market_type": tier.market_type,
        "taker_fee_rate": orchestrated.fee_rate,
        "stop_basis": "TECHNICAL_STRUCTURE_PLUS_ATR_FROZEN_PLAN",
        "technical_score": float(plan["technical_score"]),
        "technical_timeframes": list(plan["timeframes_used"]),
        "independent_signal_count": tier.independent_signal_count,
        "signal_sources": list(tier.signal_sources),
    }
