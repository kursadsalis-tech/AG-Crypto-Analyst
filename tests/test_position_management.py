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
        for sql in (
            "ALTER TABLE paper_agent_positions ADD COLUMN liquidation_price REAL",
            "ALTER TABLE paper_agent_positions ADD COLUMN leverage REAL NOT NULL DEFAULT 1",
            "ALTER TABLE paper_agent_positions ADD COLUMN liquidation_fee REAL NOT NULL DEFAULT 0",
            "ALTER TABLE paper_agent_positions ADD COLUMN liquidated INTEGER NOT NULL DEFAULT 0",
        ):
            try:
                self.con.execute(sql)
            except sqlite3.OperationalError:
                pass
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

    def test_spot_close_has_no_funding_segment_or_liquidation(self):
        self.con.execute("""UPDATE paper_agent_positions SET market_type='SPOT',
            liquidation_price=99 WHERE position_id=1""")
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        live = self.live(98, 2)
        live.market_type = "SPOT"
        result = process_market_state(
            self.con, live, ENTRY + timedelta(seconds=2), self.rules,
        )
        self.assertEqual(result["status"], "POSITION_CLOSED")
        row = self.con.execute("""SELECT funding_source,liquidated
            FROM paper_agent_positions""").fetchone()
        self.assertEqual(row, ("SPOT_NOT_APPLICABLE", 0))
        self.assertEqual(self.con.execute("""SELECT COUNT(*)
            FROM paper_funding_reconciliation_segments""").fetchone()[0], 0)

    def test_rejected_exit_is_throttled_and_creates_no_funding_segment(self):
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        illiquid = self.live(98, 2)
        illiquid.visible_quote_volume = .01
        first = process_market_state(
            self.con, illiquid, ENTRY + timedelta(seconds=2), self.rules,
        )
        second = process_market_state(
            self.con, self.live(98, 2.1), ENTRY + timedelta(seconds=2.1), self.rules,
        )
        self.assertEqual(first, {"status": "BLOCKED", "reason": "EXCHANGE_FILTER_REJECTION"})
        self.assertEqual(second["status"], "EXIT_RETRY_THROTTLED")
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM paper_funding_reconciliation_segments"
        ).fetchone()[0], 0)

    def test_funding_segments_are_loaded_once_and_costed_at_exposed_quantity(self):
        from paper_trading.futures_costs import FundingEvent
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        self.con.executemany("""INSERT INTO paper_funding_reconciliation_segments(
            position_id,start_at,end_at,direction,quantity,status
        ) VALUES(1,?,?,?,?,'PENDING')""", [
            (ENTRY.isoformat(), (ENTRY + timedelta(hours=1)).isoformat(), "LONG", 10),
            ((ENTRY + timedelta(hours=1)).isoformat(),
             (ENTRY + timedelta(hours=2)).isoformat(), "LONG", 5),
        ])
        calls = []
        def loader(**kwargs):
            calls.append(kwargs)
            return [FundingEvent("BTCUSDT", int((ENTRY + timedelta(hours=1, minutes=30)).timestamp()*1000), .001, 100)]
        result = reconcile_pending_funding(self.con, loader, ENTRY + timedelta(hours=3))
        self.assertEqual(result, {"resolved": 2, "blocked": 0})
        self.assertEqual(len(calls), 1)
        self.assertAlmostEqual(self.con.execute(
            "SELECT funding_cost FROM paper_agent_positions"
        ).fetchone()[0], .5)

    def test_short_timeout_closes_one_exact_quantity_step(self):
        self.con.execute("""UPDATE paper_agent_positions SET direction='SHORT',
            entry_price=80000,quantity=.001,notional=80,entry_fee=.04,
            due_time=? WHERE position_id=1""", (
                (ENTRY + timedelta(seconds=1)).isoformat(),))
        self.con.execute("UPDATE asset_risk_snapshots SET volatility_stop_reference_pct=2")
        initialize_open_positions(self.con, ENTRY + timedelta(milliseconds=500))
        live = LiveState(
            "BTCUSDT", last_price=77680, best_bid=77679.9, best_ask=77680.1,
            book_imbalance=.1, taker_imbalance=.1,
            visible_quote_volume=1_000_000,
            market_timestamp_ms=int((ENTRY + timedelta(seconds=2)).timestamp()*1000),
        )
        result = process_market_state(self.con, live, ENTRY + timedelta(seconds=2),
                                      self.rules)
        self.assertEqual(result["status"], "POSITION_CLOSED")
        self.assertEqual(self.con.execute(
            "SELECT remaining_quantity FROM paper_position_management"
        ).fetchone()[0], 0)

    def test_five_x_gap_liquidation_is_costed_and_audited(self):
        self.con.execute("""UPDATE paper_agent_positions SET leverage=5,
            margin_allocated=200,liquidation_price=80 WHERE position_id=1""")
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        result = process_market_state(
            self.con, self.live(79, 2), ENTRY + timedelta(seconds=2),
            ExecutionRules(quantity_step=.001, price_tick=.01, min_notional=5,
                           require_liquidity=True, liquidation_fee_rate=.0125),
        )
        self.assertEqual(result["reason"], "PAPER_5X_LIQUIDATION")
        row = self.con.execute("""SELECT status,liquidated,liquidation_fee,exit_price
            FROM paper_agent_positions""").fetchone()
        self.assertEqual(row[:2], ("CLOSED", 1))
        self.assertAlmostEqual(row[2], 10.0)
        self.assertEqual(row[3], 80)

    def test_opposing_flow_cannot_invalidate_news_only_thesis(self):
        self.con.execute("""UPDATE paper_agent_positions SET
            independent_signal_count=1,signal_sources_json='[\"NEWS\"]'
            WHERE position_id=1""")
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        for seconds in (2, 3, 4, 5):
            result = process_market_state(
                self.con, self.live(100, seconds, book=-.9, taker=-.9),
                ENTRY + timedelta(seconds=seconds), self.rules,
            )
        self.assertNotEqual(result.get("reason"), "THESIS_INVALIDATED")
        self.assertEqual(self.con.execute(
            "SELECT status FROM paper_agent_positions"
        ).fetchone()[0], "OPEN")


if __name__ == "__main__":
    unittest.main()
