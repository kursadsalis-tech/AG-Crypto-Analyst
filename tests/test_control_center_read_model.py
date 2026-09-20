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
                    2300,2,4600,1,'test rationale','OPEN',NULL,NULL,NULL,NULL,0,0
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
            result = overview(db, state, now=now)
            position = result["positions"][0]
            self.assertEqual(position["mark_price"], 2347.1)
            self.assertAlmostEqual(position["unrealized_pnl"], 94.2)
            self.assertEqual(position["market_status"], "CANLI")
            self.assertEqual(result["system"]["market_data_status"], "CANLI")
            self.assertFalse(result["system"]["real_order_allowed"])
            self.assertEqual(position["current_stop_price"], 2254)
            self.assertEqual(position["tp1_price"], 2346)
            self.assertEqual(position["tp2_price"], 2392)
            self.assertFalse(position["tp1_done"])

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
                    60000,0.1,6000,1,'test rationale','OPEN',NULL,NULL,NULL,NULL,0,0
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
            position = overview(db, state, now=now)["positions"][0]
            self.assertEqual(position["mark_price"], 59001)
            self.assertAlmostEqual(position["unrealized_pnl"], 99.9)
            self.assertEqual(position["market_status"], "GECİKMİŞ")


if __name__ == "__main__":
    unittest.main()
