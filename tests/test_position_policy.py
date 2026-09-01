import unittest
from datetime import datetime, timedelta, timezone

from paper_trading.position_policy import PositionState, evaluate, initialize


NOW = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


class PositionPolicyTests(unittest.TestCase):
    def test_entry_always_has_bounded_protective_stop(self):
        self.assertEqual(initialize("LONG", 100, 0.1).stop_price, 99.5)
        self.assertEqual(initialize("SHORT", 100, 9).stop_price, 105.0)

    def test_stop_exits_without_waiting_for_horizon(self):
        state = initialize("LONG", 100, 2)
        result = evaluate(state, 98, thesis_supported=True, now=NOW,
                          maximum_exit_at=NOW + timedelta(hours=4))
        self.assertEqual((result.action, result.reason), ("CLOSE", "PROTECTIVE_STOP"))

    def test_one_r_realizes_quarter_and_moves_above_cost(self):
        state = initialize("LONG", 100, 2)
        result = evaluate(state, 102, thesis_supported=True, now=NOW,
                          maximum_exit_at=NOW + timedelta(hours=4))
        self.assertEqual((result.action, result.close_fraction), ("REDUCE", 0.25))
        self.assertGreater(result.stop_price, 100)

    def test_three_opposing_events_invalidate_thesis(self):
        state = initialize("SHORT", 100, 2)
        for expected in (1, 2):
            decision = evaluate(state, 100, thesis_supported=False, now=NOW,
                                maximum_exit_at=NOW + timedelta(hours=4))
            self.assertEqual(decision.action, "HOLD")
            self.assertEqual(decision.opposing_events, expected)
            state = PositionState(**{**state.__dict__, "opposing_events": expected})
        decision = evaluate(state, 100, thesis_supported=False, now=NOW,
                            maximum_exit_at=NOW + timedelta(hours=4))
        self.assertEqual(decision.reason, "THESIS_INVALIDATED")

    def test_horizon_is_only_a_maximum_timeout(self):
        state = initialize("LONG", 100, 2)
        result = evaluate(state, 100, thesis_supported=True, now=NOW,
                          maximum_exit_at=NOW)
        self.assertEqual(result.reason, "MAXIMUM_HOLD_TIMEOUT")


if __name__ == "__main__":
    unittest.main()
