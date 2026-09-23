import unittest

from data import binance_liquidation_channel as liquidation


class BinanceLiquidationChannelTests(unittest.TestCase):
    def test_sell_force_order_is_long_liquidation(self):
        event = liquidation.normalize_event({
            "e": "forceOrder", "E": 1000,
            "o": {
                "s": "BTCUSDT", "S": "SELL", "ap": "100",
                "z": "2", "T": 999,
            },
        })
        self.assertEqual(event["instrument"], "BTC-USDT-SWAP")
        self.assertEqual(event["liquidated_side"], "LONG")
        self.assertEqual(event["notional_usd_estimate"], 200.0)

    def test_current_market_route_and_cm_filter(self):
        self.assertEqual(
            liquidation.WS_URL,
            "wss://fstream.binance.com/market/ws/!forceOrder@arr",
        )
        self.assertIsNone(liquidation.normalize_event({
            "e": "forceOrder", "st": 2,
            "o": {
                "s": "BTCUSDT", "S": "SELL", "ap": "100",
                "z": "2", "T": 999,
            },
        }))

    def test_buy_force_order_is_short_liquidation(self):
        event = liquidation.normalize_event({
            "e": "forceOrder", "o": {
                "s": "ETHUSDT", "S": "BUY", "p": "50",
                "q": "3", "T": 999,
            },
        })
        self.assertEqual(event["liquidated_side"], "SHORT")


if __name__ == "__main__":
    unittest.main()
