import unittest

from data.adaptive_market_scanner import build_attention_route


class AdaptiveMarketScannerTests(unittest.TestCase):
    def test_keeps_context_and_routes_limited_dynamic_candidates(self):
        rows = [
            {"instrument": "BTC-USDT-SWAP", "change_24h_pct": 1,
             "quote_volume_24h_estimate_usd": 1000, "spread_bps": 1},
            {"instrument": "ETH-USDT-SWAP", "change_24h_pct": 2,
             "quote_volume_24h_estimate_usd": 900, "spread_bps": 1},
            {"instrument": "FAST-USDT-SWAP", "change_24h_pct": 20,
             "quote_volume_24h_estimate_usd": 800, "spread_bps": 2},
            {"instrument": "SLOW-USDT-SWAP", "change_24h_pct": 0.1,
             "quote_volume_24h_estimate_usd": 10, "spread_bps": 10},
        ]
        result = build_attention_route({"selected": rows}, opportunity_slots=1)
        self.assertEqual(result["context_instruments"], ["BTC-USDT-SWAP", "ETH-USDT-SWAP"])
        self.assertEqual(result["opportunity_candidates"][0]["instrument"], "FAST-USDT-SWAP")
        self.assertEqual(result["deep_stream_instrument_count"], 3)
        self.assertFalse(result["storage_contract"]["persist_raw_order_book"])
        self.assertFalse(result["trade_signal_generated"])

    def test_does_not_depend_on_user_example_list(self):
        rows = [{"instrument": "UNSEEN-USDT-SWAP", "change_24h_pct": 3,
                 "quote_volume_24h_estimate_usd": 20, "spread_bps": 2}]
        result = build_attention_route({"selected": rows}, opportunity_slots=1)
        self.assertEqual(result["deep_stream_instruments"], ["UNSEEN-USDT-SWAP"])
        self.assertFalse(result["routing_contract"]["uses_user_example_coin_list"])


if __name__ == "__main__":
    unittest.main()
