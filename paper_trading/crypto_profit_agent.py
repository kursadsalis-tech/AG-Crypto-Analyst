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
from paper_trading.execution_model import PAPER_MARKET, simulated_decision, simulated_fill
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


DB_NAME = forecast_engine.DB_NAME
AGENT_VERSION = "CRYPTO_PROFIT_AGENT_V1"
POLICY_VERSION = "PAPER_OPPORTUNITY_POLICY_V3_IMMEDIATE_EXECUTION"
INITIAL_CAPITAL = float(os.getenv("AGCA_PAPER_INITIAL_USDT", "10000"))
FEE_BPS = float(os.getenv("AGCA_PAPER_FEE_BPS", "10"))
FEE_RATE = FEE_BPS / 10000.0
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
    ):
        if name not in position_columns:
            con.execute(f"ALTER TABLE paper_agent_positions ADD COLUMN {name} {definition}")
    con.execute("""UPDATE paper_agent_positions SET margin_allocated=notional
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


def latest_mark(con, symbol):
    row = con.execute("""
        SELECT close FROM market_klines
        WHERE symbol=? AND interval='1h'
        ORDER BY close_time DESC LIMIT 1
    """, (symbol,)).fetchone()
    return float(row[0]) if row else None


def portfolio_snapshot(con):
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
    for symbol, direction, entry, quantity, notional in open_rows:
        reserved += float(notional)
        mark = latest_mark(con, symbol)
        if mark is not None:
            unrealized += directional_pnl(
                direction, float(entry), mark, float(quantity)
            )
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
        "return_pct": (equity / initial - 1.0) * 100.0,
        "open_positions": len(open_rows),
        "validation_cohort": portfolio[4],
        "evidence_start_at": portfolio[5],
    }


def fill_pending(con, now, exchange_metadata=None):
    rows = con.execute("""
        SELECT position_id,symbol,planned_entry_time,notional,direction,leverage,
               market_type
        FROM paper_agent_positions
        WHERE status='PENDING_ENTRY'
        ORDER BY selected_at,position_id
    """).fetchall()
    filled = 0
    cancelled = 0
    for (position_id, symbol, planned_entry_time, notional, direction, leverage,
         market_type) in rows:
        planned = parse_time(planned_entry_time)
        metadata = (
            exchange_metadata.get(market_type)
            if isinstance(exchange_metadata, dict) else exchange_metadata
        )
        try:
            rules = rules_for(metadata, symbol) if metadata else None
        except ExchangeMetadataError:
            rules = None
        if exchange_metadata is not None and rules is None:
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
                )
            except execution_market_data.SpotManagementVenueUnavailable:
                con.execute("""
                    UPDATE paper_agent_positions SET status='CANCELLED',cancel_reason=?
                    WHERE position_id=? AND status='PENDING_ENTRY'
                """, ("OKX_SPOT_MANAGEMENT_UNAVAILABLE", position_id))
                cancelled += 1
                continue
            except (requests.RequestException, ValueError):
                result = None
        if result is None:
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
        entry_time, reference_price, quote_volume = result
        notional = float(notional)
        side = "BUY" if direction == "LONG" else "SELL"
        fill = simulated_fill(
            reference_price=reference_price, requested_notional=notional,
            side=side, available_quote_volume=quote_volume,
            require_liquidity=True,
            symbol=symbol,
            quantity_step=rules.quantity_step if rules else 1e-8,
            price_tick=rules.price_tick if rules else 1e-8,
            min_notional=rules.min_notional if rules else 5.0,
        )
        if fill is None:
            con.execute("""
                UPDATE paper_agent_positions SET status='CANCELLED',cancel_reason=?
                WHERE position_id=? AND status='PENDING_ENTRY'
            """, ("SIMULATED_EXCHANGE_REJECTION", position_id))
            cancelled += 1
            continue
        entry_price, fee = fill.price, fill.fee
        actual_notional = fill.quantity * fill.price
        margin = actual_notional / float(leverage)
        cash = float(con.execute(
            "SELECT cash FROM paper_agent_portfolio WHERE portfolio_id=1"
        ).fetchone()[0])
        if cash + 1e-9 < margin + fee:
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
        filled += 1
    con.commit()
    return filled, cancelled


def settle_open(con, now, exchange_metadata=None):
    rows = con.execute("""
        SELECT p.position_id,p.symbol,p.direction,p.due_time,p.entry_time,
               p.entry_price,p.quantity,p.notional,p.entry_fee,p.liquidation_price,
               p.margin_allocated,p.leverage,p.market_type
        FROM paper_agent_positions p
        LEFT JOIN paper_position_management m USING(position_id)
        WHERE p.status='OPEN' AND p.due_time<=? AND m.position_id IS NULL
        ORDER BY due_time,position_id
    """, (iso(now),)).fetchall()
    closed = 0
    for row in rows:
        (position_id, symbol, direction, due, entry_time, entry, quantity,
         notional, entry_fee, liq_price, margin_allocated, leverage,
         market_type) = row
        result = None
        if market_type in {"USD_M_PERPETUAL", "SPOT"}:
            try:
                result = execution_market_data.capture_reference(
                    con, position_id=position_id, symbol=symbol,
                    expected=parse_time(due), purpose="EXIT", observed_at=now,
                    market_type=market_type,
                )
            except (requests.RequestException, ValueError):
                result = None
        else:
            result = exact_close(con, symbol, due)
        if result is None:
            continue
        exit_time, reference_price, quote_volume = result
        entry = float(entry)
        quantity = float(quantity)
        notional = float(notional)
        entry_fee = float(entry_fee)
        exit_side = "SELL" if direction == "LONG" else "BUY"
        metadata = (
            exchange_metadata.get(market_type)
            if isinstance(exchange_metadata, dict) else exchange_metadata
        )
        try:
            rules = rules_for(metadata, symbol) if metadata else None
        except ExchangeMetadataError:
            rules = None
        if exchange_metadata is not None and rules is None:
            continue
        liquidation_time = None
        if market_type == "USD_M_PERPETUAL" and liq_price is not None:
            liquidation_time = first_liquidation_event(
                con, symbol, entry_time, due, direction, float(liq_price),
            )
        if liquidation_time is not None:
            exit_time = liquidation_time
            exit_price = float(liq_price)
            exit_fee = quantity * exit_price * rules.liquidation_fee_rate
            exit_spread_cost = 0.0
            exit_slippage_cost = 0.0
            liquidated = 1
            close_reason = f"CONSERVATIVE_{float(leverage):g}X_LIQUIDATION"
        else:
            fill = simulated_fill(
                reference_price=reference_price,
                requested_notional=quantity * reference_price,
                side=exit_side,
                available_quote_volume=quote_volume,
                require_liquidity=True,
                symbol=symbol,
                quantity_step=rules.quantity_step if rules else 1e-8,
                price_tick=rules.price_tick if rules else 1e-8,
                min_notional=rules.min_notional if rules else 5.0,
            )
            if fill is None:
                continue
            exit_price = fill.price
            exit_fee = quantity * exit_price * (fill.fee / (fill.quantity * fill.price))
            exit_spread_cost = fill.spread_cost
            exit_slippage_cost = fill.slippage_cost
            liquidated = 0
            close_reason = "HORIZON_DUE_CLOSE"
        gross = directional_pnl(direction, entry, exit_price, quantity)
        if market_type == "USD_M_PERPETUAL":
            funding_events = fetch_funding_history_via_postgres(
                database_url=os.getenv("AGCA_REMOTE_DATABASE_URL", ""),
                symbol=symbol, start=parse_time(entry_time), end=exit_time,
            )
            funding = directional_funding_payment(
                direction=direction, quantity=quantity, events=funding_events,
            )
            funding_source = "BINANCE_USD_M_FUNDING_HISTORY"
        else:
            funding_events = []
            funding = 0.0
            funding_source = "SPOT_NOT_APPLICABLE"
        recovery = max(0.0, float(margin_allocated) + gross - exit_fee - funding)
        net = recovery - float(margin_allocated) - entry_fee
        con.execute("""
            UPDATE paper_agent_positions SET
                status='CLOSED',exit_time=?,exit_price=?,exit_fee=?,
                gross_pnl=?,net_pnl=?,exit_spread_cost=?,exit_slippage_cost=?,funding_cost=?,
                funding_event_count=?,funding_source=?,liquidation_fee=?,liquidated=?,close_reason=?
            WHERE position_id=? AND status='OPEN'
        """, (
            iso(exit_time), exit_price, exit_fee, gross, net,
            exit_spread_cost, exit_slippage_cost, funding, len(funding_events), funding_source,
            exit_fee if liquidated else 0.0, liquidated, close_reason, position_id,
        ))
        con.execute("""
            UPDATE paper_agent_portfolio SET
                cash=cash+?,realized_pnl=realized_pnl+?,
                total_fees=total_fees+?
            WHERE portfolio_id=1
        """, (recovery, net, exit_fee))
        closed += 1
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


def select_new_positions(con, now, exchange_metadata=None):
    behavior_result = behavior_decision_core.evaluate(con, now)
    snapshot = portfolio_snapshot(con)
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

    if capacity <= 0:
        reject("PORTFOLIO_POSITION_CAPACITY_FULL")
        record_run(0, 0.0)
        con.commit()
        return 0
    max_new = min(MAX_NEW_PER_CYCLE, capacity)
    max_exposure = snapshot["equity"] * MAX_TOTAL_EXPOSURE
    available_exposure = max(0.0, max_exposure - snapshot["reserved"])
    selected = 0
    planned_entry = immediate_paper_entry_time(now)
    for row in behavior_result["candidates"]:
        if selected >= max_new:
            reject("PER_CYCLE_NEW_POSITION_CAP_REACHED")
            continue
        if row["symbol"] in active_symbols:
            reject("SYMBOL_ALREADY_ACTIVE")
            continue
        tier = risk_tiering.tier_for(row)
        base_margin = (
            snapshot["equity"] * tier.margin_fraction
            * float(row.get("execution_size_multiplier", 1.0))
        )
        strategy_id = behavior_decision_core.STRATEGY_ID
        strategy_weight = 1.0
        due_time = planned_entry + timedelta(hours=row["horizon"])
        risk_room = snapshot["equity"] * row["risk_cap_pct"] / 100.0
        notional = min(
            base_margin * tier.leverage,
            available_exposure,
            risk_room,
            snapshot["cash"] * tier.leverage / (1.0 + FEE_RATE * tier.leverage),
        )
        if notional < MIN_PAPER_NOTIONAL_USDT:
            reject("NOTIONAL_BELOW_EXECUTABLE_MINIMUM")
            continue
        if exchange_metadata is not None:
            metadata = exchange_metadata.get(tier.market_type)
            try:
                rules = rules_for(metadata, row["symbol"]) if metadata else None
            except ExchangeMetadataError:
                rules = None
            if rules is None:
                reject(f"LIVE_{tier.market_type}_SYMBOL_RULES_UNAVAILABLE")
                continue
            executable = simulated_decision(
                reference_price=float(row["current_price"]),
                requested_notional=notional,
                side="BUY" if row["direction"] == "LONG" else "SELL",
                symbol=row["symbol"], quantity_step=rules.quantity_step,
                price_tick=rules.price_tick, min_notional=rules.min_notional,
                require_liquidity=False,
            )
            if executable.fill is None:
                reject(f"PRESELECTION_{executable.reason}")
                continue
        reason = (
            f"BEHAVIOR {row['horizon']}h {row['direction']} | "
            f"cost_after_win={100*row['confidence']:.1f}% | "
            f"mean_net_edge={row['expected_net_edge_pct']:.3f}% | "
            f"execution_size_multiplier={row.get('execution_size_multiplier', 1.0):.3f} | "
            f"tier={row.get('experiment_tier', 'EXPLORATORY_LEARNING')} | "
            f"risk_tier={tier.tier} | leverage={tier.leverage:g}x | "
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
            selected += 1
            active_symbols.add(row["symbol"])
            available_exposure -= notional
            snapshot["cash"] -= notional / tier.leverage + notional * FEE_RATE
        else:
            reject("DECISION_ALREADY_SELECTED")
    record_run(selected, available_exposure)
    con.commit()
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
