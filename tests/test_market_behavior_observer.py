import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from scripts import collect_closed_behavior_minute as observer


class MarketBehaviorObserverTests(unittest.TestCase):
    def test_alignment_skips_partial_behavior_window(self):
        target, skipped = observer.collection_alignment(30_000, 38)
        self.assertEqual(target, 120_000)
        self.assertEqual(skipped, 60_000)

    def test_alignment_uses_next_minute_when_full_behavior_span_fits(self):
        target, skipped = observer.collection_alignment(20_000, 38)
        self.assertEqual(target, 60_000)
        self.assertIsNone(skipped)

    def test_refresh_builds_market_wide_route(self):
        tickers = [{
            "s": "AAAUSDT", "c": "10", "o": "9", "q": "10000000",
            "b": "10", "a": "10.01",
        }]
        with patch.object(
            observer, "fetch_market_tickers",
            new=AsyncMock(return_value=tickers),
        ), patch.object(observer.time, "time", return_value=20_000_000):
            universe, route = observer.refresh_market_route()
        self.assertEqual(universe["selected_count"], 1)
        self.assertEqual(
            universe["selection_contract"]["exchange"], "BINANCE"
        )
        self.assertEqual(
            universe["selection_contract"]["transport"],
            "USD_M_ROUTED_WEBSOCKET_TICKER_PLUS_BOOK",
        )
        self.assertEqual(route["deep_stream_instruments"], ["AAA-USDT-SWAP"])

    def test_behavior_collection_does_not_request_technical_kline_sink(self):
        args = type("Args", (), {"samples": 1, "cadence_seconds": 1})()
        with patch.object(
            observer, "probe", new=AsyncMock(return_value=[]),
        ), patch.object(
            observer, "collect_minute_snapshots",
            new=AsyncMock(return_value=[]),
        ) as collect:
            import asyncio
            asyncio.run(observer.collect_minute(
                {"deep_stream_instruments": []},
                {"selected": []},
                {},
                args,
                int(observer.time.time() * 1000) + 1_000,
            ))
        self.assertIsNone(collect.await_args.kwargs["closed_bar_sink"])
        self.assertIsNone(collect.await_args.kwargs["observe_until_ms"])

    def test_atomic_health_write_is_private_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            observer.write_json_atomic(path, {"status": "HEALTHY"})
            self.assertEqual(json.loads(path.read_text()), {"status": "HEALTHY"})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(tmp).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
