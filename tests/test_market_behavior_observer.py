import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import collect_closed_behavior_minute as observer


class MarketBehaviorObserverTests(unittest.TestCase):
    def test_refresh_builds_market_wide_route(self):
        exchange_info = {"symbols": [{
            "symbol": "AAAUSDT", "contractType": "PERPETUAL",
            "status": "TRADING", "quoteAsset": "USDT", "marginAsset": "USDT",
            "baseAsset": "AAA", "onboardDate": 1,
        }]}
        tickers = [{
            "symbol": "AAAUSDT", "lastPrice": "10", "openPrice": "9",
            "quoteVolume": "10000000",
        }]
        books = [{
            "symbol": "AAAUSDT", "bidPrice": "10", "askPrice": "10.01",
        }]
        with patch.object(
            observer, "fetch_json",
            side_effect=[exchange_info, tickers, books],
        ), patch.object(observer.time, "time", return_value=20_000_000):
            universe, route = observer.refresh_market_route()
        self.assertEqual(universe["selected_count"], 1)
        self.assertEqual(
            universe["selection_contract"]["exchange"], "BINANCE"
        )
        self.assertEqual(route["deep_stream_instruments"], ["AAA-USDT-SWAP"])


    def test_atomic_health_write_is_private_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            observer.write_json_atomic(path, {"status": "HEALTHY"})
            self.assertEqual(json.loads(path.read_text()), {"status": "HEALTHY"})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(tmp).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
