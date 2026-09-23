"""Autonomous, auditable virtual-money trading agent for AG Crypto Analyst.

The active decision path consumes behavior-core PAPER experiments while Unified
Forecast remains a baseline only. It never sends a real order, charges explicit
costs, and preserves one lifetime portfolio. Open positions are managed by the
real-time PAPER worker; the hourly path is only a legacy fail-closed fallback.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import requests
from database.runtime_store import connect as runtime_connect
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from decision_engine import forecast_engine
from paper_trading.execution_model import (
    PAPER_MARKET, execute_paper, estimate_order_rules, simulated_fill,
    taker_fee_rate,
)
from paper_trading.execution_contract import ExecutionDecision, OrderIntent
from paper_trading.position_management import record_execution
from paper_trading.exchange_metadata import (
    ExchangeMetadataError, load_cache, rules_for,
)
from paper_trading.governance import validate_agent_authority
from paper_trading.futures_costs import (
    directional_funding_payment, fetch_funding_history_via_postgres,
    liquidation_breached, liquidation_price,
)
from paper_trading import trade_learning
from paper_trading import strategy_allocator
from paper_trading import experience_memory
from paper_trading import behavior_decision_core
from paper_trading import execution_market_data
from paper_trading import position_management
from paper_trading import risk_tiering
from analysis import realtime_technical_context
from analysis import technical_trade_plan


DB_NAME = forecast_engine.DB_NAME
AGENT_VERSION = "CRYPTO_PROFIT_AGENT_V1"
POLICY_VERSION = "PAPER_OPPORTUNITY_POLICY_V3_IMMEDIATE_EXECUTION"
INITIAL_CAPITAL = float(os.getenv("AGCA_PAPER_INITIAL_USDT", "10000"))
MAX_TOTAL_EXPOSURE = 0.60
MAX_OPEN_POSITIONS = 6
MAX_NEW_PER_CYCLE = 3
MIN_PAPER_NOTIONAL_USDT = 10.0
MIN_DIRECTION_PROBABILITY = 0.52
MIN_PROBABILITY_GAP = 0.05
TRADE_HORIZONS = (4, 24)
ISTANBUL = ZoneInfo("Europe/Istanbul")
REPORT_PATH = Path("reports/daily_crypto_profit_agent_report.md")
FORWARD_COHORT = "FORWARD_CLEAN_V2_20260825"


def utcnow():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def parse_time(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def display_time(value):
    """Render operator-facing timestamps Istanbul-first; storage stays UTC."""
    parsed = parse_time(value)
    local = parsed.astimezone(ISTANBUL)
    return (
        f"{local:%Y-%m-%d %H:%M:%S} İstanbul "
        f"({parsed:%Y-%m-%d %H:%M:%S} UTC)"
    )


def pending_entry_schedule(con):
    return [{
        "position_id": int(row[0]), "symbol": row[1], "direction": row[2],
        "planned_entry_time": row[3],
        "planned_entry_display": display_time(row[3]),
    } for row in con.execute("""SELECT position_id,symbol,direction,planned_entry_time
        FROM paper_agent_positions WHERE status='PENDING_ENTRY'
        ORDER BY planned_entry_time,position_id""").fetchall()]


def immediate_paper_entry_time(decision_time):
    """Causal PAPER entry target: the committed decision time itself."""
    return parse_time(decision_time)


def connect():
    con = runtime_connect(DB_NAME, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def load_execution_metadata():
    if os.getenv("AGCA_REQUIRE_LIVE_EXCHANGE_RULES", "1") != "1":
        return None
    max_age = float(os.getenv("AGCA_EXCHANGE_METADATA_MAX_AGE_SECONDS", "7200"))
    return {
        "USD_M_PERPETUAL": load_cache(
            os.getenv(
                "AGCA_EXCHANGE_METADATA_CACHE",
                "/var/lib/agca/exchange-info-usdm.json",
            ),
            "USD_M_PERPETUAL", max_age_seconds=max_age,
        ),
        "SPOT": load_cache(
            os.getenv(
                "AGCA_SPOT_EXCHANGE_METADATA_CACHE",
                "/var/lib/agca/exchange-info-spot.json",
            ),
            "SPOT", max_age_seconds=max_age,
        ),
    }


def ensure_tables(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS paper_agent_portfolio (
            portfolio_id INTEGER PRIMARY KEY CHECK(portfolio_id=1),
            created_at TEXT NOT NULL,
            agent_version TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            initial_capital REAL NOT NULL,
            cash REAL NOT NULL,
            realized_pnl REAL NOT NULL DEFAULT 0,
            total_fees REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS paper_agent_positions (
            position_id INTEGER PRIMARY KEY AUTOINCREMENT,
            forecast_id INTEGER NOT NULL UNIQUE,
            agent_version TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            symbol TEXT NOT NULL,
            horizon_hours INTEGER NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('LONG','SHORT')),
            status TEXT NOT NULL CHECK(status IN
                ('PENDING_ENTRY','OPEN','CLOSED','CANCELLED')),
            selected_at TEXT NOT NULL,
            as_of TEXT NOT NULL,
            planned_entry_time TEXT NOT NULL,
            due_time TEXT NOT NULL,
            entry_time TEXT,
            entry_price REAL,
            quantity REAL,
            notional REAL NOT NULL,
            entry_fee REAL NOT NULL DEFAULT 0,
            exit_time TEXT,
            exit_price REAL,
            exit_fee REAL NOT NULL DEFAULT 0,
            gross_pnl REAL,
            net_pnl REAL,
            confidence REAL NOT NULL,
            p_up REAL NOT NULL,
            p_down REAL NOT NULL,
            target_price REAL NOT NULL,
            model_version TEXT NOT NULL,
            artifact_hash TEXT NOT NULL,
            selection_reason TEXT NOT NULL,
            cancel_reason TEXT,
            market_type TEXT NOT NULL DEFAULT 'USD_M_PERPETUAL',
            entry_spread_cost REAL NOT NULL DEFAULT 0,
            entry_slippage_cost REAL NOT NULL DEFAULT 0,
            exit_spread_cost REAL NOT NULL DEFAULT 0,
            exit_slippage_cost REAL NOT NULL DEFAULT 0,
            funding_cost REAL NOT NULL DEFAULT 0,
            partial_fill INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_paper_positions_status_due
        ON paper_agent_positions(status,due_time);
        CREATE INDEX IF NOT EXISTS idx_paper_positions_symbol_status
        ON paper_agent_positions(symbol,status);

        CREATE TABLE IF NOT EXISTS paper_agent_daily_reports (
            report_date TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            window_start TEXT NOT NULL,
            window_end TEXT NOT NULL,
            report_json TEXT NOT NULL,
            telegram_sent INTEGER NOT NULL DEFAULT 0,
            remote_mirror_status TEXT NOT NULL DEFAULT 'PENDING',
            remote_mirrored_at TEXT,
            delivery_complete INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS paper_behavior_selection_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            core_status TEXT NOT NULL,
            candidate_count INTEGER NOT NULL,
            selected_count INTEGER NOT NULL,
            active_position_count INTEGER NOT NULL,
            capacity INTEGER NOT NULL,
            available_exposure REAL NOT NULL,
            reason_counts_json TEXT NOT NULL,
            real_order_allowed INTEGER NOT NULL DEFAULT 0
        );
    """)
    columns = {row[1] for row in con.execute("PRAGMA table_info(paper_agent_daily_reports)")}
    for name, definition in (
        ("remote_mirror_status", "TEXT NOT NULL DEFAULT 'PENDING'"),
        ("remote_mirrored_at", "TEXT"),
        ("delivery_complete", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in columns:
            con.execute(f"ALTER TABLE paper_agent_daily_reports ADD COLUMN {name} {definition}")
    position_columns = {row[1] for row in con.execute("PRAGMA table_info(paper_agent_positions)")}
    for name, definition in (
        ("market_type", "TEXT NOT NULL DEFAULT 'USD_M_PERPETUAL'"),
        ("entry_spread_cost", "REAL NOT NULL DEFAULT 0"),
        ("entry_slippage_cost", "REAL NOT NULL DEFAULT 0"),
        ("exit_spread_cost", "REAL NOT NULL DEFAULT 0"),
        ("exit_slippage_cost", "REAL NOT NULL DEFAULT 0"),
        ("funding_cost", "REAL NOT NULL DEFAULT 0"),
        ("funding_event_count", "INTEGER NOT NULL DEFAULT 0"),
        ("funding_source", "TEXT"),
        ("liquidation_price", "REAL"),
        ("liquidation_fee", "REAL NOT NULL DEFAULT 0"),
        ("liquidated", "INTEGER NOT NULL DEFAULT 0"),
        ("close_reason", "TEXT"),
        ("validation_cohort", "TEXT"),
        ("evidence_eligible", "INTEGER NOT NULL DEFAULT 0"),
        ("partial_fill", "INTEGER NOT NULL DEFAULT 0"),
        ("strategy_id", "TEXT NOT NULL DEFAULT 'CHAMPION_BASELINE'"),
        ("decision_source", "TEXT NOT NULL DEFAULT 'UNIFIED_FORECAST_BASELINE'"),
        ("decision_reference_id", "INTEGER"),
        ("leverage", "REAL NOT NULL DEFAULT 1"),
        ("margin_allocated", "REAL"),
        ("risk_tier", "TEXT NOT NULL DEFAULT 'PAPER_RISK_TIER_1'"),
        ("independent_signal_count", "INTEGER NOT NULL DEFAULT 1"),
        ("signal_sources_json", "TEXT NOT NULL DEFAULT '[\"LEGACY\"]'"),
        ("technical_basis_ratio", "REAL"),
    ):
        if name not in position_columns:
            con.execute(f"ALTER TABLE paper_agent_positions ADD COLUMN {name} {definition}")
    # Legacy rows may predate explicit margin accounting.  Backfill only
    # missing values and respect the leverage recorded on that exact position;
    # never rewrite already-accounted historical rows.
    con.execute("""UPDATE paper_agent_positions
        SET margin_allocated=notional / CASE WHEN leverage>0 THEN leverage ELSE 1 END
        WHERE margin_allocated IS NULL""")
    portfolio_columns = {row[1] for row in con.execute("PRAGMA table_info(paper_agent_portfolio)")}
    for name, definition in (
        ("validation_cohort", "TEXT"),
        ("evidence_start_at", "TEXT"),
    ):
        if name not in portfolio_columns:
            con.execute(f"ALTER TABLE paper_agent_portfolio ADD COLUMN {name} {definition}")
    if PAPER_MARKET == "USD_M_PERPETUAL":
        maintenance_rate = float(os.getenv("AGCA_PAPER_MAINTENANCE_MARGIN_RATE", "0.005"))
        rows = con.execute("""
            SELECT position_id,entry_price,direction,leverage FROM paper_agent_positions
            WHERE status='OPEN' AND market_type='USD_M_PERPETUAL'
              AND liquidation_price IS NULL AND entry_price IS NOT NULL
        """).fetchall()
        for position_id, entry_price, direction, leverage in rows:
            con.execute(
                "UPDATE paper_agent_positions SET liquidation_price=? WHERE position_id=?",
                (liquidation_price(
                    entry_price=float(entry_price), direction=direction, leverage=float(leverage),
                    maintenance_margin_rate=maintenance_rate,
                ), position_id),
            )
    con.execute("""
        INSERT OR IGNORE INTO paper_agent_portfolio(
            portfolio_id,created_at,agent_version,policy_version,
            initial_capital,cash,realized_pnl,total_fees
        ) VALUES(1,?,?,?,?,?,?,?)
    """, (
        iso(utcnow()), AGENT_VERSION, POLICY_VERSION,
        INITIAL_CAPITAL, INITIAL_CAPITAL, 0.0, 0.0,
    ))
    current_cohort = con.execute(
        "SELECT validation_cohort FROM paper_agent_portfolio WHERE portfolio_id=1"
    ).fetchone()[0]
    if current_cohort != FORWARD_COHORT:
        reset_at = iso(utcnow())
        con.execute("""
            UPDATE paper_agent_positions SET
                evidence_eligible=0,
                validation_cohort=COALESCE(validation_cohort,'LEGACY_LOOKAHEAD_EXCLUDED'),
                status=CASE WHEN status IN ('PENDING_ENTRY','OPEN') THEN 'CANCELLED' ELSE status END,
                cancel_reason=CASE WHEN status IN ('PENDING_ENTRY','OPEN')
                    THEN 'LOOKAHEAD_COHORT_QUARANTINED' ELSE cancel_reason END
        """)
        con.execute("""
            UPDATE paper_agent_portfolio SET created_at=?,initial_capital=?,cash=?,
                realized_pnl=0,total_fees=0,validation_cohort=?,evidence_start_at=?
            WHERE portfolio_id=1
        """, (reset_at, INITIAL_CAPITAL, INITIAL_CAPITAL, FORWARD_COHORT, reset_at))
    # Cohort reset is normally one-time, but legacy active rows can survive or
    # be restored after the portfolio has already moved to FORWARD_COHORT.
    # They must never remain actionable PAPER positions.  Quarantine them
    # idempotently without manufacturing an execution/PnL event.
    legacy_active = con.execute("""
        SELECT 1 FROM paper_agent_positions
        WHERE status IN ('PENDING_ENTRY','OPEN')
          AND (validation_cohort IS NULL OR validation_cohort<>?
               OR COALESCE(evidence_eligible,0)<>1)
        LIMIT 1
    """, (FORWARD_COHORT,)).fetchone()
    if legacy_active:
        con.execute("""
            UPDATE paper_agent_positions SET
                evidence_eligible=0,
                validation_cohort=COALESCE(validation_cohort,'LEGACY_LOOKAHEAD_EXCLUDED'),
                status='CANCELLED',
                cancel_reason=COALESCE(cancel_reason,'LOOKAHEAD_COHORT_QUARANTINED')
            WHERE status IN ('PENDING_ENTRY','OPEN')
              AND (validation_cohort IS NULL OR validation_cohort<>?
                   OR COALESCE(evidence_eligible,0)<>1)
        """, (FORWARD_COHORT,))
    experience_memory.ensure_tables(con)
    experience_memory.record_legacy_summary(con)
    behavior_decision_core.ensure_tables(con)
    execution_market_data.ensure_table(con)
    position_management.ensure_tables(con)
    con.commit()


def exact_open(con, symbol, as_of):
    expected = parse_time(as_of) + timedelta(milliseconds=1)
    rows = con.execute("""
        SELECT open_time,open,quote_volume FROM market_klines
        WHERE symbol=? AND interval='1h'
          AND open_time>=? AND open_time<=?
        ORDER BY open_time LIMIT 3
    """, (
        symbol,
        iso(expected - timedelta(seconds=2)),
        iso(expected + timedelta(seconds=2)),
    )).fetchall()
    for stamp, price, quote_volume in rows:
        if abs((parse_time(stamp) - expected).total_seconds()) <= 1.0:
            return parse_time(stamp), float(price), (float(quote_volume) if quote_volume else None)
    return None


def exact_planned_open(con, symbol, planned_entry_time):
    """Resolve only the open committed before that timestamp; never a prior price."""
    expected = parse_time(planned_entry_time)
    rows = con.execute("""
        SELECT open_time,open,quote_volume FROM market_klines
        WHERE symbol=? AND interval='1h' AND open_time>=? AND open_time<=?
        ORDER BY open_time LIMIT 3
    """, (
        symbol, iso(expected - timedelta(seconds=1)), iso(expected + timedelta(seconds=1)),
    )).fetchall()
    for stamp, price, quote_volume in rows:
        if abs((parse_time(stamp) - expected).total_seconds()) <= 1.0:
            return parse_time(stamp), float(price), (float(quote_volume) if quote_volume else None)
    return None


def exact_close(con, symbol, due_time):
    expected = parse_time(due_time)
    rows = con.execute("""
        SELECT close_time,close,quote_volume FROM market_klines
        WHERE symbol=? AND interval='1h'
          AND close_time>=? AND close_time<=?
        ORDER BY close_time LIMIT 3
    """, (
        symbol,
        iso(expected - timedelta(seconds=2)),
        iso(expected + timedelta(seconds=2)),
    )).fetchall()
    for stamp, price, quote_volume in rows:
        if abs((parse_time(stamp) - expected).total_seconds()) <= 1.0:
            return parse_time(stamp), float(price), (float(quote_volume) if quote_volume else None)
    return None


def directional_pnl(direction, entry_price, exit_price, quantity):
    if direction == "LONG":
        return (exit_price - entry_price) * quantity
    if direction == "SHORT":
        return (entry_price - exit_price) * quantity
    raise ValueError("UNKNOWN_DIRECTION")


def first_liquidation_event(con, symbol, entry_time, due_time, direction, threshold):
    rows = con.execute("""
        SELECT close_time,low,high FROM market_klines
        WHERE symbol=? AND interval='1h' AND close_time>? AND close_time<=?
        ORDER BY close_time
    """, (symbol, iso(parse_time(entry_time)), iso(parse_time(due_time)))).fetchall()
    for close_time, low, high in rows:
        if liquidation_breached(
            direction=direction, threshold=threshold,
            low=float(low), high=float(high),
        ):
            return parse_time(close_time)
    return None


MAX_PORTFOLIO_MARK_AGE_SECONDS = float(
    os.getenv("AGCA_PORTFOLIO_MARK_MAX_AGE_SECONDS", "3900")
)


def latest_mark(con, symbol, now=None):
    """Return a closed-candle mark only when it is fresh enough for risk sizing."""
    row = con.execute("""
        SELECT close,close_time FROM market_klines
        WHERE symbol=? AND interval='1h'
        ORDER BY close_time DESC LIMIT 1
    """, (symbol,)).fetchone()
    if not row:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        # Binance market_klines.close_time is Unix milliseconds, not ISO text.
        raw_close_time = row[1]
        if isinstance(raw_close_time, (int, float)) or (
            isinstance(raw_close_time, str) and raw_close_time.isdecimal()
        ):
            timestamp = float(raw_close_time)
            mark_time = datetime.fromtimestamp(
                timestamp / 1000.0 if timestamp > 1e11 else timestamp,
                timezone.utc,
            )
        else:
            mark_time = parse_time(raw_close_time)
        age = (now - mark_time).total_seconds()
    except (TypeError, ValueError):
        return None
    if age < -60.0 or age > MAX_PORTFOLIO_MARK_AGE_SECONDS:
        return None
    mark = float(row[0])
    return (mark, mark_time) if mark > 0.0 else None


def portfolio_snapshot(con, now=None):
    portfolio = con.execute("""
        SELECT initial_capital,cash,realized_pnl,total_fees,
               validation_cohort,evidence_start_at
        FROM paper_agent_portfolio WHERE portfolio_id=1
    """).fetchone()
    open_rows = con.execute("""
        SELECT p.symbol,p.direction,p.entry_price,
               COALESCE(m.remaining_quantity,p.quantity),
               COALESCE(m.remaining_cost_basis,p.margin_allocated)
        FROM paper_agent_positions p
        LEFT JOIN paper_position_management m USING(position_id)
        WHERE p.status='OPEN'
    """).fetchall()
    reserved = 0.0
    unrealized = 0.0
    gross_exposure = 0.0
    valuation_complete = True
    missing_marks = []
    for symbol, direction, entry, quantity, margin_basis in open_rows:
        # Cash is debited by allocated margin at entry, not gross leveraged
        # notional. Equity therefore adds back only the remaining margin basis.
        # Treating margin as gross exposure would manufacture equity under
        # leverage and distort all downstream sizing.
        reserved += float(margin_basis)
        mark_state = latest_mark(con, symbol, now)
        if mark_state is None:
            valuation_complete = False
            missing_marks.append(symbol)
            continue
        mark, _ = mark_state
        qty = float(quantity)
        unrealized += directional_pnl(direction, float(entry), mark, qty)
        gross_exposure += abs(qty * mark)
    initial, cash, realized, fees = map(float, portfolio[:4])
    equity = cash + reserved + unrealized
    return {
        "initial_capital": initial,
        "cash": cash,
        "reserved": reserved,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "total_fees": fees,
        "equity": equity,
        "gross_exposure": gross_exposure,
        "valuation_complete": valuation_complete,
        "missing_marks": tuple(missing_marks),
        "return_pct": (equity / initial - 1.0) * 100.0,
        "open_positions": len(open_rows),
        "validation_cohort": portfolio[4],
        "evidence_start_at": portfolio[5],
    }


def cancel_expired_pending_entries(con, now):
    """Cancel stale PAPER entry intents without waiting for a market callback."""
    rows = con.execute("""
        SELECT position_id,symbol,planned_entry_time,direction,market_type
        FROM paper_agent_positions
        WHERE status='PENDING_ENTRY'
        ORDER BY planned_entry_time,position_id
    """).fetchall()
    cancelled = 0
    for position_id, symbol, planned_entry_time, direction, market_type in rows:
        try:
            planned = parse_time(planned_entry_time)
        except (TypeError, ValueError):
            continue
        if now - planned <= execution_market_data.MAX_ENTRY_OBSERVATION_DELAY:
            continue
        record_execution(
            con, position_id, now, "ENTRY", symbol,
            "BUY" if direction == "LONG" else "SELL", market_type,
            ExecutionDecision("REJECTED", "ENTRY_OBSERVATION_WINDOW_EXPIRED"),
        )
        updated = con.execute("""
            UPDATE paper_agent_positions
            SET status='CANCELLED',cancel_reason=?
            WHERE position_id=? AND status='PENDING_ENTRY'
        """, (f"EXACT_{market_type}_ENTRY_CAPTURE_EXPIRED", position_id))
        if updated.rowcount:
            cancelled += 1
    if cancelled:
        con.commit()
    return cancelled


def fill_pending(con, now, exchange_metadata=None):
    cancelled = cancel_expired_pending_entries(con, now)
    rows = con.execute("""
        SELECT position_id,symbol,planned_entry_time,notional,direction,leverage,
               market_type,decision_source
        FROM paper_agent_positions
        WHERE status='PENDING_ENTRY'
        ORDER BY selected_at,position_id
    """).fetchall()
    filled = 0
    for (position_id, symbol, planned_entry_time, notional, direction, leverage,
         market_type, decision_source) in rows:
        # Behavior candidates require the frozen technical entry zone and must
        # only fill from the realtime authoritative state callback.
        if decision_source == "MARKET_BEHAVIOR_CORE":
            continue
        planned = parse_time(planned_entry_time)
        metadata = (
            exchange_metadata.get(market_type)
            if isinstance(exchange_metadata, dict) else exchange_metadata
        )
        try:
            rules = rules_for(metadata, symbol) if metadata else None
        except ExchangeMetadataError:
            rules = None
        if rules is None:
            record_execution(con, position_id, now, "ENTRY", symbol,
                             "BUY" if direction == "LONG" else "SELL", market_type,
                             ExecutionDecision("REJECTED", "EXCHANGE_RULES_UNAVAILABLE"))
            con.execute("""
                UPDATE paper_agent_positions SET status='CANCELLED',cancel_reason=?
                WHERE position_id=? AND status='PENDING_ENTRY'
            """, (f"LIVE_{market_type}_SYMBOL_RULES_UNAVAILABLE", position_id))
            cancelled += 1
            continue
        result = None
        if now >= planned:
            try:
                result = execution_market_data.capture_reference(
                    con, position_id=position_id, symbol=symbol,
                    expected=planned, purpose="ENTRY", observed_at=now,
                    market_type=market_type,
                    side="BUY" if direction == "LONG" else "SELL",
                )
            except execution_market_data.SpotManagementVenueUnavailable:
                con.execute("""
                    UPDATE paper_agent_positions SET status='CANCELLED',cancel_reason=?
                    WHERE position_id=? AND status='PENDING_ENTRY'
                """, ("OKX_SPOT_MANAGEMENT_UNAVAILABLE", position_id))
                cancelled += 1
                continue
            except (requests.RequestException, ValueError) as exc:
                result = None
                record_execution(con, position_id, now, "ENTRY", symbol,
                                 "BUY" if direction == "LONG" else "SELL", market_type,
                                 ExecutionDecision("REJECTED", "EXECUTABLE_QUOTE_UNAVAILABLE",
                                                   evidence={"error_type": type(exc).__name__}))
        if result is None:
            if now >= planned:
                record_execution(con, position_id, now, "ENTRY", symbol,
                                 "BUY" if direction == "LONG" else "SELL", market_type,
                                 ExecutionDecision("REJECTED", "EXECUTABLE_QUOTE_UNAVAILABLE"))
            if now - planned > execution_market_data.MAX_ENTRY_OBSERVATION_DELAY:
                con.execute("""
                    UPDATE paper_agent_positions
                    SET status='CANCELLED',cancel_reason=?
                    WHERE position_id=? AND status='PENDING_ENTRY'
                """, (
                    f"EXACT_{market_type}_ENTRY_CAPTURE_UNAVAILABLE",
                    position_id,
                ))
                cancelled += 1
            continue
        notional = float(notional)
        side = "BUY" if direction == "LONG" else "SELL"
        execution = execute_paper(OrderIntent(symbol, side, notional), result, rules)
        fill = execution.fill
        try:
            entry_time = parse_time(result.observed_at) if result.observed_at else now
        except (TypeError, ValueError):
            entry_time = now
        if result.market_type != market_type:
            execution = ExecutionDecision("REJECTED", "QUOTE_MARKET_MISMATCH")
            fill = None
        if fill is None:
            record_execution(con, position_id, entry_time, "ENTRY", symbol, side,
                             market_type, execution)
            con.execute("""
                UPDATE paper_agent_positions SET status='CANCELLED',cancel_reason=?
                WHERE position_id=? AND status='PENDING_ENTRY'
            """, (execution.reason, position_id))
            cancelled += 1
            continue
        entry_price, fee = fill.price, fill.fee
        actual_notional = fill.quantity * fill.price
        margin = actual_notional / float(leverage)
        cash = float(con.execute(
            "SELECT cash FROM paper_agent_portfolio WHERE portfolio_id=1"
        ).fetchone()[0])
        if cash + 1e-9 < margin + fee:
            record_execution(con, position_id, entry_time, "ENTRY", symbol, side,
                             market_type, ExecutionDecision("REJECTED", "INSUFFICIENT_VIRTUAL_CASH"))
            con.execute("""
                UPDATE paper_agent_positions
                SET status='CANCELLED',cancel_reason=?
                WHERE position_id=? AND status='PENDING_ENTRY'
            """, ("INSUFFICIENT_VIRTUAL_CASH", position_id))
            cancelled += 1
            continue
        quantity = fill.quantity
        maintenance_rate = float(os.getenv("AGCA_PAPER_MAINTENANCE_MARGIN_RATE", "0.005"))
        liquidation_threshold = liquidation_price(
            entry_price=entry_price, direction=direction, leverage=float(leverage),
            maintenance_margin_rate=maintenance_rate,
        ) if market_type == "USD_M_PERPETUAL" else None
        con.execute("""
            UPDATE paper_agent_positions SET
                status='OPEN',entry_time=?,entry_price=?,quantity=?,notional=?,entry_fee=?,
                market_type=?,entry_spread_cost=?,entry_slippage_cost=?,partial_fill=?,
                liquidation_price=?,margin_allocated=?
            WHERE position_id=? AND status='PENDING_ENTRY'
        """, (iso(entry_time), entry_price, quantity, actual_notional, fee,
              market_type, fill.spread_cost, fill.slippage_cost,
              int(fill.partial), liquidation_threshold, margin, position_id))
        con.execute("""
            UPDATE paper_agent_portfolio
            SET cash=cash-?,total_fees=total_fees+?
            WHERE portfolio_id=1
        """, (margin + fee, fee))
        record_execution(con, position_id, entry_time, "ENTRY", symbol, side,
                         market_type, execution)
        filled += 1
    con.commit()
    return filled, cancelled


def settle_open(con, now, exchange_metadata=None):
    """Due legacy positions use the same bounded executable exit/accounting path.

    No historical candle or liquidation threshold can act as a fill. Partial
    exits remain under the existing position manager for subsequent fresh quotes.
    """
    from paper_trading.realtime_market import LiveState
    from paper_trading.position_management import initialize_open_positions, process_market_state
    from paper_trading.execution_model import validate_quote

    rows = con.execute("""
        SELECT p.position_id,p.symbol,p.direction,p.due_time,p.market_type
        FROM paper_agent_positions p
        LEFT JOIN paper_position_management m USING(position_id)
        WHERE p.status='OPEN' AND p.due_time<=? AND m.position_id IS NULL
        ORDER BY due_time,position_id
    """, (iso(now),)).fetchall()
    closed = 0
    for position_id, symbol, direction, due, market_type in rows:
        side = "SELL" if direction == "LONG" else "BUY"
        metadata = exchange_metadata.get(market_type) if isinstance(exchange_metadata, dict) else exchange_metadata
        try:
            rules = rules_for(metadata, symbol) if metadata else None
        except ExchangeMetadataError:
            rules = None
        reason = "EXCHANGE_RULES_UNAVAILABLE" if rules is None else None
        quote = None
        if reason is None:
            try:
                quote = execution_market_data.capture_reference(
                    con, position_id=position_id, symbol=symbol,
                    expected=parse_time(due), purpose="EXIT", observed_at=now,
                    market_type=market_type, side=side,
                )
                reference, reason = validate_quote(quote, side) if quote else (None, "EXECUTABLE_QUOTE_UNAVAILABLE")
            except (requests.RequestException, ValueError):
                reason = "EXECUTABLE_QUOTE_UNAVAILABLE"
        if reason:
            record_execution(con, position_id, now, "EXIT", symbol, side, market_type,
                             ExecutionDecision("REJECTED", reason))
            continue
        initialize_open_positions(con, now)
        live = LiveState(
            symbol=symbol, market_type=market_type,
            last_price=(quote.best_bid + quote.best_ask) / 2,
            best_bid=quote.best_bid, best_ask=quote.best_ask,
            market_timestamp_ms=quote.quote_timestamp_ms,
            quote_timestamp_ms=quote.quote_timestamp_ms,
            liquidity_timestamp_ms=quote.liquidity_timestamp_ms,
            quote_source=quote.source, visible_quote_volume=quote.available_quote_volume,
            bid_quote_volume=quote.available_quote_volume if side == "SELL" else None,
            ask_quote_volume=quote.available_quote_volume if side == "BUY" else None,
        )
        result = process_market_state(con, live, parse_time(quote.observed_at), rules)
        closed += int(result.get("status") == "POSITION_CLOSED")
    con.commit()
    return closed


def candidate_direction(row):
    p_up = float(row["p_up"])
    p_down = float(row["p_down"])
    current = float(row["current_price"])
    target = float(row["target_price"])
    if p_up >= p_down:
        direction = "LONG"
        confidence = p_up
        aligned = target > current
    else:
        if PAPER_MARKET == "SPOT":
            return None
        direction = "SHORT"
        confidence = p_down
        aligned = target < current
    gap = abs(p_up - p_down)
    if confidence < MIN_DIRECTION_PROBABILITY:
        return None
    if gap < MIN_PROBABILITY_GAP or not aligned:
        return None
    return direction, confidence, gap


def latest_candidates(con, now=None):
    now = now or utcnow()
    latest = con.execute(
        "SELECT MAX(as_of) FROM forecast_engine_predictions"
    ).fetchone()[0]
    if not latest:
        return []
    rows = con.execute("""
        SELECT forecast_id,symbol,horizon_hours,as_of,due_time,current_price,
               target_price,p_up,p_down,p_neutral,risk_eligible,risk_cap_pct,
               model_version,artifact_hash
        FROM forecast_engine_predictions
        WHERE as_of=? AND horizon_hours IN (4,24)
        ORDER BY symbol,horizon_hours
    """, (latest,)).fetchall()
    keys = (
        "forecast_id","symbol","horizon","as_of","due_time","current_price",
        "target_price","p_up","p_down","p_neutral","risk_eligible",
        "risk_cap_pct","model_version","artifact_hash",
    )
    candidates = []
    for raw in rows:
        row = dict(zip(keys, raw))
        if parse_time(row["as_of"]) > now or parse_time(row["due_time"]) <= now:
            continue
        if not bool(row["risk_eligible"]):
            continue
        decision = candidate_direction(row)
        if decision is None:
            continue
        direction, confidence, gap = decision
        predicted_move = abs(
            (float(row["target_price"]) / float(row["current_price"]) - 1.0)
            * 100.0
        )
        row.update({
            "direction": direction,
            "confidence": confidence,
            "gap": gap,
            "score": confidence + min(predicted_move / 100.0, 0.10),
            "predicted_move_pct": predicted_move,
        })
        candidates.append(row)
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def _validated_stop_geometry(row):
    """Return risk-snapshot stop geometry or fail closed on missing/invalid evidence."""
    try:
        raw_stop_pct = float(row["stop_reference_pct"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(raw_stop_pct) or raw_stop_pct <= 0:
        return None
    return raw_stop_pct, min(5.0, max(0.5, raw_stop_pct))


def _setup_fingerprint(symbol, direction, evidence):
    """Stable setup identity that ignores minute/event ids but keeps material evidence."""
    evidence = evidence or {}
    learned = evidence.get("learned_pattern") or {}
    event = evidence.get("event") or {}
    sources = set()
    primary = str(evidence.get("primary_signal_source") or "").upper()
    if primary:
        sources.add(primary)
    for signal in evidence.get("directional_signals") or []:
        if not isinstance(signal, dict):
            continue
        source = str(signal.get("source") or "").upper()
        signal_direction = str(signal.get("direction") or "").upper()
        if source and signal_direction == str(direction).upper():
            sources.add(source)
    pattern_key = str(learned.get("flow_pattern") or "")
    event_kind = str(event.get("kind") or "")
    hypothesis_scope = str(evidence.get("hypothesis_scope") or "")
    if not pattern_key or not event_kind:
        return None
    return (
        str(symbol).upper(), str(direction).upper(), primary,
        hypothesis_scope, event_kind, pattern_key, tuple(sorted(sources)),
    )


def _adverse_same_setup_reentry_blocked(con, row):
    """Block churn after an adverse exit until the setup materially changes or resets 1R."""
    current_fingerprint = _setup_fingerprint(
        row.get("symbol"), row.get("direction"), row.get("evidence")
    )
    if current_fingerprint is None:
        return False
    previous = con.execute("""
        SELECT p.exit_price,p.close_reason,t.thesis_json
        FROM paper_agent_positions p
        JOIN paper_agent_decision_theses t USING(position_id)
        WHERE p.symbol=? AND p.direction=? AND p.status='CLOSED'
        ORDER BY p.exit_time DESC,p.position_id DESC LIMIT 1
    """, (row["symbol"], row["direction"])).fetchone()
    if not previous:
        return False
    exit_price, close_reason, thesis_json = previous
    reason = str(close_reason or "")
    adverse_exit = (
        reason in {"PROTECTIVE_STOP", "THESIS_INVALIDATED"}
        or reason.endswith("_LIQUIDATION")
    )
    if not adverse_exit:
        return False
    try:
        thesis = json.loads(thesis_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    previous_fingerprint = _setup_fingerprint(
        thesis.get("symbol"), thesis.get("direction"), thesis.get("evidence_used")
    )
    if previous_fingerprint != current_fingerprint:
        return False
    stop_geometry = _validated_stop_geometry(row)
    try:
        current_price = float(row["current_price"])
        previous_exit_price = float(exit_price)
    except (KeyError, TypeError, ValueError):
        return True
    if (
        stop_geometry is None
        or not math.isfinite(current_price) or current_price <= 0
        or not math.isfinite(previous_exit_price) or previous_exit_price <= 0
    ):
        return True
    _, effective_stop_pct = stop_geometry
    reset_move_pct = abs(current_price / previous_exit_price - 1.0) * 100.0
    return reset_move_pct < effective_stop_pct


def select_new_positions(con, now, exchange_metadata=None, telemetry=None):
    behavior_result = behavior_decision_core.evaluate(con, now)
    snapshot = portfolio_snapshot(con, now)
    active = con.execute("""
        SELECT symbol FROM paper_agent_positions
        WHERE status IN ('PENDING_ENTRY','OPEN')
    """).fetchall()
    active_symbols = {row[0] for row in active}
    capacity = max(0, MAX_OPEN_POSITIONS - len(active))
    reasons = {}
    def reject(reason):
        reasons[reason] = reasons.get(reason, 0) + 1

    def record_run(selected_count, available_exposure):
        con.execute("""INSERT INTO paper_behavior_selection_runs(
            created_at,core_status,candidate_count,selected_count,
            active_position_count,capacity,available_exposure,
            reason_counts_json,real_order_allowed
        ) VALUES(?,?,?,?,?,?,?,?,0)""", (
            iso(now), behavior_result["status"], len(behavior_result["candidates"]),
            selected_count, len(active), capacity, available_exposure,
            json.dumps(reasons, sort_keys=True),
        ))

    if not snapshot["valuation_complete"]:
        reject("PORTFOLIO_VALUATION_STALE_OR_MISSING")
        record_run(0, 0.0)
        con.commit()
        if telemetry is not None:
            telemetry.update({"selected_count": 0, "candidates": [],
                              "core_status": behavior_result["status"],
                              "rejection_reasons": dict(reasons)})
        return 0
    portfolio_full = capacity <= 0
    if portfolio_full:
        reject("PORTFOLIO_POSITION_CAPACITY_FULL")
    max_new = min(MAX_NEW_PER_CYCLE, capacity)
    max_exposure = snapshot["equity"] * MAX_TOTAL_EXPOSURE
    # Fresh marked exposure comes from the same valuation used for equity.
    # Entry price is historical accounting data and must not size new risk.
    open_gross_exposure = float(snapshot["gross_exposure"])
    pending_rows = con.execute("""
        SELECT notional,leverage,market_type
        FROM paper_agent_positions
        WHERE status='PENDING_ENTRY'
    """).fetchall()
    pending_gross_exposure = sum(float(row[0] or 0.0) for row in pending_rows)
    pending_margin = sum(
        float(row[0] or 0.0) / max(float(row[1] or 1.0), 1e-9)
        for row in pending_rows
    )
    pending_entry_fees = sum(
        float(row[0] or 0.0) * taker_fee_rate(row[2])
        for row in pending_rows
    )
    available_cash = max(
        0.0, snapshot["cash"] - pending_margin - pending_entry_fees
    )
    available_exposure = max(
        0.0, max_exposure - open_gross_exposure - pending_gross_exposure
    )
    selected = 0
    planned_entry = immediate_paper_entry_time(now)

    # Rank by expected portfolio contribution, not percentage edge alone.
    # The executable size estimate uses the same risk/leverage/cash constraints
    # as selection, so a tiny high-% setup cannot crowd out a larger opportunity
    # expected to earn more net USDT.  Percentage edge remains a tie-breaker.
    ranked_candidates = []
    for row in behavior_result["candidates"]:
        tier = risk_tiering.tier_for(row)
        try:
            plan = technical_trade_plan.build_for_candidate(
                con, row, now=now, market_type=tier.market_type
            )
        except Exception:
            reject("TECHNICAL_PLAN_UNAVAILABLE")
            continue
        if not bool(plan["risk"]["risk_compatible"]):
            reject("TECHNICAL_RISK_GEOMETRY_INFEASIBLE")
            continue
        row["technical_plan"] = plan
        row["technical_context"] = realtime_technical_context.load_latest(
            con, row["symbol"], now=now
        )
        row["stop_reference_pct"] = float(plan["risk"]["stop_distance_pct"])
        technical_multiplier = float(plan["quality_multiplier"])
        tier = risk_tiering.tier_for(row)
        fee_rate = taker_fee_rate(tier.market_type)
        base_margin = (
            snapshot["equity"] * tier.margin_fraction
            * float(row.get("execution_size_multiplier", 1.0))
            * technical_multiplier
        )
        position_cap = snapshot["equity"] * row["risk_cap_pct"] / 100.0
        stop_geometry = _validated_stop_geometry(row)
        if stop_geometry is None:
            reject("TECHNICAL_STOP_GEOMETRY_INVALID")
            continue
        raw_stop_pct, effective_stop_pct = stop_geometry
        trade_risk_budget = (
            snapshot["equity"] * float(row.get("risk_per_trade_pct") or 0.0) / 100.0
        )
        risk_sized_notional = (
            trade_risk_budget / (effective_stop_pct / 100.0 + 2.0 * fee_rate)
            if trade_risk_budget > 0.0 else 0.0
        )
        sizing_cap = min(
            base_margin * tier.leverage,
            max_exposure,
            position_cap,
            risk_sized_notional,
            available_cash * tier.leverage / (1.0 + fee_rate * tier.leverage),
        )
        expected_profit_usdt = (
            max(0.0, sizing_cap)
            * max(0.0, float(row["expected_net_edge_pct"])) / 100.0
        )
        # Rank against this trade's own stop geometry, not a fixed risk
        # percentage.  The stop reference is produced from the fresh asset-risk
        # snapshot and Position Manager clamps the same value to 0.5%-5.0%.
        stop_loss_usdt = max(0.0, sizing_cap) * effective_stop_pct / 100.0
        estimated_round_trip_cost_usdt = max(0.0, sizing_cap) * (
            2.0 * fee_rate
        )
        expected_loss_budget_usdt = stop_loss_usdt + estimated_round_trip_cost_usdt
        expected_profit_per_risk = (
            expected_profit_usdt / expected_loss_budget_usdt
            if expected_loss_budget_usdt > 0.0 else 0.0
        )
        ranked_candidates.append(
            (expected_profit_per_risk, expected_profit_usdt, row)
        )
    ranked_candidates.sort(
        key=lambda item: (
            item[0],
            item[1],
            float(item[2]["expected_net_edge_pct"]),
            float((item[2].get("technical_plan") or {}).get("technical_score") or 0.0),
            float(item[2]["confidence"]),
        ),
        reverse=True,
    )
    open_position_xray = []
    for position_id, symbol, direction, entry_price, quantity, notional, selection_reason in con.execute(
        """SELECT position_id,symbol,direction,entry_price,quantity,notional,
                  selection_reason
           FROM paper_agent_positions WHERE status='OPEN'
           ORDER BY position_id"""
    ).fetchall():
        mark_state = latest_mark(con, symbol, now)
        mark = mark_state[0] if mark_state else None
        unrealized = (
            directional_pnl(direction, float(entry_price), float(mark), float(quantity))
            if mark is not None and entry_price is not None and quantity is not None
            else None
        )
        open_position_xray.append({
            "position_id": position_id, "symbol": symbol, "direction": direction,
            "entry_price": entry_price, "mark_price": mark, "notional": notional,
            "unrealized_pnl": unrealized, "selection_reason": selection_reason,
        })

    strongest_open = None
    for pos in open_position_xray:
        basis = float(pos.get("notional") or 0.0)
        pnl = pos.get("unrealized_pnl")
        pnl_pct = (100.0 * float(pnl) / basis) if pnl is not None and basis > 0 else None
        pos["unrealized_pnl_pct"] = pnl_pct
        if strongest_open is None or (
            pnl_pct is not None and
            (strongest_open.get("unrealized_pnl_pct") is None or
             pnl_pct > strongest_open["unrealized_pnl_pct"])
        ):
            strongest_open = pos

    candidate_xray = [{
        "rank": rank,
        "symbol": row["symbol"],
        "direction": row["direction"],
        "horizon_hours": row["horizon"],
        "confidence": row["confidence"],
        "expected_net_edge_pct": row["expected_net_edge_pct"],
        "expected_profit_per_risk": profit_per_risk,
        "expected_profit_usdt": expected_profit,
        "current_price": row.get("current_price"),
        "decision_source": row.get("decision_source"),
        "experiment_tier": row.get("experiment_tier"),
        "risk_cap_pct": row.get("risk_cap_pct"),
        "technical_score": (row.get("technical_plan") or {}).get("technical_score"),
        "technical_relation": (
            row.get("technical_plan") or {}
        ).get("relation_to_trigger_direction"),
        "technical_timeframes": (
            row.get("technical_plan") or {}
        ).get("timeframes_used"),
        "beats_open_position_on_observed_return": (
            strongest_open is not None
            and strongest_open.get("unrealized_pnl_pct") is not None
            and float(row["expected_net_edge_pct"]) > float(strongest_open["unrealized_pnl_pct"])
        ),
        "open_position_return_reference_pct": (
            strongest_open.get("unrealized_pnl_pct") if strongest_open else None
        ),
    } for rank, (profit_per_risk, expected_profit, row)
       in enumerate(ranked_candidates[:12], start=1)]

    if portfolio_full:
        for row in candidate_xray:
            row["portfolio_comparison"] = "PORTFOLIO_FULL_CANDIDATE_VISIBLE"

    candidate_outcomes = {
        (row["symbol"], row["direction"], row["horizon"]): {
            "status": "DEĞERLENDİRİLİYOR", "reason": None
        } for _, _, row in ranked_candidates
    }

    for _, _, row in ranked_candidates:
        outcome_key = (row["symbol"], row["direction"], row["horizon"])
        if portfolio_full:
            candidate_outcomes[outcome_key] = {
                "status": "REDDEDİLDİ",
                "reason": "PORTFOLIO_POSITION_CAPACITY_FULL",
            }
            continue
        if selected >= max_new:
            reject("PER_CYCLE_NEW_POSITION_CAP_REACHED")
            candidate_outcomes[outcome_key] = {"status": "REDDEDİLDİ", "reason": "PER_CYCLE_NEW_POSITION_CAP_REACHED"}
            continue
        if row["symbol"] in active_symbols:
            reject("SYMBOL_ALREADY_ACTIVE")
            candidate_outcomes[outcome_key] = {"status": "REDDEDİLDİ", "reason": "SYMBOL_ALREADY_ACTIVE"}
            continue
        if _adverse_same_setup_reentry_blocked(con, row):
            reason_code = "ADVERSE_SAME_SETUP_REENTRY_WITHOUT_1R_RESET"
            reject(reason_code)
            candidate_outcomes[outcome_key] = {
                "status": "REDDEDİLDİ", "reason": reason_code,
            }
            continue
        tier = risk_tiering.tier_for(row)
        plan = row.get("technical_plan")
        if not isinstance(plan, dict):
            reason_code = "TECHNICAL_PLAN_UNAVAILABLE"
            reject(reason_code)
            candidate_outcomes[outcome_key] = {
                "status": "REDDEDİLDİ", "reason": reason_code,
            }
            continue
        fee_rate = taker_fee_rate(tier.market_type)
        base_margin = (
            snapshot["equity"] * tier.margin_fraction
            * float(row.get("execution_size_multiplier", 1.0))
            * float(plan["quality_multiplier"])
        )
        strategy_id = behavior_decision_core.STRATEGY_ID
        strategy_weight = 1.0
        due_time = planned_entry + timedelta(hours=row["horizon"])
        position_cap = snapshot["equity"] * row["risk_cap_pct"] / 100.0
        stop_geometry = _validated_stop_geometry(row)
        if stop_geometry is None:
            reject("STOP_REFERENCE_MISSING_OR_INVALID")
            candidate_outcomes[outcome_key] = {
                "status": "REDDEDİLDİ",
                "reason": "STOP_REFERENCE_MISSING_OR_INVALID",
            }
            continue
        raw_stop_pct, effective_stop_pct = stop_geometry
        trade_risk_budget = (
            snapshot["equity"] * float(row.get("risk_per_trade_pct") or 0.0) / 100.0
        )
        risk_sized_notional = (
            trade_risk_budget / (effective_stop_pct / 100.0 + 2.0 * fee_rate)
            if trade_risk_budget > 0.0 else 0.0
        )
        notional = min(
            base_margin * tier.leverage,
            available_exposure,
            position_cap,
            risk_sized_notional,
            available_cash * tier.leverage / (1.0 + fee_rate * tier.leverage),
        )
        if notional < MIN_PAPER_NOTIONAL_USDT:
            reject("NOTIONAL_BELOW_EXECUTABLE_MINIMUM")
            candidate_outcomes[outcome_key] = {"status": "REDDEDİLDİ", "reason": "NOTIONAL_BELOW_EXECUTABLE_MINIMUM"}
            continue
        if exchange_metadata is not None:
            metadata = exchange_metadata.get(tier.market_type)
            try:
                rules = rules_for(metadata, row["symbol"]) if metadata else None
            except ExchangeMetadataError:
                rules = None
            if rules is None:
                reason_code = f"LIVE_{tier.market_type}_SYMBOL_RULES_UNAVAILABLE"
                reject(reason_code)
                candidate_outcomes[outcome_key] = {"status": "REDDEDİLDİ", "reason": reason_code}
                continue
            executable = estimate_order_rules(
                reference_price=float(row["current_price"]),
                requested_notional=notional,
                side="BUY" if row["direction"] == "LONG" else "SELL",
                symbol=row["symbol"], quantity_step=rules.quantity_step,
                price_tick=rules.price_tick, min_notional=rules.min_notional,
                market_type=tier.market_type,
            )
            if executable.status == "REJECTED":
                reason_code = f"PRESELECTION_{executable.reason}"
                reject(reason_code)
                candidate_outcomes[outcome_key] = {"status": "REDDEDİLDİ", "reason": reason_code}
                continue
        reason = (
            f"BEHAVIOR {row['horizon']}h {row['direction']} | "
            f"cost_after_win={100*row['confidence']:.1f}% | "
            f"mean_net_edge={row['expected_net_edge_pct']:.3f}% | "
            f"execution_size_multiplier={row.get('execution_size_multiplier', 1.0):.3f} | "
            f"technical_score={plan['technical_score']:.3f} | "
            f"technical_frames={','.join(plan['timeframes_used'])} | "
            f"tier={row.get('experiment_tier', 'EXPLORATORY_LEARNING')} | "
            f"risk_tier={tier.tier} | leverage={tier.leverage:g}x | "
            f"market={tier.market_type} | taker_fee_bps={fee_rate * 10000.0:.3f} | "
            f"signals={','.join(tier.signal_sources)} | "
            f"strategy={strategy_id}"
        )
        p_up = row["confidence"] if row["direction"] == "LONG" else 1.0 - row["confidence"]
        p_down = row["confidence"] if row["direction"] == "SHORT" else 1.0 - row["confidence"]
        cursor = con.execute("""
            INSERT OR IGNORE INTO paper_agent_positions(
                forecast_id,agent_version,policy_version,symbol,horizon_hours,
                direction,status,selected_at,as_of,planned_entry_time,due_time,
                notional,confidence,p_up,p_down,target_price,model_version,
                artifact_hash,selection_reason,market_type,validation_cohort,evidence_eligible,
                strategy_id,decision_source,decision_reference_id,leverage,
                margin_allocated,risk_tier,independent_signal_count,signal_sources_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            -row["decision_id"], AGENT_VERSION,
            behavior_decision_core.POLICY_VERSION, row["symbol"],
            row["horizon"], row["direction"], "PENDING_ENTRY", iso(now),
            datetime.fromtimestamp(
                row["evidence"]["evidence_minute_end_ms"] / 1000, timezone.utc
            ).isoformat(), iso(planned_entry), iso(due_time), notional,
            row["confidence"], p_up, p_down, row["target_price"],
            behavior_decision_core.VERSION, row["evidence_sha256"], reason,
            tier.market_type, FORWARD_COHORT, 1, strategy_id,
            row["decision_source"], row["decision_id"],
            tier.leverage, notional / tier.leverage, tier.tier,
            tier.independent_signal_count, json.dumps(tier.signal_sources),
        ))
        if cursor.rowcount == 1:
            row["execution_risk"] = {
                "risk_tier": tier.tier, "leverage": tier.leverage,
                "margin_allocated": notional / tier.leverage,
                "gross_exposure": notional,
                "planned_stop_distance_pct": effective_stop_pct,
                "planned_stop_reference_pct": raw_stop_pct,
                "risk_per_trade_pct": float(row.get("risk_per_trade_pct") or 0.0),
                "planned_stop_and_fee_risk_usdt": notional * (
                    effective_stop_pct / 100.0 + 2.0 * fee_rate
                ),
                "market_type": tier.market_type,
                "taker_fee_rate": fee_rate,
                "stop_basis": "TECHNICAL_STRUCTURE_PLUS_ATR_FROZEN_PLAN",
                "technical_score": float(plan["technical_score"]),
                "technical_timeframes": list(plan["timeframes_used"]),
                "independent_signal_count": tier.independent_signal_count,
                "signal_sources": list(tier.signal_sources),
            }
            experience_memory.capture_decision_thesis(
                con, position_id=cursor.lastrowid, row=row,
                agent_version=AGENT_VERSION,
                policy_version=behavior_decision_core.POLICY_VERSION,
                validation_cohort=FORWARD_COHORT, strategy_id=strategy_id,
                strategy_weight=strategy_weight, selected_at=now,
                planned_entry_time=planned_entry,
            )
            candidate_outcomes[outcome_key] = {"status": "SEÇİLDİ", "reason": "PAPER_POSITION_SELECTED"}
            selected += 1
            active_symbols.add(row["symbol"])
            available_exposure -= notional
            reservation = notional / tier.leverage + notional * fee_rate
            available_cash = max(0.0, available_cash - reservation)
            snapshot["cash"] -= reservation
        else:
            reject("DECISION_ALREADY_SELECTED")
            candidate_outcomes[outcome_key] = {"status": "REDDEDİLDİ", "reason": "DECISION_ALREADY_SELECTED"}
    record_run(selected, available_exposure)
    con.commit()
    if telemetry is not None:
        telemetry.update({
            "selected_count": selected,
            "candidates": [
                {**row, **candidate_outcomes.get(
                    (row["symbol"], row["direction"], row["horizon_hours"]), {}
                )} for row in candidate_xray
            ],
            "core_status": behavior_result["status"],
            "rejection_reasons": dict(reasons),
            "open_positions": open_position_xray,
            "portfolio_capacity": {
                "active": len(active), "capacity": capacity,
                "available_exposure": available_exposure,
            },
        })
    return selected


def report_window(now):
    local = now.astimezone(ISTANBUL)
    report_date = local.date()
    end_local = datetime.combine(
        report_date, datetime.min.time(), tzinfo=ISTANBUL
    ) + timedelta(hours=11, minutes=10)
    if local < end_local:
        # A persistent timer may recover after midnight but before today's
        # deadline. Recover the latest closed window, never a future report.
        report_date -= timedelta(days=1)
        end_local -= timedelta(days=1)
    start_local = end_local - timedelta(days=1)
    return (
        report_date.isoformat(),
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )


def execution_evidence_summary(con, position_id):
    """Explain the executable evidence a trade has and its realized economics."""
    events = con.execute("""
        SELECT event_type,evidence_json,fill_price,fill_quantity,fee,spread_cost,
               slippage_cost FROM paper_position_events
        WHERE position_id=? AND event_type IN ('ENTRY_FILLED','POSITION_CLOSED')
        ORDER BY event_id
    """, (position_id,)).fetchall()
    by_type = {row[0]: row for row in events}
    def unpack(event_type):
        row = by_type.get(event_type)
        if not row:
            return {"proven": False, "missing": ["EVENT"]}
        try:
            evidence = json.loads(row[1] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            evidence = {}
        required = ("best_bid","best_ask","quote_timestamp_ms","executable_reference_price")
        missing = [key for key in required if evidence.get(key) is None]
        return {
            "proven": not missing and row[2] is not None and float(row[3] or 0)>0,
            "missing": missing, "side": evidence.get("side"),
            "best_bid": evidence.get("best_bid"), "best_ask": evidence.get("best_ask"),
            "quote_timestamp_ms": evidence.get("quote_timestamp_ms"),
            "executable_reference_price": evidence.get("executable_reference_price"),
            "fill_price": row[2], "fill_quantity": row[3],
            "fee": row[4], "spread_cost": row[5], "slippage_cost": row[6],
        }
    position = con.execute("""
        SELECT net_pnl,gross_pnl,entry_fee,exit_fee,funding_cost,liquidation_fee,
               funding_source FROM paper_agent_positions WHERE position_id=?
    """, (position_id,)).fetchone()
    economics = None if not position else {
        "gross_pnl": position[1], "net_pnl": position[0],
        "entry_fee": position[2], "exit_fee": position[3],
        "funding_cost": position[4], "liquidation_fee": position[5],
        "funding_source": position[6],
    }
    entry, exit_ = unpack("ENTRY_FILLED"), unpack("POSITION_CLOSED")
    return {"position_id": int(position_id), "entry_execution": entry,
            "exit_execution": exit_, "executable_round_trip_proven": bool(
                entry["proven"] and exit_["proven"]), "economics": economics}


def build_daily_report(con, report_date, start, end):
    snapshot = portfolio_snapshot(con)
    status_counts = dict(con.execute("""
        SELECT status,COUNT(*) FROM paper_agent_positions
        WHERE validation_cohort=? AND evidence_eligible=1 GROUP BY status
    """, (FORWARD_COHORT,)).fetchall())
    lifetime_rows = con.execute("""
        SELECT net_pnl,gross_pnl,entry_fee,exit_fee,entry_spread_cost,
               entry_slippage_cost,exit_spread_cost,exit_slippage_cost,
               funding_cost,liquidation_fee,liquidated
        FROM paper_agent_positions
        WHERE validation_cohort=? AND evidence_eligible=1 AND status='CLOSED'
          AND COALESCE(funding_source,'')!='PENDING_RECONCILIATION'
        ORDER BY exit_time,position_id
    """, (FORWARD_COHORT,)).fetchall()
    lifetime_wins = sum(float(row[0]) > 0 for row in lifetime_rows)
    lifetime_losses = sum(float(row[0]) < 0 for row in lifetime_rows)
    lifetime_breakeven = len(lifetime_rows) - lifetime_wins - lifetime_losses
    positive_net = sum(max(0.0, float(row[0])) for row in lifetime_rows)
    negative_net = abs(sum(min(0.0, float(row[0])) for row in lifetime_rows))
    profit_factor = positive_net / negative_net if negative_net else (
        float("inf") if positive_net else 0.0
    )
    cumulative = peak = max_drawdown = 0.0
    for row in lifetime_rows:
        cumulative += float(row[0])
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    accountability = {
        "selected_total": sum(status_counts.values()),
        "pending_entries": int(status_counts.get("PENDING_ENTRY", 0)),
        "open_positions": int(status_counts.get("OPEN", 0)),
        "closed_total": len(lifetime_rows),
        "cancelled_total": int(status_counts.get("CANCELLED", 0)),
        "wins": lifetime_wins,
        "losses": lifetime_losses,
        "breakeven": lifetime_breakeven,
        "win_rate_pct": (
            100.0 * lifetime_wins / len(lifetime_rows) if lifetime_rows else 0.0
        ),
        "gross_pnl": sum(float(row[1] or 0) for row in lifetime_rows),
        "net_pnl": sum(float(row[0] or 0) for row in lifetime_rows),
        "profit_factor": profit_factor,
        "max_closed_trade_drawdown": max_drawdown,
        "fees": sum(float(row[2] or 0) + float(row[3] or 0) for row in lifetime_rows),
        "spread_cost": sum(float(row[4] or 0) + float(row[6] or 0) for row in lifetime_rows),
        "slippage_cost": sum(float(row[5] or 0) + float(row[7] or 0) for row in lifetime_rows),
        "funding_cost": sum(float(row[8] or 0) for row in lifetime_rows),
        "liquidation_fees": sum(float(row[9] or 0) for row in lifetime_rows),
        "liquidations": sum(int(row[10] or 0) for row in lifetime_rows),
    }
    opened = con.execute("""
        SELECT position_id,symbol,direction,horizon_hours,selected_at,entry_time,
               entry_price,notional,entry_fee,confidence,selection_reason,status,
               liquidation_price,leverage,margin_allocated,risk_tier,
               independent_signal_count
        FROM paper_agent_positions
        WHERE selected_at>=? AND selected_at<?
          AND validation_cohort=? AND evidence_eligible=1
        ORDER BY selected_at,position_id
    """, (iso(start), iso(end), FORWARD_COHORT)).fetchall()
    closed = con.execute("""
        SELECT position_id,symbol,direction,horizon_hours,entry_time,entry_price,
               exit_time,exit_price,notional,entry_fee,exit_fee,gross_pnl,
               net_pnl,selection_reason,funding_cost,funding_event_count,
               funding_source,liquidation_fee,liquidated,close_reason,
               leverage,margin_allocated,risk_tier,independent_signal_count
        FROM paper_agent_positions
        WHERE exit_time>=? AND exit_time<?
          AND validation_cohort=? AND evidence_eligible=1
        ORDER BY exit_time,position_id
    """, (iso(start), iso(end), FORWARD_COHORT)).fetchall()
    management_events = con.execute("""
        SELECT e.position_id,p.symbol,e.market_at,e.event_type,e.reason,
               e.reference_price,e.fill_price,e.fill_quantity,e.fee,
               e.stop_before,e.stop_after
        FROM paper_position_events e
        JOIN paper_agent_positions p ON p.position_id=e.position_id
        WHERE e.market_at>=? AND e.market_at<?
        ORDER BY e.market_at,e.event_id
    """, (iso(start), iso(end))).fetchall()
    wins = sum(1 for row in closed if float(row[12]) > 0)
    daily_net = sum(float(row[12]) for row in closed)
    daily_fees = sum(float(row[9]) + float(row[10]) for row in closed)
    daily_funding = sum(float(row[14] or 0) for row in closed)
    liquidations = sum(int(row[18] or 0) for row in closed)
    payload = {
        "schema": "CRYPTO_PROFIT_AGENT_DAILY_REPORT_V1",
        "report_date": report_date,
        "validation_cohort": FORWARD_COHORT,
        "window_start": iso(start),
        "window_end": iso(end),
        "portfolio": snapshot,
        "orchestrator_accountability": accountability,
        "opened_count": len(opened),
        "closed_count": len(closed),
        "wins": wins,
        "losses": len(closed) - wins,
        "win_rate_pct": (100.0 * wins / len(closed)) if closed else 0.0,
        "daily_net_pnl": daily_net,
        "daily_fees": daily_fees,
        "daily_funding": daily_funding,
        "liquidations": liquidations,
        "opened": opened,
        "closed": closed,
        "management_event_count": len(management_events),
        "management_events": management_events,
    }
    return payload


def markdown_report(payload):
    p = payload["portfolio"]
    a = payload["orchestrator_accountability"]
    lines = [
        "# AG Crypto Analyst — Kripto Kazanç Ajanı",
        "",
        f"Rapor tarihi: {payload['report_date']} | PAPER ONLY",
        f"Pencere: {display_time(payload['window_start'])} → "
        f"{display_time(payload['window_end'])}",
        "",
        "## Portföy",
        "",
        f"- Başlangıç sermayesi: {p['initial_capital']:.2f} USDT",
        f"- Güncel özsermaye: {p['equity']:.2f} USDT ({p['return_pct']:+.2f}%)",
        f"- Nakit: {p['cash']:.2f} USDT",
        f"- Açık pozisyon teminatı: {p['reserved']:.2f} USDT",
        f"- Gerçekleşmiş P&L: {p['realized_pnl']:+.2f} USDT",
        f"- Gerçekleşmemiş P&L: {p['unrealized_pnl']:+.2f} USDT",
        f"- Toplam ücret: {p['total_fees']:.2f} USDT",
        f"- Açık pozisyon: {p['open_positions']}",
        "",
        "## Ana orkestratör hesap özeti — temiz kohort, başlangıçtan bugüne",
        "",
        f"- Seçilen toplam: {a['selected_total']} | bekleyen: {a['pending_entries']} | "
        f"açık: {a['open_positions']} | kapanan: {a['closed_total']} | "
        f"iptal: {a['cancelled_total']}",
        f"- Kazanan/Kaybeden/Başabaş: {a['wins']}/{a['losses']}/{a['breakeven']} | "
        f"başarı: {a['win_rate_pct']:.1f}%",
        f"- Kümülatif brüt/net P&L: {a['gross_pnl']:+.2f} / {a['net_pnl']:+.2f} USDT",
        f"- Profit factor: {a['profit_factor']:.2f} | kapanmış-işlem max drawdown: "
        f"{a['max_closed_trade_drawdown']:.2f} USDT",
        f"- Maliyetler: ücret={a['fees']:.2f}, spread={a['spread_cost']:.2f}, "
        f"slippage={a['slippage_cost']:.2f}, funding={a['funding_cost']:+.4f}, "
        f"liquidation_fee={a['liquidation_fees']:.2f} USDT",
        f"- Liquidation sayısı: {a['liquidations']}",
        "",
        "## Günlük sonuç",
        "",
        f"- Açılan işlem: {payload['opened_count']}",
        f"- Kapanan işlem: {payload['closed_count']}",
        f"- Kazanan/Kaybeden: {payload['wins']}/{payload['losses']}",
        f"- Başarı oranı: {payload['win_rate_pct']:.1f}%",
        f"- Günlük net P&L: {payload['daily_net_pnl']:+.2f} USDT",
        f"- Kapanan işlemlerin ücretleri: {payload['daily_fees']:.2f} USDT",
        f"- Gerçek funding (pozitif=ödendi, negatif=alındı): {payload['daily_funding']:+.4f} USDT",
        f"- Liquidation: {payload['liquidations']}",
        "",
        "## Açılan işlemler",
        "",
    ]
    if not payload["opened"]:
        lines.append("- İşlem açılmadı.")
    for row in payload["opened"]:
        lines.append(
            f"- #{row[0]} {row[1]} {row[2]} {row[3]}h | "
            f"seçim={display_time(row[4])} | "
            f"giriş={display_time(row[5]) if row[5] else 'BEKLİYOR'} @ "
            f"{(format(float(row[6]), '.8g') if row[6] is not None else 'N/A')} | "
            f"notional={float(row[7]):.2f} | ücret={float(row[8]):.2f} | "
            f"confidence={100*float(row[9]):.1f}% | status={row[11]} | {row[10]}"
            f" | liquidation={format(float(row[12]), '.8g') if row[12] is not None else 'N/A'}"
            f" | leverage={float(row[13]):g}x | marjin={float(row[14]):.2f}"
            f" | risk_tier={row[15]} | sinyal={int(row[16])}"
        )
    lines.extend(["", "## Kapanan işlemler", ""])
    if not payload["closed"]:
        lines.append("- İşlem kapanmadı.")
    for row in payload["closed"]:
        lines.append(
            f"- #{row[0]} {row[1]} {row[2]} {row[3]}h | "
            f"giriş={display_time(row[4])} @ {float(row[5]):.8g} | "
            f"çıkış={display_time(row[6])} @ {float(row[7]):.8g} | "
            f"notional={float(row[8]):.2f} | "
            f"ücret={float(row[9])+float(row[10]):.2f} | "
            f"brüt={float(row[11]):+.2f} | net={float(row[12]):+.2f} USDT | "
            f"funding={float(row[14] or 0):+.4f} ({int(row[15] or 0)} olay) | "
            f"liquidated={bool(row[18])} | close={row[19]} | {row[13]}"
            f" | leverage={float(row[20]):g}x | marjin={float(row[21]):.2f}"
            f" | risk_tier={row[22]} | sinyal={int(row[23])}"
        )
    lines.extend(["", "## Gerçek zamanlı pozisyon yönetimi", ""])
    if not payload.get("management_events"):
        lines.append("- Yönetim olayı yok.")
    for row in payload.get("management_events", []):
        lines.append(
            f"- #{row[0]} {row[1]} | {display_time(row[2])} | "
            f"{row[3]} / {row[4]} | piyasa={float(row[5]):.8g} | "
            f"dolum={(format(float(row[6]), '.8g') if row[6] is not None else 'YOK')} | "
            f"miktar={float(row[7]):.8g} | ücret={float(row[8]):.4f} | "
            f"stop={row[9]}→{row[10]}"
        )
    lines.extend([
        "",
        "Not: Bu ajan tamamen sanal para kullanır; gerçek borsa emri vermez.",
    ])
    return "\n".join(lines) + "\n"


def _confirm_remote_report_delivery(con, report_date):
    """Confirm persistence only; persistence is not recipient delivery."""
    mirrored_at = iso(utcnow())
    con.execute("""
        UPDATE paper_agent_daily_reports SET remote_mirror_status='HEALTHY',
            remote_mirrored_at=? WHERE report_date=?
    """, (mirrored_at, report_date))
    con.commit()
    if con.remote_mirror_result.succeeded_for("paper_agent_daily_reports"):
        # Mark recipient-independent persistence complete only after the remote
        # mirror has acknowledged the report row. Bypass RuntimeConnection's
        # mirroring hook so this local acknowledgement cannot recursively
        # create another remote delivery attempt.
        sqlite3.Connection.execute(con, """
            UPDATE paper_agent_daily_reports SET delivery_complete=1
            WHERE report_date=?
        """, (report_date,))
        sqlite3.Connection.commit(con)
        return True
    # The local delivery flag must never claim success when the remote write failed.
    sqlite3.Connection.execute(con, """
        UPDATE paper_agent_daily_reports SET remote_mirror_status='PENDING',
            remote_mirrored_at=NULL WHERE report_date=?
    """, (report_date,))
    sqlite3.Connection.commit(con)
    return False


def maybe_daily_report(con, now):
    window = report_window(now)
    if window is None:
        return None
    report_date, start, end = window
    existing = con.execute("""
        SELECT report_json,delivery_complete FROM paper_agent_daily_reports
        WHERE report_date=?
    """, (report_date,)).fetchone()
    if existing:
        payload = json.loads(existing[0])
        payload["delivery_complete"] = bool(existing[1])
        return payload
    payload = build_daily_report(con, report_date, start, end)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(markdown_report(payload), encoding="utf-8")
    con.execute("""
        INSERT INTO paper_agent_daily_reports(
            report_date,created_at,window_start,window_end,report_json,
            telegram_sent
        ) VALUES(?,?,?,?,?,0)
    """, (
        report_date, iso(now), iso(start), iso(end),
        json.dumps(payload, ensure_ascii=False),
    ))
    con.commit()
    payload["delivery_complete"] = False
    return payload


def render(result):
    p = result["portfolio"]
    print("AG Crypto Analyst - Kripto Kazanç Ajanı v1.0.0 | PAPER_ONLY")
    print(
        f"Bütçe={p['equity']:.2f} USDT ({p['return_pct']:+.2f}%) | "
        f"nakit={p['cash']:.2f} | açık_risk={p['reserved']:.2f} | "
        f"ücret={p['total_fees']:.2f}"
    )
    print(
        f"Bu cycle: seçilen={result['selected']} | doldurulan={result['filled']} | "
        f"kapanan={result['closed']} | iptal={result['cancelled']} | "
        f"açık={p['open_positions']}"
    )
    if result["daily_report"]:
        report = result["daily_report"]
        print(
            f"11:15 raporu: {report['report_date']} | "
            f"açılan={report['opened_count']} | kapanan={report['closed_count']} | "
            f"net={report['daily_net_pnl']:+.2f} USDT"
        )
        print(
            "Remote teslim: " + ("TAMAMLANDI" if report.get("delivery_complete") else "BEKLIYOR")
        )
    print("Not: Gerçek emir yoktur; LONG/SHORT işlemler 1x sanal simülasyondur.")
    for pending in result.get("pending_entries", []):
        print(
            f"Bekleyen giriş: #{pending['position_id']} {pending['symbol']} "
            f"{pending['direction']} | {pending['planned_entry_display']}"
        )
    authority = result["authority"]
    print(
        "Yetki: öneri=EVET | gerçek_emir=HAYIR | "
        f"terfi={authority['promotion_authority']}"
    )
    learning = result["trade_learning"]
    print(
        f"Trade learning: {learning['status']} | temiz_kapanan={learning['closed']} | "
        f"hafta={learning['weeks']} | otomatik_terfi=HAYIR"
    )
    external = learning["external_research"]
    print(
        f"Dış trader araştırması: {external['status']} | "
        f"trader={external['traders']} | kapanan={external['closed_trades']} | "
        "otomatik_kopya=HAYIR"
    )


def run(now=None):
    now = now or utcnow()
    authority = validate_agent_authority(
        execution_mode=os.getenv("AGCA_EXECUTION_MODE", "PAPER_ONLY"),
        policy_version=POLICY_VERSION,
        initial_capital=INITIAL_CAPITAL,
    )
    exchange_metadata = load_execution_metadata()
    con = connect()
    try:
        ensure_tables(con)
        filled, cancelled = fill_pending(con, now, exchange_metadata)
        closed = settle_open(con, now, exchange_metadata)
        selected = select_new_positions(con, now, exchange_metadata)
        new_experience_reviews = experience_memory.review_closed_positions(con, now)
        learning = trade_learning.run(con, FORWARD_COHORT, now)
        learning["new_experience_reviews"] = new_experience_reviews
        daily = maybe_daily_report(con, now)
        snapshot = portfolio_snapshot(con)
        return {
            "status": "SUCCESS",
            "filled": filled,
            "cancelled": cancelled,
            "closed": closed,
            "selected": selected,
            "trade_learning": learning,
            "portfolio": snapshot,
            "pending_entries": pending_entry_schedule(con),
            "daily_report": daily,
            "authority": authority,
            "exchange_metadata": ({key: {
                "symbols": len(value.symbols), "fetched_at": value.fetched_at,
            } for key, value in exchange_metadata.items()}
                if exchange_metadata else None),
        }
    finally:
        con.close()


def run_behavior_selection(now=None):
    """Run the complete behavior PAPER lifecycle on the minute cadence."""
    now = now or utcnow()
    authority = validate_agent_authority(
        execution_mode=os.getenv("AGCA_EXECUTION_MODE", "PAPER_ONLY"),
        policy_version=POLICY_VERSION,
        initial_capital=INITIAL_CAPITAL,
    )
    con = connect()
    try:
        ensure_tables(con)
        exchange_metadata = load_execution_metadata()
        filled_before, cancelled_before = fill_pending(con, now, exchange_metadata)
        closed = settle_open(con, now, exchange_metadata)
        selected = select_new_positions(con, now, exchange_metadata)
        filled_now, cancelled_now = fill_pending(con, utcnow(), exchange_metadata)
        new_experience_reviews = experience_memory.review_closed_positions(con, now)
        if closed or new_experience_reviews:
            learning = trade_learning.run(con, FORWARD_COHORT, now)
        else:
            learning = {
                "status": "NO_NEW_CLOSED_TRADE",
                "closed": 0,
                "weeks": 0,
                "patterns": [],
                "promotion_allowed": False,
            }
        learning["new_experience_reviews"] = new_experience_reviews
        selection = con.execute("SELECT core_status FROM paper_behavior_selection_runs ORDER BY run_id DESC LIMIT 1").fetchone()
        return {
            "status": "SUCCESS", "selected": selected,
            "decision_status": selection[0] if selection else "NO_SELECTION_EVIDENCE",
            "filled": filled_before + filled_now,
            "cancelled": cancelled_before + cancelled_now,
            "closed": closed, "trade_learning": learning,
            "authority": authority, "portfolio": portfolio_snapshot(con),
            "pending_entries": pending_entry_schedule(con),
        }
    finally:
        con.close()


def self_test():
    assert abs(directional_pnl("LONG", 100.0, 105.0, 10.0) - 50.0) < 1e-9
    assert abs(directional_pnl("SHORT", 100.0, 95.0, 10.0) - 50.0) < 1e-9
    base = {
        "p_up": 0.60, "p_down": 0.30,
        "current_price": 100.0, "target_price": 102.0,
    }
    assert candidate_direction(base)[0] == "LONG"
    base.update({"p_up": 0.30, "p_down": 0.60, "target_price": 98.0})
    assert candidate_direction(base)[0] == "SHORT"
    base.update({"p_up": 0.49, "p_down": 0.48, "target_price": 101.0})
    assert candidate_direction(base) is None
    now = datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc)
    date, start, end = report_window(now)
    assert date == "2026-08-17"
    assert end - start == timedelta(days=1)
    print("Crypto Profit Agent self-test: PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return {"status": "SUCCESS"}
    result = run()
    render(result)
    return result


if __name__ == "__main__":
    main()


def fill_pending_from_live_state(con, now, live, exchange_metadata=None):
    """Fill pending PAPER entries only from the authoritative live state callback."""
    rows = con.execute("""
        SELECT position_id,symbol,planned_entry_time,notional,direction,leverage,
               market_type,decision_source,technical_basis_ratio
        FROM paper_agent_positions
        WHERE status='PENDING_ENTRY' AND symbol=? AND market_type=?
        ORDER BY selected_at,position_id
    """, (live.symbol, live.market_type)).fetchall()
    filled = cancelled = 0
    for (position_id, symbol, planned_entry_time, notional, direction, leverage,
         market_type, decision_source, technical_basis_ratio) in rows:
        planned = parse_time(planned_entry_time)
        if now < planned:
            continue
        metadata = (
            exchange_metadata.get(market_type)
            if isinstance(exchange_metadata, dict) else exchange_metadata
        )
        try:
            rules = rules_for(metadata, symbol) if metadata else None
        except ExchangeMetadataError:
            rules = None
        if exchange_metadata is not None and rules is None:
            con.execute("""UPDATE paper_agent_positions SET status='CANCELLED',
                cancel_reason=? WHERE position_id=? AND status='PENDING_ENTRY'""",
                (f"LIVE_{market_type}_SYMBOL_RULES_UNAVAILABLE", position_id))
            cancelled += 1
            continue
        side = "BUY" if direction == "LONG" else "SELL"
        executable_price = live.best_ask if side == "BUY" else live.best_bid
        side_quote_volume = (
            live.ask_quote_volume if side == "BUY" else live.bid_quote_volume
        )
        if executable_price is None or float(executable_price) <= 0:
            continue

        if decision_source == "MARKET_BEHAVIOR_CORE":
            thesis_row = con.execute("""
                SELECT thesis_json FROM paper_agent_decision_theses
                WHERE position_id=?
            """, (position_id,)).fetchone()
            try:
                thesis = json.loads(thesis_row[0]) if thesis_row else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                thesis = {}
            plan = thesis.get("technical_trade_plan")
            if not isinstance(plan, dict):
                con.execute("""UPDATE paper_agent_positions SET
                    status='CANCELLED',cancel_reason='TECHNICAL_PLAN_MISSING'
                    WHERE position_id=? AND status='PENDING_ENTRY'""", (position_id,))
                cancelled += 1
                continue
            if (
                str(plan.get("symbol")) != symbol
                or str(plan.get("direction")) != direction
                or not technical_trade_plan.plan_is_fresh(plan, now)
            ):
                con.execute("""UPDATE paper_agent_positions SET
                    status='CANCELLED',cancel_reason='TECHNICAL_PLAN_STALE_OR_MISMATCHED'
                    WHERE position_id=? AND status='PENDING_ENTRY'""", (position_id,))
                cancelled += 1
                continue
            try:
                levels = technical_trade_plan.execution_levels(
                    plan, float(executable_price),
                    basis_ratio=technical_basis_ratio,
                )
            except (TypeError, ValueError, KeyError):
                con.execute("""UPDATE paper_agent_positions SET
                    status='CANCELLED',cancel_reason='TECHNICAL_EXECUTION_BASIS_INVALID'
                    WHERE position_id=? AND status='PENDING_ENTRY'""", (position_id,))
                cancelled += 1
                continue
            if technical_basis_ratio is None:
                technical_basis_ratio = float(levels["basis_ratio"])
                con.execute("""UPDATE paper_agent_positions
                    SET technical_basis_ratio=?
                    WHERE position_id=? AND status='PENDING_ENTRY'""",
                    (technical_basis_ratio, position_id))
            if not (
                float(levels["entry_low"]) <= float(executable_price)
                <= float(levels["entry_high"])
            ):
                continue

        result = execution_market_data.capture_live_reference(
            con, position_id=position_id, symbol=symbol, purpose="ENTRY",
            live=live, observed_at=now,
        )
        if result is None:
            continue
        entry_time, reference_price, _audit_quote_volume = result
        # The last trade/reference price above is audit context only. A PAPER
        # entry executes from the fresh side of the live quote and visible
        # liquidity, after the frozen technical entry zone has been satisfied.
        fill = simulated_fill(
            reference_price=float(executable_price) if executable_price is not None else 0.0,
            requested_notional=float(notional), side=side,
            available_quote_volume=side_quote_volume, require_liquidity=True,
            symbol=symbol, quantity_step=rules.quantity_step if rules else 1e-8,
            price_tick=rules.price_tick if rules else 1e-8,
            min_notional=rules.min_notional if rules else 5.0,
            best_bid=live.best_bid, best_ask=live.best_ask,
            quote_timestamp_ms=live.quote_timestamp_ms,
            liquidity_timestamp_ms=live.liquidity_timestamp_ms,
            observed_at=iso(now), market_type=market_type,
            source=live.quote_source,
        )
        if fill is None:
            continue
        entry_price, fee = fill.price, fill.fee
        actual_notional = fill.quantity * fill.price
        margin = actual_notional / float(leverage)
        cash = float(con.execute(
            "SELECT cash FROM paper_agent_portfolio WHERE portfolio_id=1"
        ).fetchone()[0])
        if cash + 1e-9 < margin + fee:
            con.execute("""UPDATE paper_agent_positions SET status='CANCELLED',
                cancel_reason='INSUFFICIENT_VIRTUAL_CASH'
                WHERE position_id=? AND status='PENDING_ENTRY'""", (position_id,))
            cancelled += 1
            continue
        maintenance_rate = float(os.getenv(
            "AGCA_PAPER_MAINTENANCE_MARGIN_RATE", "0.005"
        ))
        liquidation_threshold = liquidation_price(
            entry_price=entry_price, direction=direction, leverage=float(leverage),
            maintenance_margin_rate=maintenance_rate,
        ) if market_type == "USD_M_PERPETUAL" else None
        con.execute("""UPDATE paper_agent_positions SET
            status='OPEN',entry_time=?,entry_price=?,quantity=?,notional=?,entry_fee=?,
            market_type=?,entry_spread_cost=?,entry_slippage_cost=?,partial_fill=?,
            liquidation_price=?,margin_allocated=?
            WHERE position_id=? AND status='PENDING_ENTRY'""", (
            iso(entry_time), entry_price, fill.quantity, actual_notional, fee,
            market_type, fill.spread_cost, fill.slippage_cost, int(fill.partial),
            liquidation_threshold, margin, position_id,
        ))
        con.execute("""UPDATE paper_agent_portfolio
            SET cash=cash-?,total_fees=total_fees+? WHERE portfolio_id=1""",
            (margin + fee, fee))
        filled += 1
    con.commit()
    return filled, cancelled


