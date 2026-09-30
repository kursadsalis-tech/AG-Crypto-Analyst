import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from paper_trading.execution_contract import ExecutionRules, ExecutionDecision, ExecutionFill
from paper_trading.position_management import (
    ensure_tables, initialize_open_positions, process_market_state,
    reconcile_pending_funding, record_execution, resolve_position_rules, adopt_legacy_open_rules,
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
            visible_quote_volume=1_000_000, bid_quote_volume=1_000_000,
            ask_quote_volume=1_000_000, quote_source="TEST_BOOK",
            market_timestamp_ms=int((ENTRY + timedelta(seconds=seconds)).timestamp()*1000),
            quote_timestamp_ms=int((ENTRY + timedelta(seconds=seconds)).timestamp()*1000),
            liquidity_timestamp_ms=int((ENTRY + timedelta(seconds=seconds)).timestamp()*1000),
            trade_timestamp_ms=int((ENTRY + timedelta(seconds=seconds)).timestamp()*1000),
        )

    def set_flow_thesis(self, pattern, direction="LONG"):
        self.con.execute("""UPDATE paper_agent_positions SET direction=?,
            independent_signal_count=1,signal_sources_json='["MARKET_BEHAVIOR"]'
            WHERE position_id=1""", (direction,))
        self.con.execute("""CREATE TABLE IF NOT EXISTS paper_agent_decision_theses(
            position_id INTEGER PRIMARY KEY,thesis_json TEXT)""")
        self.con.execute("INSERT OR REPLACE INTO paper_agent_decision_theses VALUES(1,?)", (
            json.dumps({"evidence_used": {"learned_pattern": {
                "flow_pattern": pattern, "direction": direction,
            }}}),
        ))
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))

    def test_weak_flow_noise_and_absorption_do_not_invalidate_continuation(self):
        self.set_flow_thesis("FLOW_POS")
        for seconds in range(2, 23):
            book, taker = (-.03, -.02) if seconds % 2 else (-.9, .8)
            result = process_market_state(
                self.con, self.live(100, seconds, book=book, taker=taker),
                ENTRY + timedelta(seconds=seconds), self.rules,
            )
            self.assertEqual(result["status"], "HOLD")
        self.assertEqual(self.con.execute(
            "SELECT opposing_events FROM paper_position_management"
        ).fetchone()[0], 0)

    def test_short_material_burst_and_neutral_gap_reset_confirmation(self):
        self.set_flow_thesis("FLOW_POS")
        for seconds, opposing in ((2, True), (3, True), (4, True), (5, False),
                                  (7, True), (12, True), (12.1, False), (17, True)):
            strength = -.8 if opposing else -.01
            result = process_market_state(
                self.con, self.live(100, seconds, book=strength, taker=strength),
                ENTRY + timedelta(seconds=seconds), self.rules,
            )
            self.assertEqual(result["status"], "HOLD")
        self.assertEqual(self.con.execute(
            "SELECT opposing_events FROM paper_position_management"
        ).fetchone()[0], 1)

    def assert_persistent_continuation_failure_closes(self, direction):
        self.set_flow_thesis("FLOW_POS" if direction == "LONG" else "FLOW_NEG", direction)
        original_thesis = self.con.execute(
            "SELECT thesis_json FROM paper_agent_decision_theses"
        ).fetchone()[0]
        strength = -.8 if direction == "LONG" else .8
        for seconds in (2, 7, 12):
            result = process_market_state(
                self.con, self.live(100, seconds, book=strength, taker=strength),
                ENTRY + timedelta(seconds=seconds), self.rules,
            )
            self.assertEqual(result["status"], "POSITION_CLOSED" if seconds == 12 else "HOLD")
        self.assertEqual(result["reason"], "THESIS_INVALIDATED")
        evidence = json.loads(self.con.execute("""SELECT evidence_json
            FROM paper_position_events WHERE event_type='POSITION_CLOSED'""").fetchone()[0])
        confirmation = evidence["position_decision"]["flow_confirmation"]
        self.assertEqual(confirmation["status"], "MATERIAL_OPPOSITION_CONFIRMED")
        self.assertEqual(confirmation["confirmations_required"], 3)
        self.assertEqual(evidence["position_decision"]["opposing_events_after"], 3)
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM paper_position_events WHERE real_order_allowed!=0"
        ).fetchone()[0], 0)
        self.assertEqual(self.con.execute(
            "SELECT thesis_json FROM paper_agent_decision_theses"
        ).fetchone()[0], original_thesis)

    def test_persistent_material_long_continuation_failure_closes(self):
        self.assert_persistent_continuation_failure_closes("LONG")

    def test_persistent_material_short_continuation_failure_closes(self):
        self.assert_persistent_continuation_failure_closes("SHORT")

    def test_reversal_entry_flow_does_not_disprove_its_own_thesis(self):
        self.set_flow_thesis("FLOW_NEG")
        for seconds in (2, 7, 12):
            result = process_market_state(
                self.con, self.live(100, seconds, book=-.9, taker=-.9),
                ENTRY + timedelta(seconds=seconds), self.rules,
            )
            self.assertEqual(result["status"], "HOLD")
        stopped = process_market_state(
            self.con, self.live(98, 12.1, book=-.9, taker=-.9),
            ENTRY + timedelta(seconds=12.1), self.rules,
        )
        self.assertEqual((stopped["status"], stopped["reason"]),
                         ("POSITION_CLOSED", "PROTECTIVE_STOP"))

    def test_stale_or_reused_trade_evidence_cannot_accumulate_confirmations(self):
        self.set_flow_thesis("FLOW_POS")
        for seconds in (2, 3, 7, 12):
            live = self.live(100, seconds, book=-.9, taker=-.9)
            live.trade_timestamp_ms = int((ENTRY + timedelta(seconds=2)).timestamp()*1000)
            result = process_market_state(
                self.con, live, ENTRY + timedelta(seconds=seconds), self.rules,
            )
            self.assertEqual(result["status"], "HOLD")
        self.assertEqual(self.con.execute(
            "SELECT opposing_events FROM paper_position_management"
        ).fetchone()[0], 0)

    def test_missing_flow_and_separated_bursts_do_not_accumulate(self):
        self.set_flow_thesis("FLOW_POS")
        for seconds in (2, 7, 30):
            result = process_market_state(
                self.con, self.live(100, seconds, book=-.9, taker=-.9),
                ENTRY + timedelta(seconds=seconds), self.rules,
            )
            self.assertEqual(result["status"], "HOLD")
        self.assertEqual(self.con.execute(
            "SELECT opposing_events FROM paper_position_management"
        ).fetchone()[0], 1)
        live = self.live(100, 30.1, book=None, taker=-.9)
        process_market_state(self.con, live, ENTRY + timedelta(seconds=30.1), self.rules)
        self.assertEqual(self.con.execute(
            "SELECT opposing_events FROM paper_position_management"
        ).fetchone()[0], 0)

    def test_protective_stop_does_not_wait_for_flow_confirmation(self):
        self.set_flow_thesis("FLOW_POS")
        result = process_market_state(
            self.con, self.live(98, 1.1, book=-.9, taker=-.9),
            ENTRY + timedelta(seconds=1.1), self.rules,
        )
        self.assertEqual((result["status"], result["reason"]),
                         ("POSITION_CLOSED", "PROTECTIVE_STOP"))

    def test_liquidation_does_not_wait_for_flow_confirmation(self):
        self.set_flow_thesis("FLOW_POS")
        self.con.execute("""UPDATE paper_agent_positions SET leverage=5,
            margin_allocated=200,liquidation_price=80 WHERE position_id=1""")
        result = process_market_state(
            self.con, self.live(79, 1.1, book=-.9, taker=-.9),
            ENTRY + timedelta(seconds=1.1), self.rules,
        )
        self.assertEqual((result["status"], result["reason"]),
                         ("POSITION_CLOSED", "PAPER_5X_LIQUIDATION"))

    def test_initializes_stop_from_entry_risk_snapshot(self):
        result = initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        self.assertEqual(result, {"initialized": 1, "blocked": []})
        row = self.con.execute("SELECT current_stop_price,remaining_quantity FROM paper_position_management").fetchone()
        self.assertEqual(row, (98.0, 10.0))
        self.assertEqual(self.con.execute("SELECT real_order_allowed FROM paper_position_events").fetchone()[0], 0)

    def test_frozen_technical_plan_initializes_exact_stop_and_targets(self):
        self.con.execute("""
            CREATE TABLE paper_agent_decision_theses(
                position_id INTEGER PRIMARY KEY, thesis_json TEXT
            )
        """)
        plan = {
            "version": "TEST_PLAN", "symbol": "BTCUSDT", "direction": "LONG",
            "execution_market_type": "USD_M_PERPETUAL",
            "source_reference_price": 100.0,
            "entry": {"anchor": 100.0, "low": 99.8, "high": 100.2},
            "risk": {"invalidation_price": 98.5, "stop_distance_pct": 1.7},
            "targets": {"tp1_price": 101.5, "tp2_price": 103.0},
        }
        thesis = {
            "technical_trade_plan": plan,
            "execution_risk": {"planned_stop_distance_pct": 1.7},
        }
        self.con.execute(
            "INSERT INTO paper_agent_decision_theses VALUES(1,?)",
            (json.dumps(thesis),),
        )
        self.con.execute(
            "UPDATE paper_agent_positions SET technical_basis_ratio=1,"
            " market_type='USD_M_PERPETUAL' WHERE position_id=1"
        )
        result = initialize_open_positions(
            self.con, ENTRY + timedelta(seconds=1)
        )
        self.assertEqual(result, {"initialized": 1, "blocked": []})
        row = self.con.execute(
            "SELECT current_stop_price,tp1_price,tp2_price,stop_distance_pct "
            "FROM paper_position_management"
        ).fetchone()
        self.assertAlmostEqual(row[0], 98.5)
        self.assertAlmostEqual(row[1], 101.5)
        self.assertAlmostEqual(row[2], 103.0)
        self.assertAlmostEqual(row[3], 1.5)
        reduced = process_market_state(
            self.con, self.live(101.5, 2),
            ENTRY + timedelta(seconds=2), self.rules,
        )
        self.assertEqual(
            (reduced["status"], reduced["reason"]),
            ("POSITION_REDUCED", "TAKE_PROFIT_TECHNICAL_1"),
        )

    def set_frozen_technical_stop(self, direction, entry_price, stop_price,
                                 entry_low=99.8, entry_high=100.2,
                                 planned_stop_pct=1.0):
        plan = {
            "version": "TECHNICAL_TRADE_PLAN_V3_ORDERED_TARGETS",
            "symbol": "BTCUSDT", "direction": direction,
            "execution_market_type": "USD_M_PERPETUAL",
            "source_reference_price": entry_price,
            "entry": {"anchor": entry_price, "low": entry_low, "high": entry_high},
            "risk": {"invalidation_price": stop_price,
                     "stop_distance_pct": planned_stop_pct},
            "targets": {
                "tp1_price": entry_price * (1.01 if direction == "LONG" else .99),
                "tp2_price": entry_price * (1.02 if direction == "LONG" else .98),
            },
        }
        thesis = {
            "technical_trade_plan": plan,
            "execution_risk": {"planned_stop_distance_pct": planned_stop_pct},
        }
        plan_json, thesis_json = json.dumps(plan), json.dumps(thesis)
        self.con.execute("""CREATE TABLE IF NOT EXISTS paper_agent_decision_theses(
            position_id INTEGER PRIMARY KEY, thesis_json TEXT)""")
        self.con.execute(
            "INSERT OR REPLACE INTO paper_agent_decision_theses VALUES(1,?)",
            (thesis_json,),
        )
        self.con.execute("""UPDATE paper_agent_positions
            SET direction=?,entry_price=?,technical_basis_ratio=1,
                market_type='USD_M_PERPETUAL',technical_execution_plan_json=?
            WHERE position_id=1""", (direction, entry_price, plan_json))
        return plan, plan_json, thesis_json

    def assert_better_fill_preserves_tighter_frozen_stop(
        self, direction, entry_price, stop_price, entry_low, entry_high,
    ):
        worst_entry = entry_high if direction == "LONG" else entry_low
        planned_stop_pct = 100.0 * abs(worst_entry - stop_price) / worst_entry
        self.assertGreaterEqual(planned_stop_pct, .5)
        self.assertLessEqual(planned_stop_pct, 5.0)
        actual_stop_pct = 100.0 * abs(entry_price - stop_price) / entry_price
        self.assertGreater(actual_stop_pct, 0)
        self.assertLess(actual_stop_pct, .5)
        plan, plan_json, thesis_json = self.set_frozen_technical_stop(
            direction, entry_price, stop_price, entry_low, entry_high,
            planned_stop_pct,
        )

        result = initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))

        self.assertEqual(result, {"initialized": 1, "blocked": []})
        self.assertEqual(self.con.execute("""SELECT current_stop_price,
            stop_distance_pct,tp1_price,tp2_price
            FROM paper_position_management WHERE position_id=1""").fetchone(), (
                stop_price, actual_stop_pct,
                plan["targets"]["tp1_price"], plan["targets"]["tp2_price"],
            ))
        self.assertEqual(self.con.execute("""SELECT technical_execution_plan_json,
            thesis_json FROM paper_agent_positions
            JOIN paper_agent_decision_theses USING(position_id)
            WHERE position_id=1""").fetchone(), (plan_json, thesis_json))
        self.assertEqual(self.con.execute(
            "SELECT real_order_allowed FROM paper_position_events"
        ).fetchone()[0], 0)

    def test_long_better_fill_preserves_subminimum_frozen_stop(self):
        self.assert_better_fill_preserves_tighter_frozen_stop(
            "LONG", 100.35, 100.0, 100.1, 100.6,
        )

    def test_short_better_fill_preserves_subminimum_frozen_stop(self):
        self.assert_better_fill_preserves_tighter_frozen_stop(
            "SHORT", 0.26421, 0.2651342857142857,
            0.2638105714285714, 0.26456942857142857,
        )

    def test_frozen_stop_rejects_unsafe_actual_fill_distance(self):
        for direction, wrong_side, over_max in (("LONG", 101.0, 94.99),
                                                ("SHORT", 99.0, 105.01)):
            for stop_price in (wrong_side, over_max, 100.0, 0.0, -1.0,
                               float("nan"), float("inf"), -float("inf")):
                with self.subTest(direction=direction, stop_price=stop_price):
                    self.set_frozen_technical_stop(direction, 100.0, stop_price)
                    result = initialize_open_positions(
                        self.con, ENTRY + timedelta(seconds=1),
                    )
                    self.assertEqual(result, {"initialized": 0, "blocked": [
                        (1, "TECHNICAL_STOP_GEOMETRY_INVALID_AT_FILL"),
                    ]})
                    self.assertEqual(self.con.execute(
                        "SELECT COUNT(*) FROM paper_position_management"
                    ).fetchone()[0], 0)

    def test_frozen_stop_accepts_exact_five_percent_actual_distance(self):
        for direction, stop_price in (("LONG", 95.0), ("SHORT", 105.0)):
            with self.subTest(direction=direction):
                self.con.execute("DELETE FROM paper_position_management")
                self.set_frozen_technical_stop(direction, 100.0, stop_price)
                result = initialize_open_positions(
                    self.con, ENTRY + timedelta(seconds=1),
                )
                self.assertEqual(result, {"initialized": 1, "blocked": []})
                self.assertEqual(self.con.execute("""SELECT current_stop_price,
                    stop_distance_pct FROM paper_position_management""").fetchone(),
                    (stop_price, 5.0))

    def test_frozen_technical_plan_initializes_without_entry_risk_snapshot(self):
        self.con.execute("""
            CREATE TABLE paper_agent_decision_theses(
                position_id INTEGER PRIMARY KEY, thesis_json TEXT
            )
        """)
        plan = {
            "version": "TEST_PLAN", "symbol": "BTCUSDT", "direction": "LONG",
            "execution_market_type": "USD_M_PERPETUAL",
            "source_reference_price": 100.0,
            "entry": {"anchor": 100.0, "low": 99.8, "high": 100.2},
            "risk": {"invalidation_price": 98.5, "stop_distance_pct": 1.5},
            "targets": {"tp1_price": 101.5, "tp2_price": 103.0},
        }
        thesis = {
            "technical_trade_plan": plan,
            "execution_risk": {"planned_stop_distance_pct": 1.5},
        }
        self.con.execute(
            "INSERT INTO paper_agent_decision_theses VALUES(1,?)",
            (json.dumps(thesis),),
        )
        self.con.execute(
            "UPDATE paper_agent_positions SET technical_basis_ratio=1,"
            " technical_execution_plan_json=?,market_type='USD_M_PERPETUAL' "
            "WHERE position_id=1",
            (json.dumps(plan),),
        )
        self.con.execute("DELETE FROM asset_risk_snapshots")

        result = initialize_open_positions(
            self.con, ENTRY + timedelta(seconds=1)
        )

        self.assertEqual(result, {"initialized": 1, "blocked": []})
        row = self.con.execute("""
            SELECT current_stop_price,tp1_price,tp2_price
            FROM paper_position_management WHERE position_id=1
        """).fetchone()
        self.assertEqual(row, (98.5, 101.5, 103.0))
        event = self.con.execute("""
            SELECT reason,evidence_json
            FROM paper_position_events
            WHERE position_id=1 AND event_type='STOP_INITIALIZED'
        """).fetchone()
        self.assertEqual(
            event[0], "FROZEN_TECHNICAL_PLAN_WITHOUT_ENTRY_RISK_SNAPSHOT"
        )
        evidence = json.loads(event[1])
        self.assertFalse(evidence["risk_snapshot_available"])
        self.assertEqual(
            evidence["stop_reference_basis"], "FROZEN_TECHNICAL_STRUCTURE"
        )

    def test_invalid_frozen_targets_recover_with_exact_stop_and_r_targets(self):
        self.con.execute("""
            CREATE TABLE paper_agent_decision_theses(
                position_id INTEGER PRIMARY KEY, thesis_json TEXT
            )
        """)
        plan = {
            "version": "TECHNICAL_TRADE_PLAN_V2_ADAPTIVE_LOCAL",
            "symbol": "BTCUSDT", "direction": "LONG",
            "execution_market_type": "USD_M_PERPETUAL",
            "source_reference_price": 100.0,
            "entry": {"anchor": 100.0, "low": 99.8, "high": 100.2},
            "risk": {"invalidation_price": 98.5, "stop_distance_pct": 1.5},
            # Historical V2 defect: TP2 can land behind a far structural TP1.
            "targets": {"tp1_price": 104.0, "tp2_price": 103.0},
        }
        thesis = {
            "technical_trade_plan": plan,
            "execution_risk": {"planned_stop_distance_pct": 1.5},
        }
        self.con.execute(
            "INSERT INTO paper_agent_decision_theses VALUES(1,?)",
            (json.dumps(thesis),),
        )
        self.con.execute("""
            UPDATE paper_agent_positions
            SET technical_basis_ratio=1,market_type='USD_M_PERPETUAL',
                technical_execution_plan_json=?
            WHERE position_id=1
        """, (json.dumps(plan),))

        result = initialize_open_positions(
            self.con, ENTRY + timedelta(seconds=1)
        )

        self.assertEqual(result, {"initialized": 1, "blocked": []})
        row = self.con.execute("""
            SELECT current_stop_price,tp1_price,tp2_price
            FROM paper_position_management WHERE position_id=1
        """).fetchone()
        self.assertEqual(row, (98.5, None, None))

        event = self.con.execute("""
            SELECT reason,evidence_json FROM paper_position_events
            WHERE position_id=1 AND event_type='STOP_INITIALIZED'
        """).fetchone()
        self.assertEqual(
            event[0], "FROZEN_TECHNICAL_STOP_WITH_R_TARGET_FALLBACK"
        )
        evidence = json.loads(event[1])
        self.assertEqual(
            evidence["technical_target_recovery"],
            "INVALID_FROZEN_TARGET_GEOMETRY_R_FALLBACK",
        )
        self.assertEqual(evidence["frozen_technical_tp1_price"], 104.0)
        self.assertEqual(evidence["frozen_technical_tp2_price"], 103.0)

        reduced = process_market_state(
            self.con, self.live(101.6, 2),
            ENTRY + timedelta(seconds=2), self.rules,
        )
        self.assertEqual(
            (reduced["status"], reduced["reason"]),
            ("POSITION_REDUCED", "TAKE_PROFIT_1R"),
        )

    def test_revalidated_execution_plan_overrides_selection_plan_at_open(self):
        self.con.execute("""
            CREATE TABLE paper_agent_decision_theses(
                position_id INTEGER PRIMARY KEY, thesis_json TEXT
            )
        """)
        selection_plan = {
            "version": "SELECTION_PLAN",
            "symbol": "BTCUSDT", "direction": "LONG",
            "execution_market_type": "USD_M_PERPETUAL",
            "source_reference_price": 100.0,
            "entry": {"anchor": 100.0, "low": 99.8, "high": 100.2},
            "risk": {"invalidation_price": 98.5, "stop_distance_pct": 1.7},
            "targets": {"tp1_price": 101.5, "tp2_price": 103.0},
        }
        current_plan = {
            **selection_plan,
            "version": "REVALIDATED_EXECUTION_PLAN",
            "risk": {"invalidation_price": 97.5, "stop_distance_pct": 2.5},
            "targets": {"tp1_price": 102.0, "tp2_price": 104.0},
        }
        thesis = {
            "technical_trade_plan": selection_plan,
            "execution_risk": {"planned_stop_distance_pct": 1.7},
        }
        self.con.execute(
            "INSERT INTO paper_agent_decision_theses VALUES(1,?)",
            (json.dumps(thesis),),
        )
        self.con.execute("""
            UPDATE paper_agent_positions
            SET technical_basis_ratio=1,market_type='USD_M_PERPETUAL',
                technical_execution_plan_json=?
            WHERE position_id=1
        """, (json.dumps(current_plan),))

        result = initialize_open_positions(
            self.con, ENTRY + timedelta(seconds=1)
        )
        self.assertEqual(result, {"initialized": 1, "blocked": []})
        row = self.con.execute("""
            SELECT current_stop_price,tp1_price,tp2_price,stop_distance_pct
            FROM paper_position_management
        """).fetchone()
        self.assertAlmostEqual(row[0], 97.5)
        self.assertAlmostEqual(row[1], 102.0)
        self.assertAlmostEqual(row[2], 104.0)
        self.assertAlmostEqual(row[3], 2.5)

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

    def test_reduce_only_profit_slice_is_exempt_from_open_notional_minimum(self):
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        strict = ExecutionRules(quantity_step=.001, price_tick=.01, min_notional=500,
                                require_liquidity=True)
        result = process_market_state(
            self.con, self.live(102, 2), ENTRY + timedelta(seconds=2), strict,
        )
        self.assertEqual(result["status"], "POSITION_REDUCED")
        self.assertEqual(result["reason"], "TAKE_PROFIT_1R")
        self.assertEqual(self.con.execute(
            "SELECT remaining_quantity FROM paper_position_management"
        ).fetchone()[0], 7.5)

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

    def freeze_entry(self, market="USD_M_PERPETUAL", rules=None, direction="LONG"):
        rules = rules or self.rules
        record_execution(
            self.con, 1, ENTRY, "ENTRY", "BTCUSDT",
            "BUY" if direction == "LONG" else "SELL", market,
            ExecutionDecision("FILLED", "PAPER_FILL", ExecutionFill(100, 10, 1, 0, 0, False)),
            rules=rules, maintenance_margin_rate=.005,
        )
        self.con.commit()

    def assert_frozen_stop_survives_metadata_outage(self, market, direction):
        self.con.execute("UPDATE paper_agent_positions SET market_type=?,direction=? WHERE position_id=1",
                         (market, direction))
        self.freeze_entry(market, direction=direction)
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        live = self.live(98 if direction == "LONG" else 102, 2)
        live.market_type = market
        result = process_market_state(self.con, live, ENTRY + timedelta(seconds=2), None)
        self.assertEqual(result["status"], "POSITION_CLOSED")
        self.assertEqual(result["reason"], "PROTECTIVE_STOP")
        event = json.loads(self.con.execute("SELECT evidence_json FROM paper_position_events WHERE event_type='POSITION_CLOSED'").fetchone()[0])
        self.assertEqual(event["execution_rules_source"], "FROZEN_ENTRY_RULES")

    def test_usdm_open_short_stop_survives_spot_and_own_metadata_outage(self):
        self.assert_frozen_stop_survives_metadata_outage("USD_M_PERPETUAL", "SHORT")

    def test_spot_open_stop_survives_usdm_and_own_metadata_outage(self):
        self.assert_frozen_stop_survives_metadata_outage("SPOT", "LONG")

    def test_frozen_liquidation_fee_survives_live_rule_change_or_outage(self):
        rules = ExecutionRules(.001, .01, 100, True, .0125)
        self.freeze_entry(rules=rules)
        self.con.execute("UPDATE paper_agent_positions SET leverage=5,margin_allocated=200,liquidation_price=80")
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        result = process_market_state(self.con, self.live(79, 2), ENTRY + timedelta(seconds=2),
                                      ExecutionRules(.001, .01, 100, True, .5))
        self.assertEqual(result["reason"], "PAPER_5X_LIQUIDATION")
        fee, price = self.con.execute("SELECT liquidation_fee,exit_price FROM paper_agent_positions").fetchone()
        self.assertAlmostEqual(fee, price * 10 * .0125)

    def test_frozen_entry_evidence_is_immutable(self):
        self.freeze_entry()
        for sql in ("UPDATE paper_position_events SET evidence_json='{}' WHERE event_type='ENTRY_FILLED'",
                    "DELETE FROM paper_position_events WHERE event_type='ENTRY_FILLED'"):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "IMMUTABLE_ENTRY"):
                self.con.execute(sql)

    def test_corrupt_frozen_evidence_cannot_fall_back_to_current_rules(self):
        self.freeze_entry()
        self.con.execute("DROP TRIGGER immutable_paper_entry_execution_update")
        self.con.execute("UPDATE paper_position_events SET evidence_sha256=? WHERE event_type='ENTRY_FILLED'", ("0" * 64,))
        rules, source, error = resolve_position_rules(self.con, 1, "BTCUSDT", "USD_M_PERPETUAL", self.rules)
        self.assertIsNone(rules)
        self.assertEqual(error, "FROZEN_RULES_EVIDENCE_HASH_MISMATCH")

    def test_legacy_adoption_preserves_history_then_survives_helper_loss(self):
        self.set_flow_thesis("FLOW_POS")
        original_thesis = self.con.execute("SELECT thesis_json FROM paper_agent_decision_theses").fetchone()[0]
        record_execution(self.con, 1, ENTRY, "ENTRY", "BTCUSDT", "BUY", "USD_M_PERPETUAL",
                         ExecutionDecision("FILLED", "PAPER_FILL", ExecutionFill(100, 10, 1, 0, 0, False)))
        original_entry = self.con.execute("SELECT evidence_json FROM paper_position_events WHERE event_type='ENTRY_FILLED'").fetchone()[0]
        first = process_market_state(self.con, self.live(100, 2), ENTRY + timedelta(seconds=2), self.rules)
        self.assertEqual(first["status"], "HOLD")
        adopted = json.loads(self.con.execute("SELECT evidence_json FROM paper_position_events WHERE event_type='LEGACY_EXECUTION_RULES_ADOPTED'").fetchone()[0])
        self.assertFalse(adopted["historical_fill_rules_available"])
        closed = process_market_state(self.con, self.live(98, 3), ENTRY + timedelta(seconds=3), None)
        self.assertEqual(closed["status"], "POSITION_CLOSED")
        self.assertEqual(self.con.execute("SELECT thesis_json FROM paper_agent_decision_theses").fetchone()[0], original_thesis)
        self.assertEqual(self.con.execute("SELECT evidence_json FROM paper_position_events WHERE event_type='ENTRY_FILLED'").fetchone()[0], original_entry)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "IMMUTABLE_ENTRY"):
            self.con.execute("DELETE FROM paper_position_events WHERE event_type='LEGACY_EXECUTION_RULES_ADOPTED'")

    def test_legacy_without_available_route_does_not_invent_frozen_rules(self):
        result = adopt_legacy_open_rules(self.con, {}, ENTRY)
        self.assertEqual(result, {"adopted": [], "blocked": [(1, "LEGACY_POSITION_METADATA_UNAVAILABLE")]})
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM paper_position_events").fetchone()[0], 0)

    def test_legacy_hold_cannot_report_rules_healthy_when_unavailable(self):
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        result = process_market_state(self.con, self.live(100, 2), ENTRY + timedelta(seconds=2), None)
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["rules_error"], "EXCHANGE_RULES_UNAVAILABLE")

    def test_frozen_reduce_only_dust_closes_below_entry_minimum(self):
        self.con.execute("UPDATE paper_agent_positions SET quantity=.01,notional=1,margin_allocated=1,entry_fee=.001")
        self.freeze_entry(rules=ExecutionRules(.001, .01, 100, True))
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        result = process_market_state(self.con, self.live(98, 2), ENTRY + timedelta(seconds=2), None)
        self.assertEqual(result["status"], "POSITION_CLOSED")
        self.assertEqual(result["quantity"], .01)

    def test_rejected_exit_is_throttled_and_creates_no_funding_segment(self):
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        illiquid = self.live(98, 2)
        illiquid.visible_quote_volume = .01
        illiquid.bid_quote_volume = .01
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

    def test_short_fractional_profit_target_keeps_step_alignment_then_closes_remainder(self):
        self.con.execute("""UPDATE paper_agent_positions SET direction='SHORT',
            quantity=1.333,notional=133.3,margin_allocated=133.3,entry_fee=.06665""")
        self.freeze_entry(direction="SHORT")
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        reduced = process_market_state(self.con, self.live(98, 2), ENTRY + timedelta(seconds=2), None)
        self.assertEqual(reduced["status"], "POSITION_REDUCED")
        self.assertEqual(reduced["quantity"], .333)
        self.assertEqual(self.con.execute("SELECT remaining_quantity FROM paper_position_management").fetchone()[0], 1.0)
        closed = process_market_state(self.con, self.live(101, 3), ENTRY + timedelta(seconds=3), None)
        self.assertEqual(closed["status"], "POSITION_CLOSED")
        self.assertEqual(closed["quantity"], 1.0)
        self.assertEqual(self.con.execute("SELECT remaining_quantity FROM paper_position_management").fetchone()[0], 0.0)
        executed = self.con.execute("SELECT SUM(fill_quantity) FROM paper_position_events WHERE event_type IN ('POSITION_REDUCED','POSITION_CLOSED')").fetchone()[0]
        self.assertAlmostEqual(executed, 1.333)

    def test_step_aligned_short_exit_still_respects_visible_quantity(self):
        self.con.execute("""UPDATE paper_agent_positions SET direction='SHORT',
            quantity=1.333,notional=133.3,margin_allocated=133.3""")
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        live = self.live(98, 2)
        live.ask_quote_volume = .2 * live.best_ask
        result = process_market_state(self.con, live, ENTRY + timedelta(seconds=2), self.rules)
        self.assertEqual(result["status"], "POSITION_REDUCED")
        self.assertLessEqual(result["quantity"], .2)
        self.assertAlmostEqual(result["quantity"] / self.rules.quantity_step,
                               round(result["quantity"] / self.rules.quantity_step))
        self.assertGreaterEqual(self.con.execute("SELECT remaining_quantity FROM paper_position_management").fetchone()[0], 1.133)

    def test_true_legacy_off_step_dust_is_never_invented_or_marked_closed(self):
        self.con.execute("""UPDATE paper_agent_positions SET direction='SHORT',
            quantity=1.33325,notional=133.325,margin_allocated=133.325""")
        initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        reduced = process_market_state(self.con, self.live(103, 2), ENTRY + timedelta(seconds=2), self.rules)
        self.assertEqual(reduced["status"], "POSITION_REDUCED")
        self.assertEqual(reduced["quantity"], 1.333)
        self.assertAlmostEqual(self.con.execute("SELECT remaining_quantity FROM paper_position_management").fetchone()[0], .00025)
        self.assertEqual(self.con.execute("SELECT status FROM paper_agent_positions").fetchone()[0], "OPEN")
        result = process_market_state(self.con, self.live(103, 3), ENTRY + timedelta(seconds=3), self.rules)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertAlmostEqual(self.con.execute("SELECT remaining_quantity FROM paper_position_management").fetchone()[0], .00025)

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
            visible_quote_volume=1_000_000, bid_quote_volume=1_000_000,
            ask_quote_volume=1_000_000, quote_source="TEST_BOOK",
            market_timestamp_ms=int((ENTRY + timedelta(seconds=2)).timestamp()*1000),
            quote_timestamp_ms=int((ENTRY + timedelta(seconds=2)).timestamp()*1000),
            liquidity_timestamp_ms=int((ENTRY + timedelta(seconds=2)).timestamp()*1000),
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
        self.assertLess(row[3], 78.99)
        self.assertAlmostEqual(row[2], 10 * row[3] * .0125)
        # Isolated-margin accounting must remain cash-reconcilable even when
        # the executable quote gaps beyond the theoretical liquidation level.
        net_pnl = self.con.execute(
            "SELECT net_pnl FROM paper_agent_positions"
        ).fetchone()[0]
        realized_pnl, total_fees = self.con.execute(
            "SELECT realized_pnl,total_fees FROM paper_agent_portfolio"
        ).fetchone()
        self.assertAlmostEqual(net_pnl, -201.0)
        self.assertAlmostEqual(realized_pnl, net_pnl)
        self.assertGreater(total_fees, 1.0 + row[2])

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


    def test_legacy_margin_backfill_respects_position_leverage(self):
        self.con.execute("DELETE FROM paper_agent_positions")
        # Use the legacy fixture's actual schema: the backfill needs only
        # notional, leverage and a missing margin, not forecast metadata.
        self.con.execute("""INSERT INTO paper_agent_positions(
            position_id,symbol,direction,status,entry_time,entry_price,
            quantity,notional,entry_fee,due_time,leverage,margin_allocated
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
            999,"LEVUSDT","LONG","CANCELLED",ENTRY.isoformat(),100.0,
            10.0,1000.0,0.0,ENTRY.isoformat(),5.0,None,
        ))
        ensure_tables(self.con)
        margin = self.con.execute(
            "SELECT margin_allocated FROM paper_agent_positions WHERE position_id=999"
        ).fetchone()[0]
        self.assertAlmostEqual(margin, 200.0)


if __name__ == "__main__":
    unittest.main()
