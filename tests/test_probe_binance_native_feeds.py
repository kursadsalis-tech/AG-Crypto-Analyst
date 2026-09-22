"""No-network tests for read-only Binance WebSocket acceptance telemetry."""
import unittest

from scripts.probe_binance_native_feeds import inspect_message, PROBES


class ProbeBinanceNativeFeedsTests(unittest.TestCase):
    def test_official_ws_routes_and_no_rest(self):
        self.assertEqual(len(PROBES), 6)
        self.assertTrue(all(s.startswith("wss://fstream.binance.com/")
                            for _, s, _ in PROBES))
        self.assertTrue(all("/public/ws/" in url if name in ("all_book", "depth20")
                            else "/market/ws/" in url for name, url, _ in PROBES))

    def test_exact_symbol_fresh_usdm_and_no_raw_data_output(self):
        seen = inspect_message([
            {"e": "24hrMiniTicker", "E": 1000, "s": "1000PEPEUSDT", "st": 1,
             "c": "1", "q": "123"},
            {"e": "24hrMiniTicker", "E": 1000, "s": "BTCUSD", "st": 2},
            {"e": "24hrMiniTicker", "E": 1000, "s": "ETHUSDT", "st": 2},
            {"e": "24hrMiniTicker", "E": 1000, "s": "SOLUSDT", "st": 1},
        ], "24hrMiniTicker", 1200)
        self.assertEqual(seen["valid_rows"], 2)
        self.assertEqual(seen["min_event_age_ms"], 200)
        self.assertEqual(seen["sample_symbols"], ["1000PEPEUSDT", "SOLUSDT"])
        self.assertNotIn("c", seen)
        self.assertNotIn("q", seen)

    def test_stale_future_wrong_type_and_unexpected_event_are_ignored(self):
        for payload in (
            {"e": "bookTicker", "s": "BTCUSDT", "E": -5000, "st": 1},
            {"e": "bookTicker", "s": "BTCUSDT", "E": 10000, "st": 1},
            {"e": "bookTicker", "s": "BTCUSDT", "E": 1000, "st": 2},
            {"e": "aggTrade", "s": "BTCUSDT", "E": 1000, "st": 1},
            {"e": "bookTicker", "s": "BTCUSD", "E": 1000, "st": 1},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(inspect_message(payload, "bookTicker", 1200)["valid_rows"], 0)

    def test_liquidation_uses_nested_native_symbol(self):
        value = inspect_message({"e": "forceOrder", "E": 1000, "st": 1,
                                 "o": {"s": "1000PEPEUSDT", "l": "100"}},
                                "forceOrder", 1200)
        self.assertEqual(value["sample_symbols"], ["1000PEPEUSDT"])
        self.assertEqual(value["valid_rows"], 1)


if __name__ == "__main__":
    unittest.main()
