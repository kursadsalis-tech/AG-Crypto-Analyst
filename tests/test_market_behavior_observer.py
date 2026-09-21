import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import collect_closed_behavior_minute as observer


class MarketBehaviorObserverTests(unittest.TestCase):
    def test_refresh_builds_binance_native_market_wide_route(self):
        now_ms = 1_800_000_000_000
        info = {"symbols": [{
            "symbol": "1000PEPEUSDT", "contractType": "PERPETUAL",
            "status": "TRADING", "quoteAsset": "USDT",
            "marginAsset": "USDT", "baseAsset": "1000PEPE",
            "onboardDate": now_ms - 100 * 86_400_000,
        }]}
        tickers = [{
            "symbol": "1000PEPEUSDT", "lastPrice": "1.0",
            "quoteVolume": "10000000", "priceChangePercent": "5",
            "closeTime": now_ms - 1000,
        }]
        books = [{
            "symbol": "1000PEPEUSDT", "bidPrice": "1.0",
            "askPrice": "1.0001", "bidQty": "1000", "askQty": "1000",
            "time": now_ms - 100,
        }]
        with patch.object(observer, "fetch_market_overview",
                          side_effect=[info, tickers, books]) as fetch, \
             patch.object(observer.time, "time", return_value=now_ms / 1000):
            universe, route = observer.refresh_market_route()
        self.assertEqual(universe["selected_count"], 1)
        self.assertEqual(route["deep_stream_instruments"], ["1000PEPEUSDT"])
        self.assertEqual(universe["selected"][0]["exchange"], "BINANCE_USD_M")
        self.assertEqual([item.args[0] for item in fetch.call_args_list], [
            "exchangeInfo", "ticker/24hr", "ticker/bookTicker",
        ])

    def test_atomic_health_write_is_private_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            observer.write_json_atomic(path, {"status": "HEALTHY"})
            self.assertEqual(json.loads(path.read_text()), {"status": "HEALTHY"})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(tmp).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
