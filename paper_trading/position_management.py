"""Idempotent SQLite accounting for event-managed PAPER positions."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from paper_trading.execution_model import simulated_fill
from paper_trading.futures_costs import FuturesCostError, directional_funding_payment
from paper_trading.position_policy import PositionState, evaluate, initialize


VERSION = "PAPER_POSITION_MANAGER_V1"


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def parse(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def ensure_tables(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS paper_position_management (
            position_id INTEGER PRIMARY KEY,
            manager_version TEXT NOT NULL,
            initialized_at TEXT NOT NULL,
            last_market_at TEXT NOT NULL,
            stop_distance_pct REAL NOT NULL,
            current_stop_price REAL NOT NULL,
            best_price REAL NOT NULL,
            original_quantity REAL NOT NULL,
            remaining_quantity REAL NOT NULL,
            remaining_cost_basis REAL NOT NULL,
            remaining_entry_fee REAL NOT NULL,
            unallocated_funding REAL NOT NULL DEFAULT 0,
            funding_reconciliation_required INTEGER NOT NULL DEFAULT 0,
            last_funding_at TEXT NOT NULL,
            realized_net_pnl REAL NOT NULL DEFAULT 0,
            realized_gross_pnl REAL NOT NULL DEFAULT 0,
            tp1_done INTEGER NOT NULL DEFAULT 0,
            tp2_done INTEGER NOT NULL DEFAULT 0,
            opposing_events INTEGER NOT NULL DEFAULT 0,
            last_thesis_at TEXT,
            real_order_allowed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS paper_position_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL,
            market_at TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            reason TEXT NOT NULL,
            reference_price REAL NOT NULL,
            fill_price REAL,
            fill_quantity REAL NOT NULL DEFAULT 0,
            fee REAL NOT NULL DEFAULT 0,
            spread_cost REAL NOT NULL DEFAULT 0,
            slippage_cost REAL NOT NULL DEFAULT 0,
            stop_before REAL,
            stop_after REAL,
            evidence_sha256 TEXT NOT NULL,
            real_order_allowed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(position_id, market_at, event_type, reason)
        );
        CREATE INDEX IF NOT EXISTS idx_paper_position_events_position
        ON paper_position_events(position_id,event_id);
        CREATE TABLE IF NOT EXISTS paper_funding_reconciliation_segments (
            segment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER NOT NULL,
            start_at TEXT NOT NULL,
            end_at TEXT NOT NULL,
            direction TEXT NOT NULL,
            quantity REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            funding_cost REAL,
            resolved_at TEXT,
            UNIQUE(position_id,start_at,end_at,quantity)
        );
    """)
    columns = {row[1] for row in con.execute(
        "PRAGMA table_info(paper_position_management)"
    )}
    if "last_thesis_at" not in columns:
        con.execute("ALTER TABLE paper_position_management ADD COLUMN last_thesis_at TEXT")
    if "funding_reconciliation_required" not in columns:
        con.execute("""ALTER TABLE paper_position_management ADD COLUMN
            funding_reconciliation_required INTEGER NOT NULL DEFAULT 0""")
    position_columns = {row[1] for row in con.execute(
        "PRAGMA table_info(paper_agent_positions)"
    )}
    if position_columns and "funding_source" not in position_columns:
        con.execute("ALTER TABLE paper_agent_positions ADD COLUMN funding_source TEXT")


def initialize_open_positions(con, now):
    ensure_tables(con)
    rows = con.execute("""
        SELECT p.position_id,p.symbol,p.direction,p.entry_time,p.entry_price,
               p.quantity,p.notional,p.entry_fee
        FROM paper_agent_positions p
        LEFT JOIN paper_position_management m ON m.position_id=p.position_id
        WHERE p.status='OPEN' AND m.position_id IS NULL
        ORDER BY p.position_id
    """).fetchall()
    initialized, blocked = 0, []
    for position_id, symbol, direction, entry_time, entry_price, quantity, notional, fee in rows:
        risk = con.execute("""
            SELECT volatility_stop_reference_pct FROM asset_risk_snapshots
            WHERE symbol=? AND created_at<=? ORDER BY created_at DESC,id DESC LIMIT 1
        """, (symbol, entry_time)).fetchone()
        if not risk:
            blocked.append((int(position_id), "MISSING_ENTRY_RISK_SNAPSHOT"))
            continue
        state = initialize(direction, float(entry_price), float(risk[0]))
        con.execute("""INSERT INTO paper_position_management(
            position_id,manager_version,initialized_at,last_market_at,
            stop_distance_pct,current_stop_price,best_price,original_quantity,
            remaining_quantity,remaining_cost_basis,remaining_entry_fee,last_funding_at,
            last_thesis_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            position_id, VERSION, iso(now), entry_time, state.stop_distance_pct,
            state.stop_price, state.best_price, quantity, quantity, notional, fee,
            entry_time, entry_time,
        ))
        _event(con, position_id, parse(entry_time), now, "STOP_INITIALIZED",
               "ENTRY_RISK_SNAPSHOT", float(entry_price), None, 0, 0, 0, 0,
               None, state.stop_price, {"risk_stop_pct": state.stop_distance_pct})
        initialized += 1
    con.commit()
    return {"initialized": initialized, "blocked": blocked}


def _event(con, position_id, market_at, observed_at, event_type, reason,
           reference_price, fill_price, fill_quantity, fee, spread, slippage,
           stop_before, stop_after, evidence):
    digest = hashlib.sha256(json.dumps(
        evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()
    con.execute("""INSERT OR IGNORE INTO paper_position_events(
        position_id,market_at,observed_at,event_type,reason,reference_price,
        fill_price,fill_quantity,fee,spread_cost,slippage_cost,stop_before,
        stop_after,evidence_sha256,real_order_allowed
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""", (
        position_id, iso(market_at), iso(observed_at), event_type, reason,
        reference_price, fill_price, fill_quantity, fee, spread, slippage,
        stop_before, stop_after, digest,
    ))


def process_market_state(con, live, observed_at, rules, funding_loader=None):
    """Apply one fresh normalized market state in a single SQLite transaction."""
    if live.last_price is None or live.market_timestamp_ms is None:
        return {"status": "NO_PRICE"}
    market_at = datetime.fromtimestamp(live.market_timestamp_ms / 1000, tz=timezone.utc)
    row = con.execute("""
        SELECT p.position_id,p.direction,p.entry_price,p.due_time,p.status,
               m.stop_distance_pct,m.current_stop_price,m.best_price,
               m.original_quantity,m.remaining_quantity,m.remaining_cost_basis,
               m.remaining_entry_fee,m.unallocated_funding,m.last_funding_at,
               m.realized_net_pnl,m.realized_gross_pnl,m.tp1_done,m.tp2_done,
               m.opposing_events,m.last_market_at,m.last_thesis_at
        FROM paper_agent_positions p JOIN paper_position_management m USING(position_id)
        WHERE p.symbol=? AND p.status='OPEN'
    """, (live.symbol,)).fetchone()
    if not row:
        return {"status": "NO_OPEN_POSITION"}
    (position_id, direction, entry_price, due_time, _, stop_distance, stop_price,
     best_price, original_qty, remaining_qty, remaining_basis, remaining_entry_fee,
     unallocated_funding, last_funding_at, realized_net, realized_gross,
     tp1_done, tp2_done, opposing_events, last_market_at, last_thesis_at) = row
    if market_at <= parse(last_market_at):
        return {"status": "DUPLICATE_OR_OLD_EVENT"}

    raw_thesis = live.thesis_supported(direction)
    thesis_due = (
        last_thesis_at is None
        or (market_at - parse(last_thesis_at)).total_seconds() >= 1.0
    )
    thesis_signal = raw_thesis if thesis_due else None
    next_thesis_at = iso(market_at) if thesis_due and raw_thesis is not None else last_thesis_at
    state = PositionState(
        direction=direction, entry_price=float(entry_price),
        remaining_fraction=float(remaining_qty) / float(original_qty),
        stop_distance_pct=float(stop_distance), stop_price=float(stop_price),
        best_price=float(best_price), tp1_done=bool(tp1_done), tp2_done=bool(tp2_done),
        opposing_events=int(opposing_events),
    )
    decision = evaluate(
        state, float(live.last_price), thesis_supported=thesis_signal,
        now=market_at, maximum_exit_at=parse(due_time),
    )
    evidence = {
        "manager_version": VERSION, "symbol": live.symbol,
        "book_imbalance": live.book_imbalance, "taker_imbalance": live.taker_imbalance,
        "market_timestamp_ms": live.market_timestamp_ms,
    }
    if decision.action in {"HOLD", "UPDATE_STOP"}:
        con.execute("""UPDATE paper_position_management SET
            last_market_at=?,best_price=?,
            current_stop_price=?,opposing_events=?,last_thesis_at=? WHERE position_id=?""", (
            iso(market_at), decision.best_price, decision.stop_price, decision.opposing_events,
            next_thesis_at, position_id,
        ))
        if decision.action == "UPDATE_STOP" or decision.opposing_events != opposing_events:
            _event(con, position_id, market_at, observed_at, decision.action,
                   decision.reason, live.last_price, None, 0, 0, 0, 0,
                   stop_price, decision.stop_price, evidence)
        con.commit()
        return {"status": decision.action, "reason": decision.reason}

    if live.visible_quote_volume is None:
        return {"status": "BLOCKED", "reason": "MISSING_VISIBLE_LIQUIDITY"}
    # Funding lookup is intentionally outside the latency-critical exit path.
    # The exact interval and exposed quantity are frozen here and reconciled by
    # the maintenance task; a database/network outage can never delay a stop.
    funding_pending, new_funding = True, 0.0
    con.execute("""INSERT OR IGNORE INTO paper_funding_reconciliation_segments(
        position_id,start_at,end_at,direction,quantity,status
    ) VALUES(?,?,?,?,?,'PENDING')""", (
        position_id, last_funding_at, iso(market_at), direction, remaining_qty,
    ))
    evidence["funding_status"] = "PENDING_RECONCILIATION"
    target_qty = min(float(remaining_qty), float(original_qty) * decision.close_fraction)
    if decision.action == "REDUCE" and target_qty * float(live.last_price) < rules.min_notional:
        deferred_reason = f"{decision.reason}_BELOW_MIN_NOTIONAL"
        con.execute("""UPDATE paper_position_management SET
            last_market_at=?,current_stop_price=?,best_price=?,opposing_events=?,
            tp1_done=?,tp2_done=?,last_thesis_at=? WHERE position_id=?""", (
            iso(market_at), decision.stop_price, decision.best_price,
            decision.opposing_events,
            int(bool(tp1_done) or decision.reason == "TAKE_PROFIT_1R"),
            int(bool(tp2_done) or decision.reason == "TAKE_PROFIT_2R"),
            next_thesis_at, position_id,
        ))
        _event(con, position_id, market_at, observed_at, "UPDATE_STOP",
               deferred_reason, live.last_price, None, 0, 0, 0, 0,
               stop_price, decision.stop_price, evidence)
        con.commit()
        return {"status": "UPDATE_STOP", "reason": deferred_reason}
    fill = simulated_fill(
        reference_price=float(live.last_price),
        requested_notional=target_qty * float(live.last_price),
        side="SELL" if direction == "LONG" else "BUY",
        available_quote_volume=float(live.visible_quote_volume),
        require_liquidity=True, symbol=live.symbol,
        quantity_step=rules.quantity_step, price_tick=rules.price_tick,
        min_notional=rules.min_notional,
    )
    if fill is None:
        _event(con, position_id, market_at, observed_at, "EXIT_REJECTED",
               "SIMULATED_EXCHANGE_REJECTION", live.last_price, None, 0, 0, 0, 0,
               stop_price, stop_price, evidence)
        con.execute("UPDATE paper_position_management SET last_market_at=? WHERE position_id=?",
                    (iso(market_at), position_id))
        con.commit()
        return {"status": "BLOCKED", "reason": "SIMULATED_EXCHANGE_REJECTION"}

    closed_qty = min(float(remaining_qty), target_qty, float(fill.quantity))
    fill_ratio = closed_qty / float(fill.quantity)
    exit_fee = fill.fee * fill_ratio
    spread_cost = fill.spread_cost * fill_ratio
    slippage_cost = fill.slippage_cost * fill_ratio
    fraction = closed_qty / float(remaining_qty)
    cost_basis = float(remaining_basis) * fraction
    entry_fee_part = float(remaining_entry_fee) * fraction
    funding_pool = float(unallocated_funding) + new_funding
    funding_part = funding_pool * fraction
    gross = ((fill.price - float(entry_price)) * closed_qty
             if direction == "LONG" else (float(entry_price) - fill.price) * closed_qty)
    net = gross - entry_fee_part - exit_fee - funding_part
    recovery = cost_basis + gross - exit_fee - funding_part
    new_qty = float(remaining_qty) - closed_qty
    final = new_qty <= max(1e-12, rules.quantity_step / 2)
    con.execute("""UPDATE paper_position_management SET
        last_market_at=?,last_funding_at=?,current_stop_price=?,best_price=?,
        remaining_quantity=?,remaining_cost_basis=?,remaining_entry_fee=?,
        unallocated_funding=?,realized_net_pnl=realized_net_pnl+?,
        realized_gross_pnl=realized_gross_pnl+?,tp1_done=?,tp2_done=?,opposing_events=?,
        last_thesis_at=?,funding_reconciliation_required=MAX(
            funding_reconciliation_required,?)
        WHERE position_id=?""", (
        iso(market_at), iso(market_at), decision.stop_price, decision.best_price,
        max(0.0, new_qty), float(remaining_basis) - cost_basis,
        float(remaining_entry_fee) - entry_fee_part, funding_pool - funding_part,
        net, gross, int(bool(tp1_done) or decision.reason == "TAKE_PROFIT_1R"),
        int(bool(tp2_done) or decision.reason == "TAKE_PROFIT_2R"),
        decision.opposing_events, next_thesis_at, int(funding_pending), position_id,
    ))
    con.execute("""UPDATE paper_agent_portfolio SET cash=cash+?,
        realized_pnl=realized_pnl+?,total_fees=total_fees+? WHERE portfolio_id=1""",
        (recovery, net, exit_fee))
    con.execute("""UPDATE paper_agent_positions SET exit_fee=exit_fee+?,
        exit_spread_cost=exit_spread_cost+?,exit_slippage_cost=exit_slippage_cost+?,
        funding_cost=funding_cost+?,gross_pnl=?,net_pnl=? WHERE position_id=?""", (
        exit_fee, spread_cost, slippage_cost, funding_part,
        float(realized_gross) + gross, float(realized_net) + net, position_id,
    ))
    event_type = "POSITION_CLOSED" if final else "POSITION_REDUCED"
    _event(con, position_id, market_at, observed_at, event_type, decision.reason,
           live.last_price, fill.price, closed_qty, exit_fee, spread_cost,
           slippage_cost, stop_price, decision.stop_price, evidence)
    if final:
        con.execute("""UPDATE paper_agent_positions SET status='CLOSED',exit_time=?,
            exit_price=?,close_reason=?,funding_source=?
            WHERE position_id=? AND status='OPEN'""",
            (iso(market_at), fill.price, decision.reason,
             "PENDING_RECONCILIATION" if funding_pending else "BINANCE_USD_M_FUNDING_HISTORY",
             position_id))
    con.commit()
    return {"status": event_type, "reason": decision.reason,
            "quantity": closed_qty, "net_pnl": net, "final": final}


def reconcile_pending_funding(con, funding_loader, now):
    """Apply previously unavailable funding without reopening trade outcomes."""
    ensure_tables(con)
    rows = con.execute("""SELECT segment_id,position_id,start_at,end_at,direction,quantity
        FROM paper_funding_reconciliation_segments WHERE status='PENDING'
        ORDER BY segment_id""").fetchall()
    resolved, blocked = 0, 0
    for segment_id, position_id, start_at, end_at, direction, quantity in rows:
        symbol = con.execute(
            "SELECT symbol FROM paper_agent_positions WHERE position_id=?", (position_id,)
        ).fetchone()
        if not symbol:
            blocked += 1
            continue
        try:
            events = funding_loader(
                symbol=symbol[0], start=parse(start_at), end=parse(end_at),
            )
            cost = directional_funding_payment(
                direction=direction, quantity=float(quantity), events=events,
            ) if events else 0.0
        except FuturesCostError:
            blocked += 1
            continue
        con.execute("""UPDATE paper_funding_reconciliation_segments SET
            status='RESOLVED',funding_cost=?,resolved_at=? WHERE segment_id=?""",
            (cost, iso(now), segment_id))
        con.execute("""UPDATE paper_agent_positions SET funding_cost=funding_cost+?,
            net_pnl=net_pnl-? WHERE position_id=?""", (cost, cost, position_id))
        con.execute("""UPDATE paper_position_management SET
            realized_net_pnl=realized_net_pnl-? WHERE position_id=?""",
            (cost, position_id))
        con.execute("""UPDATE paper_agent_portfolio SET cash=cash-?,
            realized_pnl=realized_pnl-? WHERE portfolio_id=1""", (cost, cost))
        pending = con.execute("""SELECT COUNT(*) FROM paper_funding_reconciliation_segments
            WHERE position_id=? AND status='PENDING'""", (position_id,)).fetchone()[0]
        if pending == 0:
            con.execute("""UPDATE paper_position_management SET
                funding_reconciliation_required=0 WHERE position_id=?""", (position_id,))
            con.execute("""UPDATE paper_agent_positions SET
                funding_source='BINANCE_USD_M_FUNDING_HISTORY'
                WHERE position_id=?""", (position_id,))
        resolved += 1
    con.commit()
    return {"resolved": resolved, "blocked": blocked}
