import unittest

from paper_trading.governance import (
    AUTHORIZED_INITIAL_CAPITAL,
    AUTHORIZED_POLICY_VERSION,
    validate_agent_authority,
)


class PaperGovernanceTests(unittest.TestCase):
    def valid(self, **overrides):
        values = {
            "execution_mode": "PAPER_ONLY",
            "policy_version": AUTHORIZED_POLICY_VERSION,
            "initial_capital": AUTHORIZED_INITIAL_CAPITAL,
        }
        values.update(overrides)
        return validate_agent_authority(**values)

    def test_contract_denies_all_promotion_and_real_order_authority(self):
        contract = self.valid()
        self.assertTrue(contract["may_recommend"])
        self.assertFalse(contract["may_send_real_orders"])
        self.assertFalse(contract["may_promote_model"])
        self.assertFalse(contract["may_promote_policy"])
        self.assertFalse(contract["may_override_risk"])
        self.assertFalse(contract["may_change_capital"])

    def test_non_paper_mode_is_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "NON_PAPER"):
            self.valid(execution_mode="LIVE")

    def test_policy_promotion_is_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "POLICY_PROMOTION"):
            self.valid(policy_version="AUTO_PROMOTED_POLICY")

    def test_capital_change_is_blocked(self):
        with self.assertRaisesRegex(RuntimeError, "CAPITAL_CHANGE"):
            self.valid(initial_capital=20_000)


if __name__ == "__main__":
    unittest.main()
