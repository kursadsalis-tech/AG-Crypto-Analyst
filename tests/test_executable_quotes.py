"""Decision 026 regressions: fills must prove fresh executable sides."""
import hashlib
import json
import os
import sqlite3
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from paper_trading import crypto_profit_agent as agent, position_management as manager
from paper_trading.execution_contract import ExecutionRules, MarketSnapshot, OrderIntent
from paper_trading.execution_model import execute_paper, simulated_fill
from paper_trading.governance import AUTHORITY, validate_agent_authority
from paper_trading.realtime_market import LiveState, apply_binance_message

NOW = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
MS = int(NOW.timestamp() * 1000)
RULES = ExecutionRules(.000001, .000001, 5, True, .01)


def quote(**changes):
    result = MarketSnapshot(
        reference_price=777, available_quote_volume=1_000_000,
        observed_at=NOW.isoformat(), best_bid=99, best_ask=101,
        quote_timestamp_ms=MS, liquidity_timestamp_ms=MS,
        market_type="SPOT", source="TEST_BOOK",
    )
    return replace(result, **changes)


class QuoteContractTests(unittest.TestCase):
    def decision(self, side="BUY", market=None, rules=RULES):
        return execute_paper(OrderIntent("DYNAMICUSDT", side, 100), market or quote(), rules)

    def test_buy_ask_sell_bid_ignore_last_reference(self):
        for side, boundary in (("BUY", 101), ("SELL", 99)):
            with self.subTest(side=side):
                result = self.decision(side)
                self.assertEqual(result.evidence["executable_reference_price"], boundary)
                self.assertGreaterEqual(result.fill.price, boundary) if side == "BUY" else self.assertLessEqual(result.fill.price, boundary)
                self.assertEqual(result.evidence["real_order_allowed"], 0)

    def test_missing_and_invalid_sides_never_use_last(self):
        for field, label in (("best_ask", "ASK"), ("best_bid", "BID")):
            for value in (None, 0, -1, float("nan"), float("inf")):
                for side in ("BUY", "SELL"):
                    with self.subTest(field=field, value=value, side=side):
                        result = self.decision(side, quote(**{field: value}))
                        self.assertIsNone(result.fill)
                        self.assertEqual(result.reason, ("MISSING_" if value is None else "INVALID_") + label)

    def test_stale_future_and_invalid_timestamps(self):
        for value, reason in ((MS-2001, "STALE_QUOTE"), (MS+1, "FUTURE_QUOTE"),
                              (None, "INVALID_QUOTE_TIMESTAMP"), (0, "INVALID_QUOTE_TIMESTAMP"),
                              (float("nan"), "INVALID_QUOTE_TIMESTAMP")):
            with self.subTest(value=value):
                result = self.decision(market=quote(quote_timestamp_ms=value))
                self.assertIsNone(result.fill)
                self.assertEqual(result.reason, reason)
        self.assertIsNotNone(self.decision(market=quote(quote_timestamp_ms=MS-2000)).fill)
        self.assertEqual(self.decision(market=quote(observed_at="bad")).reason, "INVALID_QUOTE_TIMESTAMP")
        self.assertEqual(self.decision(market=quote(observed_at="2026-09-19T12:00:00")).reason, "INVALID_QUOTE_TIMESTAMP")

    def test_missing_stale_liquidity_rules_and_source(self):
        cases = [
            (quote(available_quote_volume=None), "MISSING_LIQUIDITY"),
            (quote(available_quote_volume=0), "INVALID_LIQUIDITY"),
            (quote(liquidity_timestamp_ms=None), "MISSING_LIQUIDITY_TIMESTAMP"),
            (quote(liquidity_timestamp_ms=MS-2001), "STALE_LIQUIDITY"),
            (quote(source=None), "QUOTE_SOURCE_UNAVAILABLE"),
        ]
        for market, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(self.decision(market=market).reason, reason)
                self.assertIsNone(self.decision(market=market).fill)
        self.assertEqual(self.decision(rules=None).reason, "EXCHANGE_RULES_UNAVAILABLE")

    def test_no_double_spread_even_when_configured_spread_is_large(self):
        with patch.dict(os.environ, {"AGCA_PAPER_SPREAD_BPS": "0"}):
            first = self.decision()
        with patch.dict(os.environ, {"AGCA_PAPER_SPREAD_BPS": "9999"}):
            second = self.decision()
        self.assertEqual(first.fill, second.fill)
        self.assertEqual(first.fill.spread_cost, 0)
        self.assertEqual(first.evidence["observed_spread"], 2)

    def test_reference_only_wrapper_cannot_execute_candle_or_last(self):
        for side in ("BUY", "SELL"):
            self.assertIsNone(simulated_fill(reference_price=100, requested_notional=100,
                                             side=side, available_quote_volume=100000))

    def test_sell_cannot_exceed_visible_base_quantity(self):
        result = execute_paper(OrderIntent("DYNAMICUSDT", "SELL", 1000, reduce_only=True),
                               quote(available_quote_volume=99), RULES)
        self.assertLessEqual(result.fill.quantity, 1)
        self.assertTrue(result.fill.partial)

    def test_paper_only_authority_is_unchanged(self):
        self.assertFalse(AUTHORITY.may_send_real_orders)
        with self.assertRaisesRegex(RuntimeError, "NON_PAPER"):
            validate_agent_authority(execution_mode="LIVE", policy_version=agent.POLICY_VERSION,
                                     initial_capital=10000)


class QuoteAccountingTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        agent.ensure_tables(self.con)
        self.con.execute("""CREATE TABLE asset_risk_snapshots(
            id INTEGER PRIMARY KEY,symbol TEXT,created_at TEXT,
            volatility_stop_reference_pct REAL)""")
        self.addCleanup(self.con.close)
        from paper_trading.exchange_metadata import CachedExchangeMetadata, OFFICIAL_ENDPOINTS
        self.metadata = {market: CachedExchangeMetadata(market, OFFICIAL_ENDPOINTS[market],
            datetime.now(timezone.utc).isoformat(), "a" * 64, {"DYNAMICUSDT": RULES})
            for market in ("SPOT", "USD_M_PERPETUAL")}
        rules_patch = patch.object(agent, "rules_for", return_value=RULES)
        rules_patch.start()
        self.addCleanup(rules_patch.stop)
        self.con.execute("""INSERT INTO paper_agent_positions(
            forecast_id,agent_version,policy_version,symbol,horizon_hours,direction,status,
            selected_at,as_of,planned_entry_time,due_time,notional,confidence,p_up,p_down,
            target_price,model_version,artifact_hash,selection_reason,market_type)
            VALUES(1,'test','test','DYNAMICUSDT',4,'LONG','PENDING_ENTRY',?,?,?,?,100.1,.6,.6,.4,
                   102,'test','test','test','SPOT')""",
            (NOW.isoformat(), NOW.isoformat(), NOW.isoformat(), (NOW+timedelta(hours=4)).isoformat()))

    def audit(self):
        row = self.con.execute("""SELECT evidence_json,evidence_sha256,real_order_allowed
            FROM paper_position_events ORDER BY event_id DESC LIMIT 1""").fetchone()
        self.assertEqual(row[2], 0)
        self.assertEqual(hashlib.sha256(row[0].encode()).hexdigest(), row[1])
        result = json.loads(row[0])
        self.assertEqual(result["real_order_allowed"], 0)
        return result

    def entry(self, market):
        with patch.object(agent.execution_market_data, "capture_reference", return_value=market):
            return agent.fill_pending(self.con, NOW, self.metadata)

    def open_due(self, direction="LONG"):
        self.con.execute("""UPDATE paper_agent_positions SET status='OPEN',direction=?,
            entry_time=?,entry_price=100,quantity=1,notional=100,margin_allocated=100,
            due_time=?""", (direction, (NOW-timedelta(hours=4)).isoformat(), NOW.isoformat()))

    def live(self, direction="LONG", **changes):
        result = LiveState("DYNAMICUSDT", "SPOT", last_price=777,
                           best_bid=99, best_ask=101, quote_timestamp_ms=MS,
                           liquidity_timestamp_ms=MS, market_timestamp_ms=MS,
                           quote_source="TEST_BOOK", visible_quote_volume=2_000_000,
                           bid_quote_volume=1_000_000, ask_quote_volume=1_000_000)
        for name, value in changes.items():
            setattr(result, name, value)
        return result

    def test_long_entry_uses_ask_and_full_audit_is_persisted(self):
        self.assertEqual(self.entry(quote()), (1, 0))
        price = self.con.execute("SELECT entry_price FROM paper_agent_positions").fetchone()[0]
        self.assertGreaterEqual(price, 101)
        audit = self.audit()
        required = {"symbol", "side", "market_type", "decision_at", "observed_at",
                    "quote_timestamp_ms", "best_bid", "best_ask", "selected_executable_side",
                    "executable_reference_price", "final_simulated_fill_price", "quote_age_ms",
                    "fee", "slippage", "quantity", "notional", "partial", "status", "rejection_reason"}
        self.assertTrue(required <= audit.keys())
        self.assertEqual(audit["selected_executable_side"], "ASK")
        self.assertEqual(audit["final_simulated_fill_price"], price)

    def test_actual_rounded_entry_below_100_is_not_opened(self):
        self.con.execute("UPDATE paper_agent_positions SET notional=100")
        before = self.con.execute("SELECT cash,total_fees FROM paper_agent_portfolio").fetchone()
        self.assertEqual(self.entry(quote()), (0, 1))
        self.assertEqual(self.audit()["rejection_reason"], "ACTUAL_OPEN_NOTIONAL_BELOW_100_USDT")
        self.assertEqual(self.con.execute("SELECT cash,total_fees FROM paper_agent_portfolio").fetchone(), before)

    def test_short_entry_uses_bid(self):
        self.con.execute("UPDATE paper_agent_positions SET direction='SHORT',market_type='USD_M_PERPETUAL'")
        self.assertEqual(self.entry(quote(market_type="USD_M_PERPETUAL")), (1, 0))
        self.assertLessEqual(self.con.execute("SELECT entry_price FROM paper_agent_positions").fetchone()[0], 99)
        self.assertEqual(self.audit()["selected_executable_side"], "BID")

    def test_entry_missing_ask_stays_unfilled_despite_reference(self):
        before = self.con.execute("SELECT cash FROM paper_agent_portfolio").fetchone()
        self.assertEqual(self.entry(quote(best_ask=None)), (0, 1))
        self.assertEqual(self.audit()["rejection_reason"], "MISSING_ASK")
        self.assertEqual(self.con.execute("SELECT cash FROM paper_agent_portfolio").fetchone(), before)
        self.assertIsNone(self.con.execute("SELECT entry_price FROM paper_agent_positions").fetchone()[0])

    def test_stale_entry_is_rejected_and_age_audited(self):
        self.assertEqual(self.entry(quote(quote_timestamp_ms=MS-3000)), (0, 1))
        self.assertEqual(self.audit()["quote_age_ms"], 3000)
        self.assertEqual(self.audit()["rejection_reason"], "STALE_QUOTE")

    def test_missing_rules_blocks_entry(self):
        self.assertEqual(agent.fill_pending(self.con, NOW), (0, 1))
        self.assertEqual(self.audit()["rejection_reason"], "EXCHANGE_RULES_UNAVAILABLE")

    def test_long_exit_uses_bid_not_last(self):
        self.open_due()
        manager.initialize_open_positions(self.con, NOW)
        result = manager.process_market_state(self.con, self.live(), NOW, RULES)
        self.assertEqual(result["status"], "POSITION_CLOSED")
        self.assertLessEqual(self.con.execute("SELECT exit_price FROM paper_agent_positions").fetchone()[0], 99)
        self.assertEqual(self.audit()["executable_reference_price"], 99)

    def test_short_exit_uses_ask_not_last(self):
        self.open_due("SHORT")
        manager.initialize_open_positions(self.con, NOW)
        result = manager.process_market_state(self.con, self.live(), NOW, RULES)
        self.assertEqual(result["status"], "POSITION_CLOSED")
        self.assertGreaterEqual(self.con.execute("SELECT exit_price FROM paper_agent_positions").fetchone()[0], 101)
        self.assertEqual(self.audit()["executable_reference_price"], 101)

    def test_exit_missing_bid_cannot_use_last_or_acquire_fallback(self):
        self.open_due()
        manager.initialize_open_positions(self.con, NOW)
        with patch.object(agent.execution_market_data, "fetch_executable_quote", side_effect=AssertionError("no fallback")):
            result = manager.process_market_state(self.con, self.live(best_bid=None), NOW, RULES)
        self.assertEqual(result, {"status": "BLOCKED", "reason": "MISSING_BID"})
        self.assertEqual(self.audit()["rejection_reason"], "MISSING_BID")
        self.assertEqual(self.con.execute("SELECT status,exit_price FROM paper_agent_positions").fetchone(), ("OPEN", None))

    def test_recent_trade_cannot_refresh_old_quote(self):
        self.open_due()
        manager.initialize_open_positions(self.con, NOW)
        live = self.live(quote_timestamp_ms=MS-3000)
        states = {live.symbol: live}
        apply_binance_message(states, {"e": "aggTrade", "s": live.symbol, "E": MS,
                                       "p": "777", "q": "1", "m": False}, MS)
        result = manager.process_market_state(self.con, live, NOW, RULES)
        self.assertEqual(result["reason"], "STALE_QUOTE")
        self.assertEqual(self.audit()["quote_age_ms"], 3000)

    def test_legacy_due_close_cannot_use_candle_without_quote(self):
        self.open_due()
        with patch.object(agent.execution_market_data, "capture_reference", side_effect=ValueError("source unavailable")), \
                patch.object(agent, "exact_close", side_effect=AssertionError("no candle execution")):
            self.assertEqual(agent.settle_open(self.con, NOW, self.metadata), 0)
        self.assertEqual(self.con.execute("SELECT status,exit_price FROM paper_agent_positions").fetchone(), ("OPEN", None))
        self.assertEqual(self.audit()["rejection_reason"], "EXECUTABLE_QUOTE_UNAVAILABLE")

    def test_legacy_due_close_uses_current_executable_quote(self):
        self.open_due()
        with patch.object(agent.execution_market_data, "capture_reference", return_value=quote()), \
                patch.object(agent, "exact_close", side_effect=AssertionError("no candle execution")):
            self.assertEqual(agent.settle_open(self.con, NOW, self.metadata), 1)
        self.assertEqual(self.audit()["executable_reference_price"], 99)

    def test_rejected_exit_preserves_quantity_cash_and_funding(self):
        self.open_due()
        manager.initialize_open_positions(self.con, NOW)
        before = self.con.execute("SELECT cash,total_fees FROM paper_agent_portfolio").fetchone()
        result = manager.process_market_state(self.con, self.live(bid_quote_volume=None), NOW, RULES)
        self.assertEqual(result["reason"], "MISSING_LIQUIDITY")
        self.assertEqual(self.con.execute("SELECT cash,total_fees FROM paper_agent_portfolio").fetchone(), before)
        self.assertEqual(self.con.execute("SELECT remaining_quantity FROM paper_position_management").fetchone()[0], 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM paper_funding_reconciliation_segments").fetchone()[0], 0)

    def test_legacy_partial_liquidity_does_not_close_full_position(self):
        self.open_due()
        with patch.object(agent.execution_market_data, "capture_reference", return_value=quote(available_quote_volume=49.5)):
            self.assertEqual(agent.settle_open(self.con, NOW, self.metadata), 0)
        self.assertEqual(self.con.execute("SELECT status FROM paper_agent_positions").fetchone()[0], "OPEN")
        self.assertGreaterEqual(self.con.execute("SELECT remaining_quantity FROM paper_position_management").fetchone()[0], .5)
        self.assertTrue(self.audit()["partial"])

    def test_short_exit_missing_ask_blocks(self):
        self.open_due("SHORT")
        manager.initialize_open_positions(self.con, NOW)
        result = manager.process_market_state(self.con, self.live(best_ask=None), NOW, RULES)
        self.assertEqual(result["reason"], "MISSING_ASK")
        self.assertIsNone(self.con.execute("SELECT exit_price FROM paper_agent_positions").fetchone()[0])

    def test_liquidation_without_liquidity_does_not_invent_threshold_fill(self):
        self.open_due()
        self.con.execute("UPDATE paper_agent_positions SET market_type='USD_M_PERPETUAL',liquidation_price=80,leverage=5")
        manager.initialize_open_positions(self.con, NOW)
        result = manager.process_market_state(self.con, self.live(
            market_type="USD_M_PERPETUAL", last_price=79, best_bid=78, best_ask=80,
            bid_quote_volume=None), NOW, RULES)
        self.assertEqual(result["reason"], "MISSING_LIQUIDITY")
        self.assertIsNone(self.con.execute("SELECT exit_price FROM paper_agent_positions").fetchone()[0])

    def test_okx_trigger_cannot_fill_when_primary_execution_source_is_unavailable(self):
        self.open_due()
        self.con.execute("UPDATE paper_agent_positions SET market_type='USD_M_PERPETUAL'")
        manager.initialize_open_positions(self.con, NOW)
        with patch.object(agent.execution_market_data, "fetch_executable_quote", side_effect=ValueError("unavailable")):
            result = manager.process_market_state(self.con, self.live(
                market_type="USD_M_PERPETUAL", quote_source="OKX_TICKERS"), NOW, RULES)
        self.assertEqual(result["reason"], "EXECUTABLE_QUOTE_SOURCE_UNAVAILABLE")
        self.assertIsNone(self.con.execute("SELECT exit_price FROM paper_agent_positions").fetchone()[0])

    def test_stale_okx_quote_does_not_trigger_fallback_acquisition(self):
        self.open_due()
        self.con.execute("UPDATE paper_agent_positions SET market_type='USD_M_PERPETUAL'")
        manager.initialize_open_positions(self.con, NOW)
        with patch.object(agent.execution_market_data, "fetch_executable_quote", side_effect=AssertionError("no fallback")):
            result = manager.process_market_state(self.con, self.live(
                market_type="USD_M_PERPETUAL", quote_source="OKX_TICKERS", quote_timestamp_ms=MS-3000), NOW, RULES)
        self.assertEqual(result["reason"], "STALE_QUOTE")

    def test_okx_trigger_executes_only_at_primary_quote_with_base_unit_liquidity(self):
        self.open_due()
        self.con.execute("UPDATE paper_agent_positions SET market_type='USD_M_PERPETUAL'")
        manager.initialize_open_positions(self.con, NOW)
        with patch.object(agent.execution_market_data, "fetch_executable_quote", return_value=quote(
                best_bid=98, best_ask=102, market_type="USD_M_PERPETUAL", source="BINANCE_USD_M_BOOK_TICKER")):
            result = manager.process_market_state(self.con, self.live(
                market_type="USD_M_PERPETUAL", quote_source="OKX_TICKERS"), NOW, RULES)
        self.assertEqual(result["status"], "POSITION_CLOSED")
        self.assertEqual(self.audit()["executable_reference_price"], 98)
        self.assertEqual(self.audit()["source"], "BINANCE_USD_M_BOOK_TICKER")

    def test_nonfinite_rejection_evidence_is_valid_json(self):
        self.assertEqual(self.entry(quote(best_ask=float("nan"))), (0, 1))
        self.assertEqual(self.con.execute("SELECT json_valid(evidence_json) FROM paper_position_events").fetchone()[0], 1)
        self.assertEqual(self.audit()["rejection_reason"], "INVALID_ASK")

    def test_invalid_observation_timestamp_is_audited_without_opening(self):
        self.assertEqual(self.entry(quote(observed_at="bad")), (0, 1))
        self.assertEqual(self.audit()["rejection_reason"], "INVALID_QUOTE_TIMESTAMP")

    def test_short_entry_missing_bid_blocks(self):
        self.con.execute("UPDATE paper_agent_positions SET direction='SHORT',market_type='USD_M_PERPETUAL'")
        self.assertEqual(self.entry(quote(best_bid=None, market_type="USD_M_PERPETUAL")), (0, 1))
        self.assertEqual(self.audit()["rejection_reason"], "MISSING_BID")

    def test_legacy_stale_quote_does_not_close(self):
        self.open_due()
        with patch.object(agent.execution_market_data, "capture_reference", return_value=quote(quote_timestamp_ms=MS-3000)):
            self.assertEqual(agent.settle_open(self.con, NOW, self.metadata), 0)
        self.assertEqual(self.audit()["rejection_reason"], "STALE_QUOTE")
        self.assertEqual(self.con.execute("SELECT status FROM paper_agent_positions").fetchone()[0], "OPEN")
