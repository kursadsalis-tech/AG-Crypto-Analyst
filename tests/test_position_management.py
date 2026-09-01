import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from paper_trading.execution_contract import ExecutionRules
from paper_trading.position_management import (
    ensure_tables, initialize_open_positions, process_market_state,
    reconcile_pending_funding,
)
from paper_trading.realtime_market import LiveState


ENTRY = datetime(2026, 9, 2, 10, tzinfo=timezone.utc)


class PositionManagementTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript("""
            CREATE TABLE paper_agent_portfolio(
                portfolio_id INTEGER PRIMARY KEY,cash REAL,realized_pnl REAL,total_fees REAL);
            INSERT INTO paper_agent_portfolio VALUES(1,8999,0,1);
            CREATE TABLE paper_agent_positions(
                position_id INTEGER PRIMARY KEY,symbol TEXT,direction TEXT,status TEXT,
                entry_time TEXT,entry_price REAL,quantity REAL,notional REAL,entry_fee REAL,
                due_time TEXT,exit_fee REAL DEFAULT 0,exit_spread_cost REAL DEFAULT 0,
                exit_slippage_cost REAL DEFAULT 0,funding_cost REAL DEFAULT 0,
                gross_pnl REAL,net_pnl REAL,exit_time TEXT,exit_price REAL,close_reason TEXT);
            CREATE TABLE asset_risk_snapshots(
                id INTEGER PRIMARY KEY,symbol TEXT,created_at TEXT,
                volatility_stop_reference_pct REAL);
        """)
        self.con.execute("INSERT INTO paper_agent_positions VALUES(1,?,?,?,?,?,?,?,?,?,0,0,0,0,NULL,NULL,NULL,NULL,NULL)", (
            "BTCUSDT", "LONG", "OPEN", ENTRY.isoformat(), 100.0, 10.0, 1000.0, 1.0,
            (ENTRY + timedelta(hours=4)).isoformat(),
        ))
        self.con.execute("INSERT INTO asset_risk_snapshots VALUES(1,?,?,?)",
                         ("BTCUSDT", (ENTRY - timedelta(seconds=1)).isoformat(), 2.0))
        ensure_tables(self.con)
        self.rules = ExecutionRules(quantity_step=.001, price_tick=.01, min_notional=5,
                                    require_liquidity=True)

    def live(self, price, seconds, book=.4, taker=.3):
        return LiveState(
            "BTCUSDT", last_price=price, best_bid=price-.01, best_ask=price+.01,
            book_imbalance=book, taker_imbalance=taker,
            visible_quote_volume=1_000_000,
            market_timestamp_ms=int((ENTRY + timedelta(seconds=seconds)).timestamp()*1000),
        )

    def test_initializes_stop_from_entry_risk_snapshot(self):
        result = initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        self.assertEqual(result, {"initialized": 1, "blocked": []})
        row = self.con.execute("SELECT current_stop_price,remaining_quantity FROM paper_position_management").fetchone()
        self.assertEqual(row, (98.0, 10.0))
        self.assertEqual(self.con.execute("SELECT real_order_allowed FROM paper_position_events").fetchone()[0], 0)

    def test_one_r_reduces_and_stop_can_close_remainder(self):
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        reduced = process_market_state(
            self.con, self.live(102, 2), ENTRY + timedelta(seconds=2), self.rules,
        )
        self.assertEqual((reduced["status"], reduced["reason"]),
                         ("POSITION_REDUCED", "TAKE_PROFIT_1R"))
        remaining, stop = self.con.execute(
            "SELECT remaining_quantity,current_stop_price FROM paper_position_management"
        ).fetchone()
        self.assertAlmostEqual(remaining, 7.5)
        self.assertGreater(stop, 100)
        closed = process_market_state(
            self.con, self.live(99, 3), ENTRY + timedelta(seconds=3), self.rules,
        )
        self.assertEqual((closed["status"], closed["reason"]),
                         ("POSITION_CLOSED", "PROTECTIVE_STOP"))
        self.assertEqual(self.con.execute("SELECT status FROM paper_agent_positions").fetchone()[0], "CLOSED")
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM paper_position_events WHERE event_type IN ('POSITION_REDUCED','POSITION_CLOSED')"
        ).fetchone()[0], 2)

    def test_same_market_event_cannot_apply_twice(self):
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        live = self.live(102, 2)
        process_market_state(self.con, live, ENTRY + timedelta(seconds=2), self.rules)
        before = self.con.execute("SELECT cash FROM paper_agent_portfolio").fetchone()[0]
        result = process_market_state(self.con, live, ENTRY + timedelta(seconds=3), self.rules)
        after = self.con.execute("SELECT cash FROM paper_agent_portfolio").fetchone()[0]
        self.assertEqual(result["status"], "DUPLICATE_OR_OLD_EVENT")
        self.assertEqual(before, after)

    def test_below_minimum_profit_slice_tightens_stop_without_fake_fill(self):
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        strict = ExecutionRules(quantity_step=.001, price_tick=.01, min_notional=500,
                                require_liquidity=True)
        result = process_market_state(
            self.con, self.live(102, 2), ENTRY + timedelta(seconds=2), strict,
        )
        self.assertEqual(result, {"status": "UPDATE_STOP",
                                 "reason": "TAKE_PROFIT_1R_BELOW_MIN_NOTIONAL"})
        self.assertEqual(self.con.execute(
            "SELECT remaining_quantity FROM paper_position_management"
        ).fetchone()[0], 10)

    def test_missing_risk_snapshot_is_visible_and_does_not_invent_stop(self):
        self.con.execute("DELETE FROM asset_risk_snapshots")
        result = initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        self.assertEqual(result["blocked"], [(1, "MISSING_ENTRY_RISK_SNAPSHOT")])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM paper_position_management").fetchone()[0], 0)

    def test_funding_outage_never_blocks_protective_stop(self):
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        result = process_market_state(
            self.con, self.live(98, 2), ENTRY + timedelta(seconds=2), self.rules,
        )
        self.assertEqual(result["status"], "POSITION_CLOSED")
        row = self.con.execute("""SELECT status,funding_source
            FROM paper_agent_positions""").fetchone()
        self.assertEqual(row, ("CLOSED", "PENDING_RECONCILIATION"))
        reconciled = reconcile_pending_funding(
            self.con, lambda **_: [], ENTRY + timedelta(minutes=1),
        )
        self.assertEqual(reconciled, {"resolved": 1, "blocked": 0})
        self.assertEqual(self.con.execute(
            "SELECT funding_source FROM paper_agent_positions"
        ).fetchone()[0], "BINANCE_USD_M_FUNDING_HISTORY")


if __name__ == "__main__":
    unittest.main()
