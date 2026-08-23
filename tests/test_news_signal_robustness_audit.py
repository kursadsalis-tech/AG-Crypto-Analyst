import unittest
from datetime import datetime, timedelta, timezone

from analysis.news_signal_robustness_audit import (
    build_report,
    chronological_split,
    cost_adjust,
)


def row(day, horizon="1h", direction="positive", directional=0.20, regime="UP"):
    news_time = datetime(2026, 1, day, tzinfo=timezone.utc)
    return {
        "news_time": news_time,
        "entry_time": news_time + timedelta(minutes=20),
        "exit_time": news_time + timedelta(hours=2),
        "horizon": horizon,
        "direction": direction,
        "directional_return": directional,
        "impact": 70,
        "confidence": 80,
        "regime": regime,
    }


class RobustnessAuditTests(unittest.TestCase):
    def test_cost_is_charged_to_signal_and_always_long(self):
        positive, negative = cost_adjust([
            row(1, direction="positive", directional=0.20),
            row(2, direction="negative", directional=0.20),
        ])
        self.assertAlmostEqual(positive["net_directional_return_pct"], 0.05)
        self.assertAlmostEqual(positive["always_long_net_return_pct"], 0.05)
        self.assertAlmostEqual(negative["net_directional_return_pct"], 0.05)
        self.assertAlmostEqual(negative["always_long_net_return_pct"], -0.35)

    def test_chronological_split_keeps_future_out_of_train(self):
        rows = [row(day) for day in range(1, 11)]
        train, validation = chronological_split(rows)
        self.assertLess(max(item["news_time"] for item in train),
                        min(item["news_time"] for item in validation))

    def test_report_never_enables_real_money(self):
        rows = []
        for day in range(1, 29):
            for horizon in ("1h", "4h", "24h"):
                regime = "UP" if day % 2 else "DOWN"
                rows.append(row(day, horizon=horizon, directional=0.40, regime=regime))
        report = build_report(rows)
        self.assertFalse(report["real_orders_allowed"])
        for result in report["horizons"].values():
            self.assertFalse(result["production_eligible"])


if __name__ == "__main__":
    unittest.main()
