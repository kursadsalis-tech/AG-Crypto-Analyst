import json
import math
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from analysis import realtime_technical_context
from analysis import technical_trade_plan as plan
from database import technical_bar_store


NOW = datetime(2026, 9, 23, 1, 30, tzinfo=timezone.utc)


class TechnicalTradePlanTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        technical_bar_store.ensure_table(self.con)
        realtime_technical_context.ensure_table(self.con)
        self.save_higher_context()

    def tearDown(self):
        self.con.close()

    def save_higher_context(self):
        context = {
            "version": realtime_technical_context.VERSION,
            "symbol": "BTCUSDT",
            "generated_at": (NOW - timedelta(minutes=2)).isoformat(),
            "as_of": (NOW - timedelta(minutes=30)).isoformat(),
            "market_scope": "TEST_LOCAL_ONLY",
            "reference_price": 100.0,
            "levels": {
                "support_1h_72": 98.5,
                "resistance_1h_72": 103.5,
                "support_4h_20": 97.0,
                "resistance_4h_20": 105.0,
            },
            "observation_only": True,
            "real_order_allowed": False,
            "timeframes": ["1h", "4h"],
            "advanced_1h": {},
            "structure_4h": {"trend": "BULLISH"},
            "summary": {
                "trend_1h": "BULLISH",
                "trend_4h": "BULLISH",
                "alignment": "ALIGNED_BULLISH",
            },
            "authority": {
                "direction_input": False,
                "ranking_input": False,
                "confidence_input": False,
                "sizing_input": False,
                "stop_input": False,
                "purpose": "FORWARD_OBSERVATION_AND_LATER_CHALLENGER_EVIDENCE",
            },
        }
        realtime_technical_context.save_latest(self.con, context, 0.2)
        self.con.commit()

    @staticmethod
    def bars(count=360, end=NOW):
        start = end - timedelta(minutes=count)
        rows = []
        previous = 100.0
        for index in range(count):
            opened = start + timedelta(minutes=index)
            drift = 0.012 * index
            wave = 0.08 * math.sin(index / 9.0)
            close = 100.0 + drift + wave
            opened_price = previous
            high = max(opened_price, close) + 0.12
            low = min(opened_price, close) - 0.12
            rows.append({
                "symbol": "BTCUSDT",
                "open_time_ms": int(opened.timestamp() * 1000),
                "close_time_ms": int(opened.timestamp() * 1000) + 59_999,
                "open": opened_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": 100.0 + index,
                "quote_volume": 10_000.0 + 10.0 * index,
                "trade_count": 100 + index,
                "taker_buy_quote_volume": 5_000.0 + 5.0 * index,
                "source": "TEST_BINANCE_WS",
            })
            previous = close
        return rows

    @staticmethod
    def candidate(kind="PRICE_ACTION", horizon=4):
        return {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "horizon": horizon,
            "current_price": 104.0,
            "evidence": {
                "evidence_minute_end_ms": int((NOW - timedelta(minutes=1)).timestamp() * 1000),
                "primary_signal_source": (
                    "PRICE_ACTION" if kind == "PRICE_ACTION" else "MARKET_BEHAVIOR"
                ),
                "event": {"kind": kind},
            },
        }

    def seed(self, rows=None):
        technical_bar_store.upsert_bars(self.con, rows or self.bars())
        self.con.commit()

    def test_adaptive_profiles_do_not_blindly_use_every_timeframe(self):
        fast = plan.adaptive_timeframes(self.candidate("PRICE_ACTION"))
        structure = plan.adaptive_timeframes(self.candidate("MARKET_STRUCTURE"))
        regime = plan.adaptive_timeframes(self.candidate("MACRO", horizon=24 * 7))
        self.assertEqual(fast["profile"], "FAST_PRICE_ACTION")
        self.assertEqual(fast["requested"], ["1m", "5m", "15m", "1h"])
        self.assertEqual(structure["profile"], "STRUCTURE_BREAKOUT")
        self.assertEqual(structure["requested"], ["5m", "15m", "1h", "4h"])
        self.assertIn("1M", regime["requested"])
        self.assertNotEqual(fast["requested"], structure["requested"])

    def test_local_cache_build_has_real_pretrade_authority_and_geometry(self):
        self.seed()
        result = plan.build_for_candidate(
            self.con, self.candidate("PRICE_ACTION"), "SPOT", now=NOW
        )
        self.assertEqual(result["version"], plan.VERSION)
        self.assertFalse(result["network_io"])
        self.assertEqual(result["source"], "BINANCE_CANONICAL_LOCAL_CACHE")
        self.assertEqual(result["analysis_market_type"], "USD_M_PERPETUAL")
        self.assertEqual(result["execution_market_type"], "SPOT")
        self.assertEqual(result["profile"], "FAST_PRICE_ACTION")
        self.assertEqual(result["timeframes_used"], ["1m", "5m", "15m", "1h"])
        self.assertGreater(result["entry"]["high"], result["entry"]["low"])
        self.assertFalse(result["entry"]["chase_allowed"])
        self.assertTrue(result["risk"]["risk_compatible"])
        self.assertGreater(result["risk"]["stop_distance_pct"], 0)
        self.assertGreater(
            result["targets"]["tp2_reward_pct"],
            result["targets"]["tp1_reward_pct"],
        )
        self.assertTrue(result["authority"]["ranking_input"])
        self.assertTrue(result["authority"]["sizing_input"])
        self.assertTrue(result["authority"]["entry_input"])
        self.assertTrue(result["authority"]["stop_input"])
        self.assertTrue(result["authority"]["take_profit_input"])
        self.assertFalse(result["authority"]["direction_override"])
        self.assertFalse(result["real_order_allowed"])

    def test_market_structure_uses_different_frame_mix(self):
        self.seed()
        result = plan.build_for_candidate(
            self.con, self.candidate("MARKET_STRUCTURE"),
            "USD_M_PERPETUAL", now=NOW,
        )
        self.assertEqual(result["profile"], "STRUCTURE_BREAKOUT")
        self.assertEqual(result["timeframes_used"], ["5m", "15m", "1h", "4h"])
        self.assertNotIn("1m", result["timeframes_used"])

    def test_stale_local_bars_fail_closed(self):
        self.seed(self.bars(end=NOW - timedelta(minutes=10)))
        with self.assertRaisesRegex(ValueError, "stale local technical bars"):
            plan.build_for_candidate(
                self.con, self.candidate("PRICE_ACTION"), "SPOT", now=NOW
            )

    def test_gap_near_now_is_not_bridged(self):
        rows = self.bars()
        del rows[-10]
        self.seed(rows)
        with self.assertRaisesRegex(ValueError, "insufficient fresh contiguous"):
            plan.build_for_candidate(
                self.con, self.candidate("PRICE_ACTION"), "SPOT", now=NOW
            )

    def test_plan_freshness_requires_replan_for_ttl_or_material_price_move(self):
        self.seed()
        result = plan.build_for_candidate(
            self.con, self.candidate("PRICE_ACTION"), "SPOT", now=NOW
        )
        self.assertIsNone(plan.replan_reason(
            result, result["reference_price"], now=NOW + timedelta(seconds=30)
        ))
        self.assertEqual(
            plan.replan_reason(
                result, result["reference_price"],
                now=NOW + timedelta(seconds=plan.PLAN_TTL_SECONDS + 1),
            ),
            "PLAN_TTL_EXPIRED",
        )
        threshold = result["freshness"]["replan_price_move_pct"]
        moved = result["reference_price"] * (1.0 + (threshold + 0.05) / 100.0)
        self.assertEqual(
            plan.replan_reason(result, moved, now=NOW + timedelta(seconds=30)),
            "PRICE_MOVED_MATERIALLY",
        )

    def test_long_regime_does_not_invent_missing_daily_or_weekly_context(self):
        self.seed()
        with self.assertRaisesRegex(ValueError, "required long-regime context"):
            plan.build_for_candidate(
                self.con, self.candidate("MACRO", horizon=24 * 7),
                "USD_M_PERPETUAL", now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
