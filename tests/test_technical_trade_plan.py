import math
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from analysis import technical_trade_plan as ttp


NOW = datetime(2026, 9, 23, 0, 30, tzinfo=timezone.utc)


def candles(count=720, *, slope=0.01):
    start = NOW - timedelta(minutes=count)
    rows = []
    for index in range(count):
        opened = start + timedelta(minutes=index)
        base = 100.0 + slope * index + 0.15 * math.sin(index / 17.0)
        close = base + 0.02 * math.sin(index / 5.0)
        rows.append([
            int(opened.timestamp() * 1000),
            close - 0.03,
            close + 0.10,
            close - 0.10,
            close,
            10.0,
            int((opened + timedelta(minutes=1)).timestamp() * 1000) - 1,
            1000.0,
            0, 0, 0, 0,
        ])
    return rows


def higher(bullish=True):
    return {
        "summary": {
            "trend_1h": "BULLISH" if bullish else "BEARISH",
            "trend_4h": "BULLISH" if bullish else "BEARISH",
        },
        "levels": {
            "support_1h_72": 103.0,
            "resistance_1h_72": 109.0,
            "support_4h_20": 101.0,
            "resistance_4h_20": 111.0,
        },
    }


class TechnicalTradePlanTests(unittest.TestCase):
    def test_long_plan_uses_multitimeframe_structure_for_entry_stop_targets(self):
        plan = ttp.build_plan(
            candles(), symbol="BTCUSDT", direction="LONG",
            market_type="USD_M_PERPETUAL", higher_context=higher(True),
            generated_at=NOW,
        )
        self.assertEqual(plan["source"], "BINANCE_CANONICAL")
        self.assertEqual(plan["timeframes_used"], ["1m", "5m", "15m", "1h", "4h"])
        self.assertTrue(plan["authority"]["entry_input"])
        self.assertTrue(plan["authority"]["stop_input"])
        self.assertTrue(plan["authority"]["take_profit_input"])
        self.assertFalse(plan["authority"]["direction_override"])
        self.assertFalse(plan["authority"]["single_indicator_hard_veto"])
        self.assertLess(plan["entry"]["low"], plan["entry"]["high"])
        self.assertLess(plan["risk"]["invalidation_price"], plan["entry"]["high"])
        self.assertGreater(plan["targets"]["tp1_price"], plan["entry"]["low"])
        self.assertGreater(plan["targets"]["tp2_price"], plan["targets"]["tp1_price"])
        self.assertGreaterEqual(plan["risk"]["stop_distance_pct"], 0.5)
        self.assertLessEqual(plan["quality_multiplier"], 1.15)

    def test_opposing_technical_context_softens_but_does_not_hard_veto(self):
        plan = ttp.build_plan(
            candles(slope=-0.01), symbol="BTCUSDT", direction="LONG",
            market_type="USD_M_PERPETUAL", higher_context=higher(False),
            generated_at=NOW,
        )
        self.assertEqual(plan["relation_to_behavior_direction"], "OPPOSES_DIRECTION")
        self.assertLess(plan["quality_multiplier"], 1.0)
        self.assertIn("entry", plan)
        self.assertIn("targets", plan)

    def test_fetch_closed_1m_uses_selected_binance_market(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = candles()
        session = Mock()
        session.get.return_value = response
        rows = ttp.fetch_closed_1m(
            "BTCUSDT", "USD_M_PERPETUAL", now=NOW, session=session
        )
        self.assertGreaterEqual(len(rows), ttp.MIN_ONE_MINUTE_CANDLES)
        called_url = session.get.call_args.args[0]
        self.assertEqual(called_url, ttp.USDM_KLINES_URL)
        self.assertEqual(session.get.call_args.kwargs["params"]["interval"], "1m")


if __name__ == "__main__":
    unittest.main()
