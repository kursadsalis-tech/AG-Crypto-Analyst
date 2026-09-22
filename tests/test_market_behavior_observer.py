import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import collect_closed_behavior_minute as observer


class MarketBehaviorObserverTests(unittest.TestCase):
    def test_loads_prevalidated_binance_metadata_without_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metadata.json"
            info = {"symbols": [{
                "symbol": "1000PEPEUSDT", "status": "TRADING",
                "contractType": "PERPETUAL", "quoteAsset": "USDT",
                "marginAsset": "USDT", "baseAsset": "1000PEPE",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.0001"},
                    {"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            }]}
            import hashlib
            from datetime import datetime, timezone
            document = {
                "schema_version": 1, "market": "USD_M_PERPETUAL",
                "source_url": "https://fapi.binance.com/fapi/v1/exchangeInfo",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "payload_sha256": hashlib.sha256(
                    json.dumps(info, separators=(",", ":")).encode()
                ).hexdigest(),
                "payload": info,
            }
            path.write_text(json.dumps(document))
            with patch.dict(os.environ, {"AGCA_EXCHANGE_METADATA_CACHE": str(path)}):
                payload, fetched_at = observer.verified_exchange_info()
            self.assertEqual(payload["symbols"][0]["symbol"], "1000PEPEUSDT")
            self.assertEqual(fetched_at, document["fetched_at"])

    def test_rejects_concurrent_metadata_rotation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metadata.json"
            path.write_text(json.dumps({"payload_sha256": "untrusted"}))
            from types import SimpleNamespace
            fake = SimpleNamespace(
                payload_sha256="a"*64, fetched_at="2026-09-21T00:00:00+00:00",
            )
            with patch.dict(os.environ, {"AGCA_EXCHANGE_METADATA_CACHE": str(path)}), \
                 patch.object(observer, "load_cache", return_value=fake):
                with self.assertRaisesRegex(RuntimeError, "ROTATED"):
                    observer.verified_exchange_info()

    def test_atomic_health_write_is_private_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            observer.write_json_atomic(path, {"status": "HEALTHY"})
            self.assertEqual(json.loads(path.read_text()), {"status": "HEALTHY"})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(tmp).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
