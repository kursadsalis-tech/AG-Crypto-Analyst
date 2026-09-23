import inspect
import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from analysis import realtime_technical_context as rtc
from analysis import technical_trade_plan as plan


NOW = datetime(2026, 9, 23, 1, 0, tzinfo=timezone.utc)


class TechnicalTradePlanTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        rtc.ensure_table(self.con)
        self.con.execute("""
            CREATE TABLE market_behavior_minute_features(
                instrument TEXT NOT NULL,
                market_data_provider TEXT NOT NULL,
                eligible_for_learning INTEGER NOT NULL,
                minute_start_timestamp_ms INTEGER NOT NULL,
                minute_end_timestamp_ms INTEGER NOT NULL,
                mid_price_first REAL,
                mid_price_high REAL,
                mid_price_low REAL,
                mid_price_last REAL,
                aggressive_buy_notional_usd REAL NOT NULL DEFAULT 0,
                aggressive_sell_notional_usd REAL NOT NULL DEFAULT 0
            )
        """)
        self.addCleanup(self.con.close)

    def support_context(
        self, *, generated=NOW - timedelta(minutes=2),
        trend_1h="BULLISH", trend_4h="BULLISH",
        support_1h=98.0, support_4h=96.0,
    ):
        return {
            "version": rtc.VERSION,
            "symbol": "BTCUSDT",
            "generated_at": generated.isoformat(),
            "as_of": (NOW - timedelta(hours=1)).isoformat(),
            "daily_as_of": (NOW - timedelta(days=1)).isoformat(),
            "market_scope": "TEST",
            "observation_only": True,
            "real_order_allowed": False,
            "timeframes": ["1h", "4h", "1d", "3d", "1w", "1M"],
            "advanced_1h": {},
            "structure_4h": {},
            "long_frames": {
                "1d": {
                    "support": 90.0, "resistance": 112.0,
                    "atr": 2.5, "trend": "BULLISH",
                },
                "3d": {
                    "support": 88.0, "resistance": 115.0,
                    "atr": 4.0, "trend": "BULLISH",
                },
                "1w": {
                    "support": 82.0, "resistance": 120.0,
                    "atr": 6.0, "trend": "BULLISH",
                },
                "1M": {
                    "support": 75.0, "resistance": 130.0,
                    "atr": 10.0, "trend": "BULLISH",
                },
            },
            "levels": {
                "latest_1h": 100.0,
                "support_1h_72": support_1h,
                "resistance_1h_72": 108.0,
                "atr_1h": 1.0,
                "support_4h_20": support_4h,
                "resistance_4h_20": 110.0,
                "atr_4h": 2.0,
            },
            "summary": {
                "trend_1h": trend_1h,
                "trend_4h": trend_4h,
                "trend_1d": "BULLISH",
                "trend_3d": "BULLISH",
                "trend_1w": "BULLISH",
                "trend_1M": "BULLISH",
                "alignment": "ALIGNED_BULLISH",
                "rsi14": 55.0,
                "atr14_pct": 1.0,
                "breakout24": 0.0,
            },
            "authority": {
                "direction_input": False,
                "ranking_input": False,
                "confidence_input": False,
                "sizing_input": False,
                "stop_input": False,
                "purpose": "SUPPORT_CONTEXT_FOR_AUTHORITATIVE_TECHNICAL_TRADE_PLAN",
            },
        }

    def save_context(self, context=None):
        context = context or self.support_context()
        rtc.save_latest(self.con, context, 0.5)
        self.con.commit()

    def add_minutes(self, count=420):
        last_end = int((NOW - timedelta(minutes=1)).timestamp() * 1000)
        first_start = last_end - count * 60_000
        for index in range(count):
            start = first_start + index * 60_000
            end = start + 60_000
            close = 96.5 + index * 0.01
            opened = close - 0.01
            self.con.execute("""
                INSERT INTO market_behavior_minute_features VALUES(
                    ?, 'BINANCE', 1, ?, ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                "BTC-USDT-SWAP", start, end,
                opened, close + 0.05, opened - 0.05, close,
                1000.0 + index, 700.0,
            ))
        self.con.commit()
        return last_end, 96.5 + (count - 1) * 0.01

    def candidate(self, decision_id=1, horizon=4, price=100.0,
                  primary="PRICE_ACTION", minute_end=None):
        if minute_end is None:
            minute_end = int((NOW - timedelta(minutes=1)).timestamp() * 1000)
        return {
            "decision_id": decision_id,
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "horizon": horizon,
            "current_price": price,
            "evidence": {
                "evidence_minute_end_ms": minute_end,
                "primary_signal_source": primary,
            },
        }

    def test_price_action_uses_available_lower_frames_plus_1h_4h(self):
        self.save_context()
        minute_end, current = self.add_minutes()
        result = plan.build_for_candidate(
            self.con,
            self.candidate(price=current, minute_end=minute_end),
            now=NOW,
        )
        self.assertEqual(
            result["timeframes_used"], ["1m", "5m", "15m", "1h", "4h"]
        )
        self.assertTrue(result["risk"]["risk_compatible"])
        self.assertLess(
            result["risk"]["invalidation_price"],
            result["entry"]["high"],
        )
        self.assertGreater(
            result["targets"]["tp1_price"],
            result["entry"]["anchor"],
        )
        self.assertFalse(result["authority"]["direction_override"])
        self.assertTrue(result["authority"]["ranking_input"])
        self.assertFalse(result["authority"]["network_call_in_decision_path"])

    def test_missing_lower_history_still_builds_from_prepared_1h_4h_context(self):
        self.save_context()
        result = plan.build_for_candidate(
            self.con, self.candidate(decision_id=2), now=NOW
        )
        self.assertEqual(result["timeframes_used"], ["1h", "4h"])
        self.assertEqual(result["setup_atr_timeframe"], "1h")
        self.assertEqual(result["lower_frames"], {})

    def test_conflicted_higher_context_adaptively_adds_long_regime(self):
        context = self.support_context(
            trend_1h="BULLISH", trend_4h="BEARISH"
        )
        context["summary"]["alignment"] = "MIXED"
        self.save_context(context)
        minute_end, current = self.add_minutes()
        result = plan.build_for_candidate(
            self.con,
            self.candidate(
                decision_id=3, price=current, minute_end=minute_end
            ),
            now=NOW,
        )
        self.assertIn("1d", result["timeframes_used"])
        self.assertIn("3d", result["timeframes_used"])
        self.assertNotEqual(
            result["timeframes_used"],
            ["1m", "5m", "15m", "1h", "4h"],
        )

    def test_24h_candidate_can_use_weekly_and_monthly_context(self):
        self.save_context()
        result = plan.build_for_candidate(
            self.con,
            self.candidate(decision_id=4, horizon=24),
            now=NOW,
        )
        self.assertIn("1d", result["timeframes_used"])
        self.assertIn("3d", result["timeframes_used"])
        self.assertIn("1w", result["timeframes_used"])
        self.assertIn("1M", result["timeframes_used"])

    def test_far_structure_is_marked_risk_incompatible_not_clamped_inward(self):
        context = self.support_context(
            support_1h=80.0, support_4h=75.0
        )
        context["long_frames"]["1d"]["support"] = 70.0
        context["long_frames"]["3d"]["support"] = 65.0
        context["long_frames"]["1w"]["support"] = 60.0
        context["long_frames"]["1M"]["support"] = 50.0
        self.save_context(context)
        result = plan.build_for_candidate(
            self.con, self.candidate(decision_id=5), now=NOW
        )
        self.assertFalse(result["risk"]["risk_compatible"])
        self.assertGreater(result["risk"]["stop_distance_pct"], 5.0)

    def test_decision_path_contains_no_network_client(self):
        source = inspect.getsource(plan)
        self.assertNotIn("import requests", source)
        self.assertNotIn("requests.", source)
        self.assertNotIn("fapi.binance.com", source)
        self.assertNotIn("data-api.binance.vision", source)


if __name__ == "__main__":
    unittest.main()
