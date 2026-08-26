import unittest

from paper_trading.capital_gate import (
    ExecutionTrace,
    PromotionAttestations,
    evaluate_capital_gate,
    reconcile_execution_traces,
)


class CapitalGateTests(unittest.TestCase):
    def trace(self, **changes):
        values = {
            "symbol": "BTCUSDT", "side": "BUY", "status": "FILLED",
            "reason": "PAPER_FILL", "price": 100.1, "quantity": 1.0,
            "fee": 0.05,
        }
        values.update(changes)
        return ExecutionTrace(**values)

    def test_matching_shadow_and_dry_run_can_pass_reconciliation(self):
        result = reconcile_execution_traces([self.trace()], [self.trace()])
        self.assertTrue(result.passed)
        self.assertEqual(result.compared, 1)

    def test_empty_or_mismatched_traces_fail_closed(self):
        self.assertFalse(reconcile_execution_traces([], []).passed)
        result = reconcile_execution_traces(
            [self.trace()], [self.trace(price=100.2)]
        )
        self.assertFalse(result.passed)
        self.assertIn("TRACE_0_PRICE_MISMATCH", result.mismatches)

    def test_missing_approval_and_attestation_are_independent_blocks(self):
        reconciliation = reconcile_execution_traces([self.trace()], [self.trace()])
        result = evaluate_capital_gate(reconciliation, PromotionAttestations())
        self.assertFalse(result.allowed)
        self.assertIn("EXPLICIT_USER_APPROVAL_MISSING", result.reasons)
        self.assertIn("ORCHESTRATOR_ATTESTATION_MISSING", result.reasons)

    def test_reviewed_code_gate_stays_closed_even_with_all_evidence(self):
        reconciliation = reconcile_execution_traces([self.trace()], [self.trace()])
        result = evaluate_capital_gate(
            reconciliation,
            PromotionAttestations("user-record-1", "orchestrator-record-1"),
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.reasons, ("REVIEWED_CODE_GATE_CLOSED",))


if __name__ == "__main__":
    unittest.main()
