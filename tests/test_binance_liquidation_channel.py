import unittest

from data.binance_liquidation_channel import normalize_event


class BinanceLiquidationChannelTests(unittest.TestCase):
    def test_sell_force_order_is_long_liquidation(self):
        event = normalize_event({
            "e": "forceOrder", "E": 1000,
            "o": {
                "s": "BTCUSDT", "S": "SELL", "ap": "100",
                "z": "2", "T": 999,
            },
        })
        self.assertEqual(event["instrument"], "BTC-USDT-SWAP")
        self.assertEqual(event["liquidated_side"], "LONG")
        self.assertEqual(event["notional_usd_estimate"], 200.0)

    def test_buy_force_order_is_short_liquidation(self):
        event = normalize_event({
            "e": "forceOrder", "o": {
                "s": "ETHUSDT", "S": "BUY", "p": "50",
                "q": "3", "T": 999,
            },
        })
        self.assertEqual(event["liquidated_side"], "SHORT")


if __name__ == "__main__":
    unittest.main()
