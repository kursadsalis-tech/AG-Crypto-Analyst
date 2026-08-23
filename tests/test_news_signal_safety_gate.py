import sqlite3
import unittest

from risk_manager import signal_risk_gate


class NewsSignalSafetyGateTests(unittest.TestCase):
    def test_strong_forward_summary_cannot_bypass_robustness_rejection(self):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            """
            CREATE TABLE forward_shadow_predictions (
                model_version TEXT, status TEXT, validation_status TEXT,
                multifactor_eligible INTEGER, hit INTEGER,
                directional_return REAL
            )
            """
        )
        connection.executemany(
            "INSERT INTO forward_shadow_predictions VALUES (?, ?, ?, ?, ?, ?)",
            [
                (signal_risk_gate.MODEL_VERSION, "SETTLED", "VERIFIED", 1, 1, 1.0)
                for _ in range(100)
            ],
        )

        evidence = signal_risk_gate.forward_evidence(connection)

        self.assertEqual(evidence["state"], "SHADOW_ONLY")
        self.assertIn("NEWS_DIRECTIONAL_ROBUSTNESS_REJECTED", evidence["reasons"])
        connection.close()


if __name__ == "__main__":
    unittest.main()
