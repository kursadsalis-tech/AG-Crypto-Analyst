import unittest

from runtime.observer_health_alert import evaluate


class ObserverHealthAlertTests(unittest.TestCase):
    def test_three_consecutive_degraded_checks_open_one_alert(self):
        health = {"state": "CURRENT", "status": "DEGRADED"}
        state = {"consecutive_unhealthy": 0, "alert_open": False}
        state, action, _ = evaluate(health, state, threshold=3)
        self.assertIsNone(action)
        state, action, _ = evaluate(health, state, threshold=3)
        self.assertIsNone(action)
        state, action, _ = evaluate(health, state, threshold=3)
        self.assertEqual(action, "ALERT")
        state, action, _ = evaluate(health, state, threshold=3)
        self.assertIsNone(action)

    def test_recovery_is_emitted_after_open_alert(self):
        health = {"state": "CURRENT", "status": "HEALTHY"}
        state, action, reason = evaluate(
            health, {"consecutive_unhealthy": 4, "alert_open": True}, threshold=3,
        )
        self.assertEqual((action, reason), ("RECOVERY", "HEALTHY"))
        self.assertFalse(state["alert_open"])

    def test_stale_health_alerts_immediately(self):
        health = {"state": "STALE", "status": "HEALTHY"}
        _, action, _ = evaluate(
            health, {"consecutive_unhealthy": 0, "alert_open": False}, threshold=3,
        )
        self.assertEqual(action, "ALERT")


if __name__ == "__main__":
    unittest.main()
