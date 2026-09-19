"""Conservative, deterministic paper execution model.

This module never talks to an order endpoint. Fresh executable bid/ask is
mandatory for every fill; analysis references cannot authorize execution.
"""
from __future__ import annotations

import math
import os
from datetime import datetime

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
MAX_QUOTE_AGE_MS = 2_000


def quote_evidence(market, side):
    evidence = {
        "side": side, "market_type": market.market_type,
        "observed_at": market.observed_at,
        "quote_timestamp_ms": market.quote_timestamp_ms,
        "liquidity_timestamp_ms": market.liquidity_timestamp_ms,
        "best_bid": market.best_bid, "best_ask": market.best_ask,
        "selected_executable_side": "ASK" if side == "BUY" else "BID",
        "executable_reference_price": None, "quote_age_ms": None,
        "source": market.source, "real_order_allowed": 0,
    }
    try:
        observed = datetime.fromisoformat(market.observed_at.replace("Z", "+00:00"))
        if observed.tzinfo and math.isfinite(market.quote_timestamp_ms):
            evidence["quote_age_ms"] = observed.timestamp() * 1000 - market.quote_timestamp_ms
    except (AttributeError, TypeError, ValueError, OverflowError):
        pass
    return evidence


def validate_quote(market, side):
    if side not in {"BUY", "SELL"}:
        return None, "INVALID_SIDE"
    for name, value in (("BID", market.best_bid), ("ASK", market.best_ask)):
        if value is None:
            return None, "MISSING_" + name
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            return None, "INVALID_" + name
    if market.best_bid > market.best_ask:
        return None, "CROSSED_QUOTE"
    if market.market_type not in {"SPOT", "USD_M_PERPETUAL"}:
        return None, "UNSUPPORTED_MARKET"
    if not market.source:
        return None, "QUOTE_SOURCE_UNAVAILABLE"
    try:
        observed = datetime.fromisoformat(market.observed_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError()
        timestamp = market.quote_timestamp_ms
        if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp <= 0:
            raise ValueError()
        age = observed.timestamp() * 1000 - timestamp
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None, "INVALID_QUOTE_TIMESTAMP"
    if age < 0:
        return None, "FUTURE_QUOTE"
    if age > MAX_QUOTE_AGE_MS:
        return None, "STALE_QUOTE"
    return (market.best_ask if side == "BUY" else market.best_bid), None



def _env_bps(name: str, default: str) -> float:
    return max(0.0, float(os.getenv(name, default))) / 10_000.0


class PaperExecutionAdapter:
    """Deterministic PAPER implementation of the common execution contract."""

    name = "CONSERVATIVE_PAPER_V1"

    def execute(self, intent, market, rules):
        reference, reason = validate_quote(market, intent.side)
        evidence = quote_evidence(market, intent.side)
        evidence["symbol"] = intent.symbol
        evidence["requested_notional"] = intent.requested_notional
        if reason is None:
            evidence["executable_reference_price"] = reference
            observed_ms = datetime.fromisoformat(market.observed_at.replace("Z", "+00:00")).timestamp() * 1000
            evidence["quote_age_ms"] = observed_ms - market.quote_timestamp_ms
            evidence["observed_spread"] = market.best_ask - market.best_bid
            if rules is None:
                reason = "EXCHANGE_RULES_UNAVAILABLE"
            elif rules.require_liquidity:
                timestamp = market.liquidity_timestamp_ms
                if timestamp is None:
                    reason = "MISSING_LIQUIDITY_TIMESTAMP"
                elif (not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp)
                      or timestamp <= 0 or timestamp > observed_ms):
                    reason = "INVALID_LIQUIDITY_TIMESTAMP"
                elif observed_ms - timestamp > MAX_QUOTE_AGE_MS:
                    reason = "STALE_LIQUIDITY"
        fill = None
        if reason is None:
            fill, reason = _paper_fill(intent, market, rules, reference)
        evidence.update({
            "status": ("PARTIAL" if fill.partial else "FILLED") if fill else "REJECTED",
            "rejection_reason": None if fill else reason,
            "final_simulated_fill_price": fill.price if fill else None,
            "quantity": fill.quantity if fill else 0,
            "notional": fill.quantity * fill.price if fill else 0,
            "fee": fill.fee if fill else 0,
            "slippage": fill.slippage_cost if fill else 0,
            "partial": fill.partial if fill else False,
        })
        return ExecutionDecision(status="FILLED" if fill else "REJECTED",
                                 reason=reason, fill=fill, evidence=evidence)


def _paper_fill(intent, market, rules, reference_price, *, estimate=False):
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
    spread = _env_bps("AGCA_PAPER_SPREAD_BPS", "4") if estimate else 0.0
    base_slippage = _env_bps("AGCA_PAPER_SLIPPAGE_BPS", "3")
    latency_impact = _env_bps("AGCA_PAPER_LATENCY_BPS", "1")
    participation_limit = max(
        0.0001, min(1.0, float(os.getenv("AGCA_PAPER_MAX_VOLUME_PARTICIPATION", "0.01")))
    )
    fill_notional = requested_notional
    partial = False
    if available_quote_volume is not None:
        # A reduce-only order removes existing risk and may consume the
        # explicitly observed book. New exposure keeps the conservative
        # participation cap; protective exits must not be stranded by it.
        capacity = available_quote_volume * (1.0 if intent.reduce_only else participation_limit)
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
    if not math.isfinite(raw_price) or raw_price <= 0:
        return None, "INVALID_SIMULATED_PRICE"
    if side == "BUY":
        price = math.ceil(raw_price / price_tick) * price_tick
    else:
        price = math.floor(raw_price / price_tick) * price_tick
    if (not math.isfinite(price) or price <= 0
            or (side == "BUY" and price < reference_price)
            or (side == "SELL" and price > reference_price)):
        return None, "INVALID_SIMULATED_PRICE"
    quantity = math.floor((fill_notional / price) / quantity_step) * quantity_step
    if available_quote_volume is not None:
        # Adverse SELL prices must not turn a quote-notional budget into more
        # base units than were actually visible on the selected side.
        max_visible_qty = math.floor((capacity / reference_price) / quantity_step) * quantity_step
        quantity = min(quantity, max_visible_qty)
    actual_notional = quantity * price
    if quantity <= 0 or (actual_notional < min_notional and not intent.reduce_only):
        return None, "EXCHANGE_FILTER_REJECTION"
    fee_rate = _env_bps("AGCA_PAPER_TAKER_FEE_BPS", "5")
    return Fill(
        price=price,
        quantity=quantity,
        fee=actual_notional * fee_rate,
        spread_cost=actual_notional * spread / 2.0,
        slippage_cost=(actual_notional * impact if estimate else quantity * abs(price - reference_price)),
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
    reduce_only: bool = False, best_bid: float | None = None,
    best_ask: float | None = None, quote_timestamp_ms: int | None = None,
    liquidity_timestamp_ms: int | None = None, observed_at: str | None = None,
    market_type: str = "USD_M_PERPETUAL", source: str | None = None,
) -> Fill | None:
    """Backward-compatible PAPER wrapper over the common contract."""
    return simulated_decision(
        reference_price=reference_price, requested_notional=requested_notional,
        side=side, available_quote_volume=available_quote_volume,
        quantity_step=quantity_step, price_tick=price_tick,
        min_notional=min_notional, require_liquidity=require_liquidity,
        symbol=symbol, reduce_only=reduce_only,
        best_bid=best_bid, best_ask=best_ask, quote_timestamp_ms=quote_timestamp_ms,
        liquidity_timestamp_ms=liquidity_timestamp_ms, observed_at=observed_at,
        market_type=market_type, source=source,
    ).fill


def simulated_decision(
    *, reference_price: float, requested_notional: float, side: str,
    available_quote_volume: float | None = None, quantity_step: float = 1e-8,
    price_tick: float = 1e-8, min_notional: float = 5.0,
    require_liquidity: bool = False, symbol: str = "UNSPECIFIED",
    reduce_only: bool = False, best_bid: float | None = None,
    best_ask: float | None = None, quote_timestamp_ms: int | None = None,
    liquidity_timestamp_ms: int | None = None, observed_at: str | None = None,
    market_type: str = "USD_M_PERPETUAL", source: str | None = None,
) -> ExecutionDecision:
    """Return the fill and exact fail-closed exchange decision for audit."""
    return execute_paper(
        OrderIntent(
            symbol=symbol, side=side,
            requested_notional=requested_notional,
            reduce_only=reduce_only,
        ),
        MarketSnapshot(
            reference_price=reference_price,
            available_quote_volume=available_quote_volume,
            best_bid=best_bid, best_ask=best_ask, quote_timestamp_ms=quote_timestamp_ms,
            liquidity_timestamp_ms=liquidity_timestamp_ms, observed_at=observed_at,
            market_type=market_type, source=source,
        ),
        ExecutionRules(
            quantity_step=quantity_step, price_tick=price_tick,
            min_notional=min_notional, require_liquidity=require_liquidity,
        ),
    )


def funding_cost(notional: float, held_hours: float) -> float:
    """Conservative configurable perpetual funding charge (zero for spot)."""
    if PAPER_MARKET == "SPOT":
        return 0.0
    eight_hour_rate = _env_bps("AGCA_PAPER_FUNDING_BPS_8H", "1")
    return max(0.0, notional * eight_hour_rate * held_hours / 8.0)


def estimate_order_rules(*, reference_price, requested_notional, side, symbol,
                         quantity_step, price_tick, min_notional):
    """Preserve the selection filter's estimate; never return an executable fill."""
    fill, reason = _paper_fill(
        OrderIntent(symbol, side, requested_notional),
        MarketSnapshot(reference_price, None),
        ExecutionRules(quantity_step, price_tick, min_notional, False),
        reference_price, estimate=True,
    )
    return ExecutionDecision("ELIGIBLE" if fill else "REJECTED", reason)
