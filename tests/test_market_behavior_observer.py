import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import collect_closed_behavior_minute as observer


class MarketBehaviorObserverTests(unittest.TestCase):
    def test_refresh_builds_market_wide_route(self):
        instruments = {"code": "0", "data": [{
            "instId": "AAA-USDT-SWAP", "instType": "SWAP", "state": "live",
            "instCategory": "1", "settleCcy": "USDT", "ctVal": "1",
            "ctValCcy": "AAA", "listTime": "1",
        }]}
        tickers = {"code": "0", "data": [{
            "instId": "AAA-USDT-SWAP", "bidPx": "10", "askPx": "10.01",
            "last": "10", "open24h": "9", "vol24h": "1000000",
        }]}
        with patch.object(observer, "fetch_json", side_effect=[instruments, tickers]), \
             patch.object(observer.time, "time", return_value=20_000_000):
            universe, route = observer.refresh_market_route()
        self.assertEqual(universe["selected_count"], 1)
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
