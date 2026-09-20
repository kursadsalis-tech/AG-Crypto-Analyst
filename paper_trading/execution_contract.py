"""Provider-neutral execution contract shared by PAPER and future adapters.

This module defines data only. It has no network client, credentials or order
endpoint and therefore cannot place an exchange order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str
    requested_notional: float
    order_type: str = "MARKET"
    reduce_only: bool = False


@dataclass(frozen=True)
class MarketSnapshot:
    reference_price: float
    available_quote_volume: float | None
    observed_at: str | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    quote_timestamp_ms: int | None = None
    liquidity_timestamp_ms: int | None = None
    market_type: str = "USD_M_PERPETUAL"
    source: str | None = None


@dataclass(frozen=True)
class ExecutionRules:
    quantity_step: float
    price_tick: float
    min_notional: float
    require_liquidity: bool = True
    liquidation_fee_rate: float = 0.0


@dataclass(frozen=True)
class ExecutionFill:
    price: float
    quantity: float
    fee: float
    spread_cost: float
    slippage_cost: float
    partial: bool


@dataclass(frozen=True)
class ExecutionDecision:
    status: str
    reason: str
    fill: ExecutionFill | None = None
    evidence: dict = field(default_factory=dict)


class ExecutionAdapter(Protocol):
    name: str

    def execute(
        self, intent: OrderIntent, market: MarketSnapshot,
        rules: ExecutionRules,
    ) -> ExecutionDecision: ...
