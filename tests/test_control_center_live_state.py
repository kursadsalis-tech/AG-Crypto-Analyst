import asyncio
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runtime import paper_realtime_worker


class LiveControlStateTests(unittest.TestCase):
    def test_dynamic_attention_route_not_legacy_risk_basket_drives_monitoring(self):
        con = sqlite3.connect(":memory:")
        try:
            con.execute(
                "CREATE TABLE asset_risk_snapshots("
                "policy_snapshot_id INTEGER, symbol TEXT)"
            )
            con.executemany(
                "INSERT INTO asset_risk_snapshots VALUES(?,?)",
                [(7, "ARKMUSDT"), (7, "BIOUSDT"), (7, "ILVUSDT")],
            )
            metadata = SimpleNamespace(
                symbols={
                    "BTCUSDT": object(), "ETHUSDT": object(),
                    "DOGEUSDT": object(), "TAOUSDT": object(),
                    "ARKMUSDT": object(), "BIOUSDT": object(),
                    "ILVUSDT": object(),
                }
            )
            with tempfile.TemporaryDirectory() as tmp:
                report = Path(tmp) / "live.json"
                report.write_text(json.dumps({
                    "source_exchange": "BINANCE_USD_M",
                    "universe_coverage": {"deep_stream_instrument_count": 4},
                    "rows": [
                        {"instrument": "BTCUSDT"},
                        {"instrument": "ETHUSDT"},
                        {"instrument": "DOGEUSDT"},
                        {"instrument": "TAOUSDT"},
                    ],
                }))
                self.assertEqual(
                    paper_realtime_worker.dynamic_monitoring_symbols(
                        con, metadata, report
                    ),
                    (
                        ("BTCUSDT", "USD_M_PERPETUAL"),
                        ("ETHUSDT", "USD_M_PERPETUAL"),
                        ("DOGEUSDT", "USD_M_PERPETUAL"),
                        ("TAOUSDT", "USD_M_PERPETUAL"),
                    ),
                )
        finally:
            con.close()

    def test_dynamic_monitoring_falls_back_only_to_latest_behavior_minute(self):
        con = sqlite3.connect(":memory:")
        try:
            con.execute(
                "CREATE TABLE market_behavior_minute_features("
                "instrument TEXT, minute_end_timestamp_ms INTEGER, "
                "eligible_for_learning INTEGER)"
            )
            con.executemany(
                "INSERT INTO market_behavior_minute_features VALUES(?,?,?)",
                [
                    ("ARKMUSDT", 1000, 1),
                    ("BTCUSDT", 2000, 1),
                    ("DOGEUSDT", 2000, 1),
                ],
            )
            metadata = SimpleNamespace(
                symbols={
                    "ARKMUSDT": object(), "BTCUSDT": object(),
                    "DOGEUSDT": object(),
                }
            )
            self.assertEqual(
                paper_realtime_worker.dynamic_monitoring_symbols(
                    con, metadata, Path("/definitely/missing.json")
                ),
                (
                    ("BTCUSDT", "USD_M_PERPETUAL"),
                    ("DOGEUSDT", "USD_M_PERPETUAL"),
                ),
            )
        finally:
            con.close()

    def test_websocket_ingest_does_not_wait_for_slow_decision_database_work(self):
        started = threading.Event()
        callback_latency = []
        def schema(db):
            db.execute("CREATE TABLE IF NOT EXISTS paper_agent_positions("
                       "symbol TEXT,market_type TEXT,status TEXT)")
            db.commit()
        def slow_management(*_):
            started.set()
            time.sleep(.2)
            return {"status": "HOLD"}
        async def feed(_symbols, on_state, stop, _health, **_kwargs):
            from paper_trading.realtime_market import LiveState
            stamp = int(time.time() * 1000)
            state = LiveState("BTCUSDT", market_timestamp_ms=stamp,
                              quote_timestamp_ms=stamp, last_price=100,
                              best_bid=99.9, best_ask=100.1)
            await on_state(state, stamp)
            assert await asyncio.to_thread(started.wait, 1)
            before = time.monotonic()
            await on_state(state, stamp)
            callback_latency.append(time.monotonic() - before)
            stop.set()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(paper_realtime_worker, "DB_NAME", str(root / "test.db")), \
                    patch.object(paper_realtime_worker, "HEALTH_PATH", root / "health.json"), \
                    patch.object(paper_realtime_worker, "BRAIN_STATE_PATH", root / "brain.json"), \
                    patch.object(paper_realtime_worker, "BRAIN_TIMELINE_PATH", root / "timeline.json"), \
                    patch.object(paper_realtime_worker, "MARKET_STATE_PATH", root / "market.json"), \
                    patch.object(paper_realtime_worker, "validate_agent_authority"), \
                    patch.object(paper_realtime_worker, "load_cache", return_value=None), \
                    patch.object(paper_realtime_worker, "ensure_tables", side_effect=schema), \
                    patch.object(paper_realtime_worker.position_management, "ensure_tables"), \
                    patch.object(paper_realtime_worker.position_management,
                                 "initialize_open_positions"), \
                    patch.object(paper_realtime_worker, "cancel_expired_pending_entries",
                                 return_value=0), \
                    patch.object(paper_realtime_worker, "fill_pending_from_live_state",
                                 return_value=(0, 0)), \
                    patch.object(paper_realtime_worker, "rules_for", return_value=object()), \
                    patch.object(paper_realtime_worker.position_management,
                                 "process_market_state", side_effect=slow_management), \
                    patch.object(paper_realtime_worker.position_management,
                                 "reconcile_pending_funding", return_value={"resolved": 0}), \
                    patch.object(paper_realtime_worker.realtime_market, "stream", side_effect=feed):
                asyncio.run(paper_realtime_worker.run())
        self.assertEqual(len(callback_latency), 1)
        self.assertLess(callback_latency[0], .15)

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

    def test_brain_timeline_keeps_live_latency_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "brain.json"
            timeline_path = Path(tmp) / "timeline.json"
            with patch.object(paper_realtime_worker, "BRAIN_STATE_PATH", state_path), \
                    patch.object(paper_realtime_worker, "BRAIN_TIMELINE_PATH", timeline_path):
                paper_realtime_worker.write_brain_state({
                    "phase": "OPPORTUNITY_SCAN", "observed_symbol": "BTCUSDT",
                    "observed_event_ms": 10000, "context_window_start_ms": 5000,
                    "decision_time_ms": 10120, "queue_delay_ms": 40,
                    "evaluation_ms": 80, "decision": "CONTINUE_SCANNING",
                    "rejection_reasons": {"RISK_VETO": 1},
                })
            row = json.loads(timeline_path.read_text())[0]
            self.assertEqual(row["observed_event_ms"], 10000)
            self.assertEqual(row["context_window_start_ms"], 5000)
            self.assertEqual(row["decision_time_ms"], 10120)
            self.assertEqual(row["queue_delay_ms"], 40)
            self.assertEqual(row["evaluation_ms"], 80)
            self.assertEqual(row["rejection_reasons"], {"RISK_VETO": 1})

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
