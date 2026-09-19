import unittest

from paper_trading.execution_contract import (
    ExecutionRules,
    MarketSnapshot,
    OrderIntent,
)
from paper_trading.execution_model import execute_paper, simulated_fill


class ExecutionContractParityTests(unittest.TestCase):
    def contract_decision(self, **overrides):
        intent = OrderIntent(
            symbol="BTCUSDT", side=overrides.get("side", "BUY"),
            requested_notional=overrides.get("requested_notional", 1000),
        )
        market = MarketSnapshot(
            reference_price=overrides.get("reference_price", 100),
            available_quote_volume=overrides.get("available_quote_volume", 10_000),
            observed_at="2026-08-24T20:00:00+00:00",
            best_bid=100, best_ask=100, quote_timestamp_ms=1787601600000,
            liquidity_timestamp_ms=1787601600000, source="TEST_BOOK",
        )
        rules = ExecutionRules(
            quantity_step=overrides.get("quantity_step", 1e-8),
            price_tick=overrides.get("price_tick", 1e-8),
            min_notional=overrides.get("min_notional", 5),
            require_liquidity=True,
        )
        return execute_paper(intent, market, rules)

    def test_wrapper_and_contract_have_identical_fill(self):
        decision = self.contract_decision()
        legacy = simulated_fill(
            symbol="BTCUSDT", reference_price=100,
            best_bid=100, best_ask=100, quote_timestamp_ms=1787601600000,
            liquidity_timestamp_ms=1787601600000, source="TEST_BOOK",
            observed_at="2026-08-24T20:00:00+00:00",
            requested_notional=1000, side="BUY",
            available_quote_volume=10_000, require_liquidity=True,
        )
        self.assertEqual(decision.status, "FILLED")
        self.assertEqual(decision.fill, legacy)

    def test_wrapper_and_contract_have_identical_rejection(self):
        decision = self.contract_decision(available_quote_volume=None)
        legacy = simulated_fill(
            symbol="BTCUSDT", reference_price=100,
            best_bid=100, best_ask=100, quote_timestamp_ms=1787601600000,
            liquidity_timestamp_ms=1787601600000, source="TEST_BOOK",
            observed_at="2026-08-24T20:00:00+00:00",
            requested_notional=1000, side="BUY",
            available_quote_volume=None, require_liquidity=True,
        )
        self.assertEqual(decision.status, "REJECTED")
        self.assertEqual(decision.reason, "MISSING_LIQUIDITY")
        self.assertIsNone(decision.fill)
        self.assertIsNone(legacy)

    def test_contract_contains_no_live_execution_capability(self):
        intent = OrderIntent("BTCUSDT", "BUY", 100)
        self.assertFalse(hasattr(intent, "api_key"))
        self.assertFalse(hasattr(intent, "order_endpoint"))


if __name__ == "__main__":
    unittest.main()
