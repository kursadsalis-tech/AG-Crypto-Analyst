import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runtime import paper_realtime_worker


class LiveControlStateTests(unittest.TestCase):
    def test_heartbeat_requires_recent_quote_for_connected_status(self):
        status = paper_realtime_worker.heartbeat_market_status
        active = [("BTCUSDT", "USD_M_PERPETUAL")]
        self.assertEqual(status(active, None, 10_000),
                         ("DEGRADED", "NO_LIVE_QUOTES_YET"))
        self.assertEqual(status(active, 7_999, 10_000),
                         ("DEGRADED", "LIVE_QUOTES_STALE"))
        self.assertEqual(status(active, 9_900, 10_000),
                         ("CONNECTED", None))
        self.assertEqual(status([], None, 10_000), ("IDLE", None))

    def test_partial_quote_gap_is_not_hidden_by_fresh_btc(self):
        active = [("BTCUSDT", "USD_M_PERPETUAL"),
                  ("ETHUSDT", "USD_M_PERPETUAL")]
        live = {
            active[0]: {"quote_timestamp_ms": 99_800},
            active[1]: {"quote_timestamp_ms": -28_000},
        }
        gaps = paper_realtime_worker.stale_quote_symbols(active, live, 100_000)
        self.assertEqual(gaps, [active[1]])
        live[active[1]] = {"quote_timestamp_ms": 99_900}
        self.assertEqual(
            paper_realtime_worker.stale_quote_symbols(active, live, 100_000), [],
        )
        self.assertEqual(
            paper_realtime_worker.stale_quote_symbols(
                active, {active[0]: live[active[0]]}, 100_000,
            ),
            [active[1]],
        )

    def test_market_state_payload_is_read_only_paper_telemetry(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "market-state.json"
            with patch.object(paper_realtime_worker, "MARKET_STATE_PATH", target):
                paper_realtime_worker.write_market_state(
                    [{"symbol": "BTCUSDT", "best_bid": 100.0}]
                )
            payload = json.loads(target.read_text())
            self.assertEqual(payload["execution_mode"], "PAPER_ONLY")
            self.assertFalse(payload["real_order_allowed"])
            self.assertEqual(payload["live_market"][0]["symbol"], "BTCUSDT")

    def test_market_state_write_failure_cannot_break_paper_runtime(self):
        with patch.object(
            paper_realtime_worker, "_atomic_json_write",
            side_effect=OSError("telemetry unavailable"),
        ):
            paper_realtime_worker.write_market_state(
                [{"symbol": "BTCUSDT", "best_bid": 100.0}]
            )

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
            liquidity_timestamp_ms=1_800_000_000_000,
            visible_quote_volume=125000.0,
            bid_quote_volume=70000.0,
            ask_quote_volume=55000.0,
            book_imbalance=0.12,
            taker_imbalance=-0.08,
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
                "liquidity_timestamp_ms": value.liquidity_timestamp_ms,
                "visible_quote_volume": value.visible_quote_volume,
                "bid_quote_volume": value.bid_quote_volume,
                "ask_quote_volume": value.ask_quote_volume,
                "book_imbalance": value.book_imbalance,
                "taker_imbalance": value.taker_imbalance,
                "observed_timestamp_ms": observed_ms,
            }
        result = payload(state, 1_800_000_000_100)
        self.assertEqual(result["best_bid"], state.best_bid)
        self.assertEqual(result["best_ask"], state.best_ask)
        self.assertEqual(result["quote_source"], state.quote_source)
        self.assertEqual(result["liquidity_timestamp_ms"], state.liquidity_timestamp_ms)
        self.assertEqual(result["visible_quote_volume"], state.visible_quote_volume)
        self.assertEqual(result["book_imbalance"], state.book_imbalance)
        self.assertEqual(result["taker_imbalance"], state.taker_imbalance)
        self.assertEqual(result["observed_timestamp_ms"], 1_800_000_000_100)


if __name__ == "__main__":
    unittest.main()
