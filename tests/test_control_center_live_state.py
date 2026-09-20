import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runtime import paper_realtime_worker


class LiveControlStateTests(unittest.TestCase):
    def test_health_payload_is_paper_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "health.json"
            with patch.object(paper_realtime_worker, "HEALTH_PATH", target):
                paper_realtime_worker.write_health(
                    "CONNECTED",
                    live_market=[{"symbol": "BTCUSDT", "best_bid": 100.0}],
                )
            payload = json.loads(target.read_text())
            self.assertEqual(payload["execution_mode"], "PAPER_ONLY")
            self.assertFalse(payload["real_order_allowed"])
            self.assertEqual(payload["live_market"][0]["symbol"], "BTCUSDT")

    def test_live_payload_preserves_worker_market_state(self):
        state = SimpleNamespace(
            symbol="ETHUSDT",
            market_type="USD_M_PERPETUAL",
            last_price=2347.2,
            best_bid=2347.1,
            best_ask=2347.2,
            quote_source="BINANCE_USD_M_WS_TICKER_BID_ASK",
            market_timestamp_ms=1_800_000_000_000,
            quote_timestamp_ms=1_800_000_000_000,
        )
        # Contract fields are intentionally direct copies of the canonical
        # normalized state; the Control Center must not fetch or invent prices.
        def payload(value, observed_ms):
            return {
                "symbol": value.symbol,
                "market_type": value.market_type,
                "last_price": value.last_price,
                "best_bid": value.best_bid,
                "best_ask": value.best_ask,
                "quote_source": value.quote_source,
                "market_timestamp_ms": value.market_timestamp_ms,
                "quote_timestamp_ms": value.quote_timestamp_ms,
                "observed_timestamp_ms": observed_ms,
            }
        result = payload(state, 1_800_000_000_100)
        self.assertEqual(result["best_bid"], state.best_bid)
        self.assertEqual(result["best_ask"], state.best_ask)
        self.assertEqual(result["quote_source"], state.quote_source)
        self.assertEqual(result["observed_timestamp_ms"], 1_800_000_000_100)


if __name__ == "__main__":
    unittest.main()
