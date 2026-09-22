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

    def test_technical_stop_and_targets_override_legacy_r_multiples(self):
        state = initialize(
            "LONG", 100, 2,
            stop_price=98.5, tp1_price=101.2, tp2_price=103.4,
        )
        self.assertAlmostEqual(state.stop_price, 98.5)
        self.assertAlmostEqual(state.stop_distance_pct, 1.5)
        before = evaluate(
            state, 101.0, thesis_supported=True, now=NOW,
            maximum_exit_at=NOW + timedelta(hours=4),
        )
        self.assertEqual(before.action, "HOLD")
        first = evaluate(
            state, 101.2, thesis_supported=True, now=NOW,
            maximum_exit_at=NOW + timedelta(hours=4),
        )
        self.assertEqual(
            (first.action, first.reason),
            ("REDUCE", "TAKE_PROFIT_TECHNICAL_1"),
        )
        after_first = PositionState(**{
            **state.__dict__,
            "tp1_done": True,
            "stop_price": first.stop_price,
        })
        second = evaluate(
            after_first, 103.4, thesis_supported=True, now=NOW,
            maximum_exit_at=NOW + timedelta(hours=4),
        )
        self.assertEqual(
            (second.action, second.reason),
            ("REDUCE", "TAKE_PROFIT_TECHNICAL_2"),
        )

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

    def test_missing_flow_does_not_erase_existing_opposition(self):
        state = initialize("LONG", 100, 2)
        state = PositionState(**{**state.__dict__, "opposing_events": 2})
        result = evaluate(state, 100, thesis_supported=None, now=NOW,
                          maximum_exit_at=NOW + timedelta(hours=4))
        self.assertEqual(result.opposing_events, 2)


if __name__ == "__main__":
    unittest.main()
