import math
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from analysis import realtime_technical_context as rtc
from analysis import technical_trade_plan as plan
from database import technical_bar_store as store


NOW = datetime(2026, 9, 23, 2, 30, tzinfo=timezone.utc)


def minute_bars(symbol="BTCUSDT", count=720, slope=0.002):
    start = NOW - timedelta(minutes=count)
    rows = []
    for index in range(count):
        opened = start + timedelta(minutes=index)
        price = 100.0 + slope * index + 0.08 * math.sin(index / 13)
        rows.append({
            "symbol": symbol,
            "open_time_ms": int(opened.timestamp() * 1000),
            "close_time_ms": int((opened + timedelta(minutes=1)).timestamp() * 1000) - 1,
            "open": price - 0.02,
            "high": price + 0.08,
            "low": price - 0.08,
            "close": price,
            "volume": 10.0,
            "quote_volume": 1000.0,
            "trade_count": 20,
            "taker_buy_quote_volume": 550.0,
            "source": "TEST",
        })
    return rows


def context(symbol="BTCUSDT", regime=None):
    return {
        "version": rtc.VERSION,
        "symbol": symbol,
        "generated_at": (NOW - timedelta(seconds=10)).isoformat(),
        "as_of": (NOW - timedelta(hours=1)).isoformat(),
        "market_scope": "TEST",
        "observation_only": True,
        "real_order_allowed": False,
        "timeframes": ["1h", "4h", *((regime or {}).keys())],
        "advanced_1h": {},
        "structure_4h": {"trend": "BULLISH"},
        "levels": {
            "support_1h_72": 99.0,
            "resistance_1h_72": 104.0,
            "support_4h_20": 98.0,
            "resistance_4h_20": 106.0,
        },
        "regime": regime or {},
        "summary": {
            "trend_1h": "BULLISH",
            "trend_4h": "BULLISH",
            "alignment": "ALIGNED_BULLISH",
        },
        "authority": {
            "direction_input": False, "ranking_input": False,
            "confidence_input": False, "sizing_input": False,
            "stop_input": False, "purpose": "SOURCE_CONTEXT",
        },
    }


def candidate(horizon=4, event_kind="MARKET_STRUCTURE"):
    return {
        "symbol": "BTCUSDT", "direction": "LONG", "horizon": horizon,
        "evidence": {"event": {"kind": event_kind}},
    }


class AdaptiveTechnicalTradePlanTests(unittest.TestCase):
    def setUp(self):
        plan._PLAN_CACHE.clear()
        self.con = sqlite3.connect(":memory:")
        store.ensure_table(self.con)
        rtc.ensure_table(self.con)
        store.upsert_bars(self.con, minute_bars())
        rtc.save_latest(self.con, context(), 1.0)
        self.con.commit()

    def tearDown(self):
        self.con.close()
        plan._PLAN_CACHE.clear()

    def test_plan_is_network_free_adaptive_and_structural(self):
        result = plan.build_for_candidate(
            self.con, candidate(), now=NOW, market_type="USD_M_PERPETUAL"
        )
        self.assertEqual(
            result["timeframes_used"], ["1m", "5m", "15m", "1h", "4h"]
        )
        self.assertTrue(result["authority"]["entry_input"])
        self.assertTrue(result["authority"]["stop_input"])
        self.assertTrue(result["authority"]["take_profit_input"])
        self.assertFalse(result["authority"]["direction_override"])
        self.assertFalse(result["authority"]["single_indicator_hard_veto"])
        self.assertLess(
            result["risk"]["invalidation_price"], result["entry"]["high"]
        )
        self.assertGreater(
            result["targets"]["tp2_price"], result["targets"]["tp1_price"]
        )

    def test_spot_scaling_freezes_basis_without_moving_plan_offsets(self):
        result = plan.build_for_candidate(
            self.con, candidate(), now=NOW, market_type="SPOT"
        )
        levels = plan.execution_levels(result, 102.0)
        repeated = plan.execution_levels(
            result, 105.0, basis_ratio=levels["basis_ratio"]
        )
        self.assertNotEqual(levels["basis_ratio"], 1.0)
        self.assertEqual(levels["basis_ratio"], repeated["basis_ratio"])
        self.assertAlmostEqual(levels["entry_low"], repeated["entry_low"])
        self.assertAlmostEqual(levels["tp2_price"], repeated["tp2_price"])

    def test_macro_candidate_requires_available_long_regime_context(self):
        with self.assertRaisesRegex(ValueError, "long-regime"):
            plan.build_for_candidate(
                self.con, candidate(24, "MACRO"), now=NOW,
                market_type="USD_M_PERPETUAL",
            )
        self.con.execute("DELETE FROM paper_technical_context_latest")
        regime = {
            name: {"trend": "BULLISH"} for name in ("1d", "3d", "1w", "1M")
        }
        rtc.save_latest(self.con, context(regime=regime), 1.0)
        self.con.commit()
        plan._PLAN_CACHE.clear()
        result = plan.build_for_candidate(
            self.con, candidate(24, "MACRO"), now=NOW,
            market_type="USD_M_PERPETUAL",
        )
        self.assertEqual(
            result["timeframes_used"],
            ["1m", "5m", "15m", "1h", "4h", "1d", "3d", "1w", "1M"],
        )

    def test_stale_minute_cache_fails_closed(self):
        old = NOW - timedelta(minutes=10)
        self.con.execute(
            "UPDATE technical_minute_bars SET close_time_ms=close_time_ms-600000,"
            " open_time_ms=open_time_ms-600000"
        )
        self.con.commit()
        plan._PLAN_CACHE.clear()
        with self.assertRaisesRegex(ValueError, "stale"):
            plan.build_for_candidate(
                self.con, candidate(), now=NOW, market_type="USD_M_PERPETUAL"
            )


if __name__ == "__main__":
    unittest.main()
