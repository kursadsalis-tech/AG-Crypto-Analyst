import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from control_center.read_model import overview


class ControlCenterReadModelTests(unittest.TestCase):
    def test_overview_uses_canonical_live_quote_and_read_only_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "runtime.db"
            state = Path(tmp) / "market-state.json"
            con = sqlite3.connect(db)
            con.executescript("""
                CREATE TABLE paper_agent_portfolio(
                    portfolio_id INTEGER PRIMARY KEY, initial_capital REAL,
                    cash REAL, realized_pnl REAL, total_fees REAL
                );
                CREATE TABLE paper_agent_positions(
                    position_id INTEGER PRIMARY KEY, symbol TEXT, market_type TEXT,
                    direction TEXT, entry_time TEXT, entry_price REAL, quantity REAL,
                    notional REAL, leverage REAL, selection_reason TEXT, status TEXT,
                    decision_source TEXT DEFAULT 'PAPER_OPPORTUNITY_POLICY',
                    decision_reference_id INTEGER,
                    exit_time TEXT, exit_price REAL, net_pnl REAL, close_reason TEXT,
                    entry_fee REAL DEFAULT 0, exit_fee REAL DEFAULT 0
                );
                CREATE TABLE paper_position_management(
                    position_id INTEGER PRIMARY KEY, stop_distance_pct REAL,
                    current_stop_price REAL, tp1_done INTEGER, tp2_done INTEGER,
                    remaining_quantity REAL
                );
                INSERT INTO paper_agent_portfolio VALUES(1,10000,9000,25,5);
                INSERT INTO paper_agent_positions VALUES(
                    7,'ETHUSDT','USD_M_PERPETUAL','LONG','2026-09-21T10:00:00+00:00',
                    2300,2,4600,1,'test rationale','OPEN','PAPER_OPPORTUNITY_POLICY',101,NULL,NULL,NULL,NULL,0,0
                );
                INSERT INTO paper_position_management VALUES(7,2,2254,0,0,2);
            """)
            con.commit()
            con.close()
            now = datetime(2026, 9, 21, 15, 0, 1, tzinfo=timezone.utc)
            stamp = int(datetime(2026, 9, 21, 15, 0, 0, tzinfo=timezone.utc).timestamp()*1000)
            state.write_text(json.dumps({
                "execution_mode": "PAPER_ONLY",
                "real_order_allowed": False,
                "live_market": [{
                    "symbol": "ETHUSDT", "market_type": "USD_M_PERPETUAL",
                    "last_price": 2347.2, "best_bid": 2347.1, "best_ask": 2347.2,
                    "quote_source": "BINANCE_USD_M_WS_TICKER_BID_ASK",
                    "quote_timestamp_ms": stamp,
                }],
            }))
            health = Path(tmp) / "health.json"
            health.write_text(json.dumps({
                "execution_mode": "PAPER_ONLY", "real_order_allowed": False,
                "status": "CONNECTED", "at": now.isoformat(),
                "action": "HOLD", "active_symbols": ["ETHUSDT"],
            }))
            result = overview(db, state, health, now=now)
            position = result["positions"][0]
            self.assertEqual(position["mark_price"], 2347.1)
            self.assertAlmostEqual(position["unrealized_pnl"], 94.2)
            self.assertEqual(position["market_status"], "CANLI")
            self.assertEqual(result["system"]["market_data_status"], "CANLI")
            self.assertEqual(len(result["market"]), 1)
            market = result["market"][0]
            self.assertEqual(market["symbol"], "ETHUSDT")
            self.assertEqual(market["market_type"], "USD_M_PERPETUAL")
            self.assertEqual(market["last_price"], 2347.2)
            self.assertEqual(market["best_bid"], 2347.1)
            self.assertEqual(market["best_ask"], 2347.2)
            self.assertEqual(market["market_status"], "CANLI")
            self.assertEqual(
                market["quote_source"], "BINANCE_USD_M_WS_TICKER_BID_ASK"
            )
            self.assertFalse(result["system"]["real_order_allowed"])
            self.assertTrue(result["system"]["worker_healthy"])
            self.assertEqual(result["system"]["worker_status"], "CONNECTED")
            self.assertEqual(result["system"]["last_action"], "HOLD")
            self.assertEqual(position["current_stop_price"], 2254)
            self.assertEqual(position["tp1_price"], 2346)
            self.assertEqual(position["tp2_price"], 2392)
            self.assertFalse(position["tp1_done"])
            flow = position["decision_flow"]
            self.assertEqual(flow["observation"]["status"], "CANLI")
            self.assertEqual(flow["opportunity"]["reason"], "test rationale")
            self.assertEqual(flow["decision"]["direction"], "LONG")
            self.assertEqual(flow["decision"]["reference_id"], 101)
            self.assertEqual(flow["execution"]["status"], "AÇIK")

    def test_short_marks_against_ask_and_stale_data_is_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "runtime.db"
            state = Path(tmp) / "market-state.json"
            con = sqlite3.connect(db)
            con.executescript("""
                CREATE TABLE paper_agent_portfolio(
                    portfolio_id INTEGER PRIMARY KEY, initial_capital REAL,
                    cash REAL, realized_pnl REAL, total_fees REAL
                );
                CREATE TABLE paper_agent_positions(
                    position_id INTEGER PRIMARY KEY, symbol TEXT, market_type TEXT,
                    direction TEXT, entry_time TEXT, entry_price REAL, quantity REAL,
                    notional REAL, leverage REAL, selection_reason TEXT, status TEXT,
                    decision_source TEXT DEFAULT 'PAPER_OPPORTUNITY_POLICY',
                    decision_reference_id INTEGER,
                    exit_time TEXT, exit_price REAL, net_pnl REAL, close_reason TEXT,
                    entry_fee REAL DEFAULT 0, exit_fee REAL DEFAULT 0
                );
                CREATE TABLE paper_position_management(
                    position_id INTEGER PRIMARY KEY, stop_distance_pct REAL,
                    current_stop_price REAL, tp1_done INTEGER, tp2_done INTEGER,
                    remaining_quantity REAL
                );
                INSERT INTO paper_agent_portfolio VALUES(1,10000,9000,0,0);
                INSERT INTO paper_agent_positions VALUES(
                    8,'BTCUSDT','USD_M_PERPETUAL','SHORT','2026-09-21T10:00:00+00:00',
                    60000,0.1,6000,1,'test rationale','OPEN','PAPER_OPPORTUNITY_POLICY',102,NULL,NULL,NULL,NULL,0,0
                );
                INSERT INTO paper_position_management VALUES(8,1,60600,1,0,0.075);
            """)
            con.commit()
            con.close()
            old = int(datetime(2026, 9, 21, 14, 59, 0, tzinfo=timezone.utc).timestamp()*1000)
            state.write_text(json.dumps({
                "execution_mode": "PAPER_ONLY", "real_order_allowed": False,
                "live_market": [{
                    "symbol": "BTCUSDT", "market_type": "USD_M_PERPETUAL",
                    "last_price": 59000, "best_bid": 58999, "best_ask": 59001,
                    "quote_timestamp_ms": old,
                }],
            }))
            now = datetime(2026, 9, 21, 15, 0, 0, tzinfo=timezone.utc)
            health = Path(tmp) / "health.json"
            health.write_text(json.dumps({
                "execution_mode": "PAPER_ONLY", "real_order_allowed": False,
                "status": "CONNECTED", "at": now.isoformat(),
                "action": "HOLD", "active_symbols": ["BTCUSDT"],
            }))
            position = overview(db, state, health, now=now)["positions"][0]
            self.assertEqual(position["mark_price"], 59001)
            self.assertAlmostEqual(position["unrealized_pnl"], 99.9)
            self.assertEqual(position["market_status"], "GECİKMİŞ")

    def test_malformed_health_and_learning_evidence_fail_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "runtime.db"
            state = Path(tmp) / "market.json"
            health = Path(tmp) / "health.json"
            con = sqlite3.connect(db)
            con.executescript("""
                CREATE TABLE paper_agent_portfolio(
                    portfolio_id INTEGER PRIMARY KEY, initial_capital REAL,
                    cash REAL, realized_pnl REAL, total_fees REAL
                );
                CREATE TABLE paper_agent_positions(
                    position_id INTEGER PRIMARY KEY, symbol TEXT, market_type TEXT,
                    direction TEXT, entry_time TEXT, entry_price REAL, quantity REAL,
                    notional REAL, leverage REAL, selection_reason TEXT, status TEXT,
                    decision_source TEXT, decision_reference_id INTEGER,
                    exit_time TEXT, exit_price REAL, net_pnl REAL, close_reason TEXT,
                    entry_fee REAL DEFAULT 0, exit_fee REAL DEFAULT 0
                );
                CREATE TABLE paper_position_management(
                    position_id INTEGER PRIMARY KEY, stop_distance_pct REAL,
                    current_stop_price REAL, tp1_done INTEGER, tp2_done INTEGER,
                    remaining_quantity REAL
                );
                INSERT INTO paper_agent_portfolio VALUES(1,10000,10000,0,0);
            """)
            con.commit()
            con.close()
            state.write_text(json.dumps({
                "execution_mode": "PAPER_ONLY", "real_order_allowed": False,
                "live_market": [],
            }))
            health.write_text(json.dumps({
                "execution_mode": "PAPER_ONLY", "real_order_allowed": False,
                "status": "CONNECTED", "at": "not-a-timestamp",
            }))
            con = sqlite3.connect(db)
            con.execute("""CREATE TABLE paper_agent_trade_learning_reviews (
                review_id INTEGER PRIMARY KEY, created_at TEXT, learning_version TEXT,
                validation_cohort TEXT, closed_count INTEGER, independent_weeks INTEGER,
                status TEXT, promotion_allowed INTEGER, evidence_json TEXT)""")
            con.execute("""INSERT INTO paper_agent_trade_learning_reviews VALUES
                (1,'2026-09-21T00:00:00+00:00','V1','TEST',1,1,
                 'COLLECTING_EVIDENCE',0,'{broken')""")
            con.commit()
            con.close()
            result = overview(db, state, health, now=datetime(2026, 9, 21, tzinfo=timezone.utc))
            self.assertFalse(result["system"]["worker_healthy"])
            self.assertIsNone(result["system"]["heartbeat_age_seconds"])
            self.assertEqual(result["learning"]["evidence"], {})


    def test_detects_silent_work_stall_with_live_worker_and_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "runtime.db"
            state = Path(tmp) / "market.json"
            health = Path(tmp) / "health.json"
            now = datetime(2026, 9, 21, 15, 0, 0, tzinfo=timezone.utc)
            con = sqlite3.connect(db)
            con.executescript("""
                CREATE TABLE paper_agent_portfolio(
                    portfolio_id INTEGER PRIMARY KEY, initial_capital REAL,
                    cash REAL, realized_pnl REAL, total_fees REAL);
                CREATE TABLE paper_agent_positions(
                    position_id INTEGER PRIMARY KEY, symbol TEXT, market_type TEXT,
                    direction TEXT, entry_time TEXT, entry_price REAL, quantity REAL,
                    notional REAL, leverage REAL, selection_reason TEXT, status TEXT,
                    decision_source TEXT, decision_reference_id INTEGER,
                    selected_at TEXT, exit_time TEXT, exit_price REAL, net_pnl REAL,
                    close_reason TEXT, entry_fee REAL DEFAULT 0, exit_fee REAL DEFAULT 0);
                CREATE TABLE paper_position_management(
                    position_id INTEGER PRIMARY KEY, stop_distance_pct REAL,
                    current_stop_price REAL, tp1_done INTEGER, tp2_done INTEGER,
                    remaining_quantity REAL);
                CREATE TABLE paper_behavior_selection_runs(
                    run_id INTEGER PRIMARY KEY, created_at TEXT);
                INSERT INTO paper_agent_portfolio VALUES(1,10000,10000,0,0);
                INSERT INTO paper_behavior_selection_runs VALUES(
                    1,'2026-09-21T14:50:00+00:00');
            """)
            con.commit(); con.close()
            stamp = int(now.timestamp() * 1000)
            state.write_text(json.dumps({
                "execution_mode":"PAPER_ONLY","real_order_allowed":False,
                "live_market":[{"symbol":"BTCUSDT","market_type":"USD_M_PERPETUAL",
                "last_price":60000,"best_bid":59999,"best_ask":60001,
                "quote_timestamp_ms":stamp}]
            }))
            health.write_text(json.dumps({
                "execution_mode":"PAPER_ONLY","real_order_allowed":False,
                "status":"CONNECTED","at":now.isoformat(),"action":"SELECT"
            }))
            result = overview(db, state, health, now=now)
            self.assertTrue(result["system"]["worker_healthy"])
            self.assertEqual(result["system"]["pipeline_state"], "İŞ ÜRETMİYOR")
            self.assertAlmostEqual(result["system"]["latest_work_age_seconds"], 600.0)
            self.assertEqual(result["incidents"][0]["kind"], "PIPELINE_VISIBILITY")
            self.assertEqual(result["incidents"][0]["severity"], "KRİTİK")

            health.write_text(json.dumps({
                "execution_mode":"PAPER_ONLY","real_order_allowed":False,
                "status":"CONNECTED","at":now.isoformat(),"action":"HOLD"
            }))
            healthy_hold = overview(db, state, health, now=now)
            self.assertEqual(healthy_hold["system"]["pipeline_state"], "İŞ ÜRETMİYOR")
            self.assertTrue(healthy_hold["system"]["healthy_hold"])
            self.assertFalse(healthy_hold["system"]["brain_scan_fresh"])


if __name__ == "__main__":
    unittest.main()
