import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from paper_trading.execution_model import (
    simulated_fill as execute_fill,
    taker_fee_rate,
)


def simulated_fill(**kwargs):
    reference = kwargs.get("reference_price", 100)
    return execute_fill(
        best_bid=reference, best_ask=reference,
        quote_timestamp_ms=1788000000000, liquidity_timestamp_ms=1788000000000,
        observed_at=datetime.fromtimestamp(1788000000, timezone.utc).isoformat(), source="TEST_BOOK", **kwargs,
    )


class ExecutionModelTest(unittest.TestCase):
    def test_buy_and_sell_are_adverse_and_charge_fee(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            buy = simulated_fill(reference_price=100, requested_notional=1000, side="BUY")
            sell = simulated_fill(reference_price=100, requested_notional=1000, side="SELL")
        self.assertGreater(buy.price, 100)
        self.assertLess(sell.price, 100)
        self.assertGreater(buy.fee, 0)

    def test_default_taker_fee_contract_is_venue_specific(self):
        names = (
            "AGCA_PAPER_SPOT_TAKER_FEE_BPS",
            "AGCA_PAPER_USDM_TAKER_FEE_BPS",
            "AGCA_PAPER_TAKER_FEE_BPS",
            "AGCA_PAPER_FEE_BPS",
        )
        saved = {name: os.environ.pop(name, None) for name in names}
        try:
            self.assertAlmostEqual(taker_fee_rate("SPOT"), 0.001)
            self.assertAlmostEqual(taker_fee_rate("USD_M_PERPETUAL"), 0.0005)
            spot = simulated_fill(
                reference_price=100, requested_notional=1000, side="BUY",
                market_type="SPOT",
            )
            futures = simulated_fill(
                reference_price=100, requested_notional=1000, side="BUY",
                market_type="USD_M_PERPETUAL",
            )
        finally:
            for name, value in saved.items():
                if value is not None:
                    os.environ[name] = value
        self.assertGreater(spot.fee, futures.fee)

    def test_venue_fee_override_beats_legacy_generic_fee(self):
        with mock.patch.dict(os.environ, {
            "AGCA_PAPER_TAKER_FEE_BPS": "7",
            "AGCA_PAPER_SPOT_TAKER_FEE_BPS": "11",
            "AGCA_PAPER_USDM_TAKER_FEE_BPS": "4",
        }, clear=False):
            self.assertAlmostEqual(taker_fee_rate("SPOT"), 0.0011)
            self.assertAlmostEqual(taker_fee_rate("USD_M_PERPETUAL"), 0.0004)

    def test_capacity_creates_partial_fill(self):
        fill = simulated_fill(
            reference_price=100, requested_notional=1000, side="BUY",
            available_quote_volume=10_000,
        )
        self.assertTrue(fill.partial)
        self.assertLess(fill.quantity * fill.price, 1000)

    def test_min_notional_rejects(self):
        self.assertIsNone(simulated_fill(
            reference_price=100, requested_notional=1, side="BUY", min_notional=5,
        ))

    def test_reduce_only_allows_final_dust_exit(self):
        fill = simulated_fill(
            reference_price=100, requested_notional=1, side="SELL",
            min_notional=5, quantity_step=.001, reduce_only=True,
        )
        self.assertIsNotNone(fill)
        self.assertLess(fill.quantity * fill.price, 5)

    def test_reduce_only_can_use_observed_liquidity_beyond_entry_participation_cap(self):
        fill = simulated_fill(
            reference_price=100, requested_notional=75, side="SELL",
            available_quote_volume=100, min_notional=50,
            quantity_step=.001, reduce_only=True,
        )
        self.assertIsNotNone(fill)
        self.assertGreater(fill.quantity * fill.price, 70)

    def test_non_finite_market_inputs_fail_closed(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            self.assertIsNone(simulated_fill(
                reference_price=value, requested_notional=100,
                side="BUY", available_quote_volume=10_000,
                require_liquidity=True,
            ))
            self.assertIsNone(simulated_fill(
                reference_price=100, requested_notional=100,
                side="BUY", available_quote_volume=value,
                require_liquidity=True,
            ))

    def test_missing_or_invalid_liquidity_fails_closed_when_required(self):
        for volume in (None, 0, -1):
            self.assertIsNone(simulated_fill(
                reference_price=100, requested_notional=100,
                side="BUY", available_quote_volume=volume,
                require_liquidity=True,
            ))

    def test_invalid_exchange_rules_fail_closed(self):
        for field in ("quantity_step", "price_tick", "min_notional"):
            arguments = {
                "reference_price": 100,
                "requested_notional": 100,
                "side": "BUY",
                "available_quote_volume": 10_000,
                "require_liquidity": True,
                field: 0,
            }
            self.assertIsNone(simulated_fill(**arguments))


if __name__ == "__main__":
    unittest.main()
