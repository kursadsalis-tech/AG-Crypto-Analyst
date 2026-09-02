"""Conservative, deterministic paper execution model.

This module never talks to an order endpoint.  It converts a reference market
price into an adverse simulated fill and enforces basic exchange constraints.
"""
from __future__ import annotations

import math
import os

from paper_trading.execution_contract import (
    ExecutionDecision,
    ExecutionFill,
    ExecutionRules,
    MarketSnapshot,
    OrderIntent,
)


PAPER_MARKET = os.getenv("AGCA_PAPER_MARKET", "USD_M_PERPETUAL").upper()
if PAPER_MARKET not in {"SPOT", "USD_M_PERPETUAL"}:
    raise RuntimeError("AGCA_PAPER_MARKET must be SPOT or USD_M_PERPETUAL")


Fill = ExecutionFill


def _env_bps(name: str, default: str) -> float:
    return max(0.0, float(os.getenv(name, default))) / 10_000.0


class PaperExecutionAdapter:
    """Deterministic PAPER implementation of the common execution contract."""

    name = "CONSERVATIVE_PAPER_V1"

    def execute(self, intent, market, rules):
        fill, reason = _paper_fill(intent, market, rules)
        return ExecutionDecision(
            status="FILLED" if fill is not None else "REJECTED",
            reason=reason,
            fill=fill,
        )


def _paper_fill(intent, market, rules):
    reference_price = market.reference_price
    requested_notional = intent.requested_notional
    side = intent.side
    available_quote_volume = market.available_quote_volume
    quantity_step = rules.quantity_step
    price_tick = rules.price_tick
    min_notional = rules.min_notional
    numeric = (
        reference_price, requested_notional, quantity_step, price_tick,
        min_notional,
    )
    if (
        side not in {"BUY", "SELL"}
        or not all(math.isfinite(value) for value in numeric)
        or reference_price <= 0
        or requested_notional <= 0
        or quantity_step <= 0
        or price_tick <= 0
        or min_notional <= 0
    ):
        return None, "INVALID_INTENT_OR_RULES"
    if available_quote_volume is not None and (
        not math.isfinite(available_quote_volume) or available_quote_volume <= 0
    ):
        return None, "INVALID_LIQUIDITY"
    if rules.require_liquidity and available_quote_volume is None:
        return None, "MISSING_LIQUIDITY"
    spread = _env_bps("AGCA_PAPER_SPREAD_BPS", "4")
    base_slippage = _env_bps("AGCA_PAPER_SLIPPAGE_BPS", "3")
    latency_impact = _env_bps("AGCA_PAPER_LATENCY_BPS", "1")
    participation_limit = max(
        0.0001, min(1.0, float(os.getenv("AGCA_PAPER_MAX_VOLUME_PARTICIPATION", "0.01")))
    )
    fill_notional = requested_notional
    partial = False
    if available_quote_volume is not None:
        capacity = available_quote_volume * participation_limit
        if capacity < fill_notional:
            fill_notional = capacity
            partial = True
    if fill_notional < min_notional and not intent.reduce_only:
        return None, "MIN_NOTIONAL"
    impact = base_slippage + latency_impact
    if available_quote_volume is not None:
        impact += min(0.01, (fill_notional / available_quote_volume) ** 0.5 / 100.0)
    adverse = spread / 2.0 + impact
    raw_price = reference_price * (1.0 + adverse if side == "BUY" else 1.0 - adverse)
    if side == "BUY":
        price = math.ceil(raw_price / price_tick) * price_tick
    else:
        price = math.floor(raw_price / price_tick) * price_tick
    quantity = math.floor((fill_notional / price) / quantity_step) * quantity_step
    actual_notional = quantity * price
    if quantity <= 0 or (actual_notional < min_notional and not intent.reduce_only):
        return None, "EXCHANGE_FILTER_REJECTION"
    fee_rate = _env_bps("AGCA_PAPER_TAKER_FEE_BPS", "5")
    return Fill(
        price=price,
        quantity=quantity,
        fee=actual_notional * fee_rate,
        spread_cost=actual_notional * spread / 2.0,
        slippage_cost=actual_notional * impact,
        partial=partial,
    ), "PAPER_FILL"


PAPER_EXECUTOR = PaperExecutionAdapter()


def execute_paper(intent, market, rules):
    return PAPER_EXECUTOR.execute(intent, market, rules)


def simulated_fill(
    *, reference_price: float, requested_notional: float, side: str,
    available_quote_volume: float | None = None, quantity_step: float = 1e-8,
    price_tick: float = 1e-8, min_notional: float = 5.0,
    require_liquidity: bool = False, symbol: str = "UNSPECIFIED",
    reduce_only: bool = False,
) -> Fill | None:
    """Backward-compatible PAPER wrapper over the common contract."""
    decision = execute_paper(
        OrderIntent(
            symbol=symbol, side=side,
            requested_notional=requested_notional,
            reduce_only=reduce_only,
        ),
        MarketSnapshot(
            reference_price=reference_price,
            available_quote_volume=available_quote_volume,
        ),
        ExecutionRules(
            quantity_step=quantity_step, price_tick=price_tick,
            min_notional=min_notional, require_liquidity=require_liquidity,
        ),
    )
    return decision.fill


def funding_cost(notional: float, held_hours: float) -> float:
    """Conservative configurable perpetual funding charge (zero for spot)."""
    if PAPER_MARKET == "SPOT":
        return 0.0
    eight_hour_rate = _env_bps("AGCA_PAPER_FUNDING_BPS_8H", "1")
    return max(0.0, notional * eight_hour_rate * held_hours / 8.0)
