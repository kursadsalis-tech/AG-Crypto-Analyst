"""Market breadth must survive capacity changes, rotation and deep failures."""
import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from data.adaptive_market_scanner import build_attention_route
from database import broad_market_store as store
from database.runtime_store import REMOTE_RUNTIME_TABLES
from runtime import technical_kline_worker as worker, paper_realtime_worker as realtime
from risk_manager import risk_manager
from scripts import collect_closed_behavior_minute as observer


def tickers(count=102):
    return [{"s": symbol, "c": "10", "o": str(9 + index / 1000),
             "q": str(10_000_000 + index), "b": "10", "a": "10.001"}
            for index, symbol in enumerate(["BTCUSDT", "ETHUSDT"] +
                [f"COIN{index:03}USDT" for index in range(count - 2)])]


class BroadMarketRadarTests(unittest.TestCase):
    def test_breadth_and_technical_subscriptions_ignore_deep_slot_limit(self):
        universe = worker.build_universe(tickers(), snapshot_ms=60_000)
        expected = set(worker.selected_symbols(universe))
        for slots in (0, 1, 4, 10):
            route = build_attention_route(universe, slots)
            self.assertEqual(route["broad_scan_instrument_count"], 102)
            self.assertEqual(len(route["broad_scan_instruments"]), 102)
            self.assertEqual(route["deep_stream_instrument_count"], slots + 2)
            self.assertEqual(route["outside_deep_analysis_count"], 100 - slots)
            self.assertEqual(set(worker.selected_symbols(universe)), expected)
            self.assertFalse(route["trade_signal_generated"])
            self.assertFalse(route["routing_contract"]["deep_shortlist_is_trade_universe"])

    def test_rotation_reaches_every_instrument_under_unchanged_scores(self):
        universe = worker.build_universe(tickers(), snapshot_ms=60_000)
        seen = set()
        for epoch in range(50):
            route = build_attention_route(universe, 10, rotation_epoch=epoch)
            self.assertEqual(route["deep_stream_instrument_count"], 12)
            seen.update(route["deep_stream_instruments"])
        self.assertEqual(seen, {row["instrument"] for row in universe["selected"]})

    def test_producer_publishes_without_observer_and_consumer_does_no_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "runtime.db")
            store.bootstrap_schema(db)
            state = worker.UniverseState(now_ms=60_000)
            stop = asyncio.Event()
            with patch.object(worker, "DB_PATH", db), patch.object(
                worker, "fetch_market_tickers", new=AsyncMock(return_value=tickers())
            ), patch.object(worker.time, "time", return_value=60), patch.object(
                worker, "write_health", side_effect=lambda *_args, **_kw: stop.set()
            ):
                asyncio.run(worker.refresh_universe(state, stop))
            self.assertEqual(len(state.symbols), 102)
            with patch.object(observer.time, "time", return_value=61), patch.object(
                observer, "fetch_market_tickers", side_effect=AssertionError("duplicate network fetch")
            ):
                universe, route = observer.refresh_market_route(database=db, opportunity_slots=1)
            self.assertEqual(universe["selected_count"], 102)
            self.assertEqual(route["deep_stream_instrument_count"], 3)
            # A failed deep collection has no write authority over the radar.
            with patch.object(observer.time, "time", return_value=61), patch.object(
                observer, "collect_minute", new=AsyncMock(side_effect=ConnectionError("deep failed"))
            ):
                with self.assertRaises(ConnectionError):
                    observer.run_cycle(SimpleNamespace(
                        refresh_live_universe=True, database=db, opportunity_slots=1))
            self.assertEqual(store.load_universe(db, now_ms=62_000)["selected_count"], 102)
            next_universe = worker.build_universe(tickers(103), snapshot_ms=120_000)
            store.persist_universe(db, next_universe)
            store.persist_universe(db, universe)  # late old writer cannot rewind
            self.assertEqual(store.load_universe(db, now_ms=121_000)["selected_count"], 103)
            with sqlite3.connect(db) as con:
                self.assertEqual(con.execute(f"SELECT COUNT(*) FROM {store.TABLE}").fetchone()[0], 1)
                tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, {store.TABLE})
            self.assertNotIn(store.TABLE, REMOTE_RUNTIME_TABLES)

    def test_missing_stale_future_and_noncanonical_radar_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "runtime.db")
            store.bootstrap_schema(db)
            with self.assertRaises(ValueError):
                store.load_universe(db, now_ms=60_000)
            universe = worker.build_universe(tickers(), snapshot_ms=60_000)
            store.persist_universe(db, universe)
            for now_ms in (59_999, 240_001):
                with self.assertRaises(ValueError):
                    store.load_universe(db, now_ms=now_ms)
            universe["selection_contract"]["exchange"] = "OTHER"
            with self.assertRaises(ValueError):
                store.persist_universe(db, universe)

    def test_bar_health_cannot_hide_stale_radar(self):
        with patch.dict(worker._LAST_PERSISTENCE, {
            "broad_radar_snapshot_timestamp_ms": 60_000,
            "broad_radar_instrument_count": 102,
        }, clear=True), patch.object(worker.time, "time", return_value=241), patch.object(
            worker, "_atomic_json_write"
        ) as write:
            worker.write_health("CONNECTED", last_persist_duration_ms=2)
        health = write.call_args.args[1]
        self.assertEqual(health["status"], "DEGRADED")
        self.assertFalse(health["broad_radar_fresh"])
        self.assertEqual(health["error"], "BROAD_RADAR_STALE_OR_MISSING")
        self.assertFalse(health["may_send_real_orders"])

    def test_realtime_and_risk_keep_previous_fresh_slice_and_active_routes(self):
        con = sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.executescript("""
            CREATE TABLE paper_agent_positions(symbol TEXT,market_type TEXT,status TEXT);
            INSERT INTO paper_agent_positions VALUES('HELDUSDT','SPOT','OPEN');
            INSERT INTO paper_agent_positions VALUES('PENDINGUSDT','SPOT','PENDING_ENTRY');
            CREATE TABLE market_behavior_minute_features(
                instrument TEXT,market_data_provider TEXT,
                minute_end_timestamp_ms INTEGER,eligible_for_learning INTEGER);
        """)
        con.executemany("INSERT INTO market_behavior_minute_features VALUES(?,?,?,?)", [
            ("OLD-USDT-SWAP", "BINANCE", 900_000, 1),
            ("NEW-USDT-SWAP", "BINANCE", 960_000, 1),
            ("STALE-USDT-SWAP", "BINANCE", 100_000, 1),
            ("FUTURE-USDT-SWAP", "BINANCE", 1_001_000, 1),
            ("OTHER-USDT-SWAP", "OTHER", 960_000, 1),
            ("BAD-USDT-SWAP", "BINANCE", 960_000, 0),
        ])
        now = datetime.fromtimestamp(1000, timezone.utc)
        self.assertEqual(set(realtime.subscription_symbols(con, now)), {
            ("HELDUSDT", "SPOT"), ("PENDINGUSDT", "SPOT"),
            ("OLDUSDT", "USD_M_PERPETUAL"), ("NEWUSDT", "USD_M_PERPETUAL"),
        })
        self.assertEqual(risk_manager.behavior_risk_symbols(con, now), ("NEWUSDT", "OLDUSDT"))

    def test_radar_and_collector_cannot_create_positions_or_mutate_policy(self):
        root = Path(__file__).resolve().parents[1]
        for relative in ("database/broad_market_store.py", "runtime/technical_kline_worker.py",
                         "data/adaptive_market_scanner.py", "scripts/collect_closed_behavior_minute.py"):
            text = (root / relative).read_text()
            for forbidden in ("INSERT INTO paper_agent_positions", "UPDATE paper_agent_positions",
                              "select_new_positions(", "execute_paper(", "policy_mutation_allowed=True"):
                self.assertNotIn(forbidden, text)
