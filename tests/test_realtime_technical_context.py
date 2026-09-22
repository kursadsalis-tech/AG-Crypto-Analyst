import json
import math
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from analysis import realtime_technical_context as rtc
from database.runtime_store import REMOTE_RUNTIME_TABLES


class RealtimeTechnicalContextTests(unittest.TestCase):
    @staticmethod
    def candles(count=169):
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        rows = []
        for index in range(count):
            opened = start + timedelta(hours=index)
            close = 100.0 + 0.08 * index + math.sin(index / 7.0)
            rows.append([
                int(opened.timestamp() * 1000),
                close - 0.05,
                close + 0.40,
                close - 0.40,
                close,
                100.0,
                int((opened + timedelta(hours=1)).timestamp() * 1000) - 1,
                1000.0 + 5.0 * index,
                0, 0, 0, 0,
            ])
        return rows

    def test_build_is_finite_causal_and_excludes_incomplete_4h_bucket(self):
        generated = datetime(2026, 9, 8, 1, 5, tzinfo=timezone.utc)
        context = rtc.build(
            self.candles(), symbol="BTCUSDT", generated_at=generated,
            market_scope="TEST",
        )
        self.assertEqual(context["version"], rtc.VERSION)
        self.assertTrue(context["observation_only"])
        self.assertFalse(context["real_order_allowed"])
        self.assertEqual(context["timeframes"], ["1h", "4h"])
        self.assertGreaterEqual(context["structure_4h"]["bar_count"], 40)
        self.assertLess(
            datetime.fromisoformat(context["structure_4h"]["as_of"]),
            datetime.fromisoformat(context["as_of"]),
        )
        flattened = list(context["advanced_1h"].values()) + [
            value for value in context["structure_4h"].values()
            if isinstance(value, (int, float))
        ]
        self.assertTrue(all(math.isfinite(float(value)) for value in flattened))
        self.assertEqual(
            context["authority"],
            {
                "direction_input": False,
                "ranking_input": False,
                "confidence_input": False,
                "sizing_input": False,
                "stop_input": False,
                "purpose": "FORWARD_OBSERVATION_AND_LATER_CHALLENGER_EVIDENCE",
            },
        )

    def test_latest_cache_is_local_only_and_freshness_bounded(self):
        con = sqlite3.connect(":memory:")
        try:
            generated = datetime(2026, 9, 8, 1, 5, tzinfo=timezone.utc)
            context = rtc.build(
                self.candles(), symbol="BTCUSDT", generated_at=generated,
                market_scope="TEST",
            )
            rtc.save_latest(con, context, 1.25)
            con.commit()
            loaded = rtc.load_latest(
                con, "BTCUSDT", now=generated + timedelta(minutes=5)
            )
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["summary"], context["summary"])
            self.assertIsNone(rtc.load_latest(
                con, "BTCUSDT", now=generated + timedelta(minutes=31)
            ))
        finally:
            con.close()
        self.assertNotIn("paper_technical_context_latest", REMOTE_RUNTIME_TABLES)

    def test_less_than_169_hourly_candles_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "insufficient closed 1h candles"):
            rtc.build(self.candles(168), symbol="BTCUSDT")


if __name__ == "__main__":
    unittest.main()
