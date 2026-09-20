"""Idempotent SQLite accounting for event-managed PAPER positions."""
from __future__ import annotations

import hashlib
import json
import os
import math
from datetime import datetime, timezone

from paper_trading.execution_model import execute_paper, quote_evidence, validate_quote
from paper_trading.execution_contract import ExecutionFill, ExecutionDecision, MarketSnapshot, OrderIntent
from paper_trading.futures_costs import (
    FuturesCostError, directional_funding_payment, liquidation_breached,
)
from paper_trading.position_policy import Decision, PositionState, evaluate, initialize


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
            last_exit_attempt_at TEXT,
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
    event_columns = {row[1] for row in con.execute("PRAGMA table_info(paper_position_events)")}
    if "evidence_json" not in event_columns:
        con.execute("ALTER TABLE paper_position_events ADD COLUMN evidence_json TEXT")
    columns = {row[1] for row in con.execute(
        "PRAGMA table_info(paper_position_management)"
    )}
    if "last_thesis_at" not in columns:
        con.execute("ALTER TABLE paper_position_management ADD COLUMN last_thesis_at TEXT")
    if "funding_reconciliation_required" not in columns:
        con.execute("""ALTER TABLE paper_position_management ADD COLUMN
            funding_reconciliation_required INTEGER NOT NULL DEFAULT 0""")
    if "last_exit_attempt_at" not in columns:
        con.execute("ALTER TABLE paper_position_management ADD COLUMN last_exit_attempt_at TEXT")
    position_columns = {row[1] for row in con.execute(
        "PRAGMA table_info(paper_agent_positions)"
    )}
    for name, definition in (
        ("margin_allocated", "REAL"), ("funding_source", "TEXT"),
        ("liquidation_price", "REAL"), ("leverage", "REAL NOT NULL DEFAULT 1"),
        ("market_type", "TEXT NOT NULL DEFAULT 'USD_M_PERPETUAL'"),
        ("liquidation_fee", "REAL NOT NULL DEFAULT 0"),
        ("liquidated", "INTEGER NOT NULL DEFAULT 0"),
        ("independent_signal_count", "INTEGER NOT NULL DEFAULT 1"),
        ("signal_sources_json", "TEXT NOT NULL DEFAULT '[\"LEGACY\"]'"),
    ):
        if position_columns and name not in position_columns:
            con.execute(f"ALTER TABLE paper_agent_positions ADD COLUMN {name} {definition}")
            position_columns.add(name)
    if position_columns:
        # Same idempotent legacy backfill as the PAPER agent.  Leveraged gross
        # notional is not cash margin.
        con.execute("""UPDATE paper_agent_positions
            SET margin_allocated=notional / CASE WHEN leverage>0 THEN leverage ELSE 1 END
            WHERE margin_allocated IS NULL""")


def initialize_open_positions(con, now):
    # Schema setup is a caller/startup responsibility.  This function is on the
    # realtime hot path (symbols() is polled by both websocket loops and health
    # callbacks), so running ensure_tables() here would repeatedly execute DDL
    # and an UPDATE against paper_agent_positions.  With the selection service
    # writing the same SQLite database that can create avoidable writer
    # contention and block realtime startup.
    rows = con.execute("""
        SELECT p.position_id,p.symbol,p.direction,p.entry_time,p.entry_price,
               p.quantity,p.margin_allocated,p.entry_fee
        FROM paper_agent_positions p
        LEFT JOIN paper_position_management m ON m.position_id=p.position_id
        WHERE p.status='OPEN' AND m.position_id IS NULL
        ORDER BY p.position_id
    """).fetchall()
    initialized, blocked = 0, []
    for position_id, symbol, direction, entry_time, entry_price, quantity, margin_allocated, fee in rows:
        risk = con.execute("""
            SELECT volatility_stop_reference_pct FROM asset_risk_snapshots
            WHERE symbol=? AND created_at<=? ORDER BY created_at DESC,id DESC LIMIT 1
        """, (symbol, entry_time)).fetchone()
        thesis_row = con.execute("""
            SELECT thesis_json FROM paper_agent_decision_theses WHERE position_id=?
        """, (position_id,)).fetchone()
        thesis = {}
        if thesis_row:
            try:
                thesis = json.loads(thesis_row[0] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                thesis = {}
        stale_recovery = not risk and now >= parse(con.execute(
            "SELECT due_time FROM paper_agent_positions WHERE position_id=?",
            (position_id,),
        ).fetchone()[0])
        if not risk and not stale_recovery:
            blocked.append((int(position_id), "MISSING_ENTRY_RISK_SNAPSHOT"))
            continue
        # A missing entry snapshot must block a new/active thesis, but it must
        # not make an already expired PAPER position immortal.  The stop value
        # is immaterial here because the first fresh tick is forced through the
        # maximum-hold exit; MAX_STOP_PCT is used only to create the accounting
        # state required for that audited close.
        volatility_stop = float(risk[0]) if risk else 5.0
        # A setup-specific invalidation may tighten the volatility envelope when
        # the frozen thesis actually provides a price level.  Never invent one.
        invalidation_price = thesis.get("invalidation_price")
        invalidation_stop = None
        if invalidation_price is not None:
            try:
                invalidation_price = float(invalidation_price)
                if direction == "LONG" and 0 < invalidation_price < float(entry_price):
                    invalidation_stop = 100.0 * (float(entry_price)-invalidation_price) / float(entry_price)
                elif direction == "SHORT" and invalidation_price > float(entry_price):
                    invalidation_stop = 100.0 * (invalidation_price-float(entry_price)) / float(entry_price)
            except (TypeError, ValueError):
                invalidation_stop = None
        frozen_risk = thesis.get("execution_risk") or {}
        frozen_stop = frozen_risk.get("planned_stop_distance_pct")
        if frozen_stop is not None:
            try:
                frozen_stop = float(frozen_stop)
            except (TypeError, ValueError):
                frozen_stop = None
        if frozen_stop is not None and (
            not math.isfinite(frozen_stop) or not 0.5 <= frozen_stop <= 5.0
        ):
            blocked.append((int(position_id), "INVALID_FROZEN_STOP_PLAN"))
            continue
        # The risk budget and stop must refer to the SAME frozen selection
        # plan.  An unvalidated invalidation price must not silently tighten
        # the stop after sizing; legacy positions retain the volatility plan.
        stop_reference = frozen_stop if frozen_stop is not None else volatility_stop
        state = initialize(direction, float(entry_price), stop_reference)
        con.execute("""INSERT INTO paper_position_management(
            position_id,manager_version,initialized_at,last_market_at,
            stop_distance_pct,current_stop_price,best_price,original_quantity,
            remaining_quantity,remaining_cost_basis,remaining_entry_fee,last_funding_at,
            last_thesis_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            position_id, VERSION, iso(now), entry_time, state.stop_distance_pct,
            state.stop_price, state.best_price, quantity, quantity, margin_allocated, fee,
            entry_time, entry_time,
        ))
        _event(con, position_id, parse(entry_time), now, "STOP_INITIALIZED",
               ("STALE_POSITION_RECOVERY_WITHOUT_ENTRY_RISK_SNAPSHOT"
                if stale_recovery else "ENTRY_RISK_SNAPSHOT"),
               float(entry_price), None, 0, 0, 0, 0,
               None, state.stop_price, {
                   "risk_stop_pct": state.stop_distance_pct,
                   "raw_risk_stop_reference_pct": float(risk[0]) if risk else None,
                   "thesis_invalidation_price": invalidation_price,
                   "thesis_invalidation_stop_pct": invalidation_stop,
                   "selected_stop_reference_pct": stop_reference,
                   "stop_reference_basis": (
                       "FROZEN_SELECTION_RISK_PLAN" if frozen_stop is not None
                       else "LEGACY_VOLATILITY_ENVELOPE"
                   ),
                   "planned_stop_and_fee_risk_usdt": frozen_risk.get(
                       "planned_stop_and_fee_risk_usdt"
                   ),
                   "entry_price": float(entry_price),
                   "direction": direction,
                   "margin_allocated": float(margin_allocated or 0),
                   "risk_snapshot_available": bool(risk),
                   "stop_policy_min_pct": 0.5,
                   "stop_policy_max_pct": 5.0,
               })
        initialized += 1
    con.commit()
    return {"initialized": initialized, "blocked": blocked}


def _event(con, position_id, market_at, observed_at, event_type, reason,
           reference_price, fill_price, fill_quantity, fee, spread, slippage,
           stop_before, stop_after, evidence):
    def safe(value):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, dict):
            return {key: safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [safe(item) for item in value]
        return value
    evidence_json = json.dumps(safe(evidence), sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True)
    digest = hashlib.sha256(evidence_json.encode()).hexdigest()
    con.execute("""INSERT OR IGNORE INTO paper_position_events(
        position_id,market_at,observed_at,event_type,reason,reference_price,
        fill_price,fill_quantity,fee,spread_cost,slippage_cost,stop_before,
        stop_after,evidence_sha256,evidence_json,real_order_allowed
    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""", (
        position_id, iso(market_at), iso(observed_at), event_type, reason,
        reference_price, fill_price, fill_quantity, fee, spread, slippage,
        stop_before, stop_after, digest, evidence_json,
    ))


def record_execution(con, position_id, observed_at, purpose, symbol, side,
                     market_type, decision, market=None):
    evidence = {
        "observed_at": iso(observed_at), "quote_timestamp_ms": None,
        "best_bid": None, "best_ask": None,
        "selected_executable_side": "ASK" if side == "BUY" else "BID",
        "executable_reference_price": None, "final_simulated_fill_price": None,
        "quote_age_ms": None, "fee": 0, "slippage": 0, "quantity": 0,
        "notional": 0, "partial": False,
        **decision.evidence,
    }
    evidence.update({"symbol": symbol, "side": side, "market_type": market_type,
                     "decision_at": iso(observed_at), "real_order_allowed": 0})
    evidence.setdefault("status", decision.status)
    evidence.setdefault("rejection_reason", decision.reason if not decision.fill else None)
    fill = decision.fill
    reference = evidence.get("executable_reference_price") or 0.0
    _event(con, position_id, observed_at, observed_at,
           purpose + ("_FILLED" if fill else "_REJECTED"), decision.reason,
           reference, fill.price if fill else None, fill.quantity if fill else 0,
           fill.fee if fill else 0, fill.spread_cost if fill else 0,
           fill.slippage_cost if fill else 0, None, None, evidence)


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
               m.opposing_events,m.last_market_at,m.last_thesis_at,m.last_exit_attempt_at,
               p.liquidation_price,p.leverage,p.independent_signal_count,
               p.signal_sources_json,p.market_type
        FROM paper_agent_positions p JOIN paper_position_management m USING(position_id)
        WHERE p.symbol=? AND p.market_type=? AND p.status='OPEN'
    """, (live.symbol, live.market_type)).fetchone()
    if not row:
        return {"status": "NO_OPEN_POSITION"}
    (position_id, direction, entry_price, due_time, _, stop_distance, stop_price,
     best_price, original_qty, remaining_qty, remaining_basis, remaining_entry_fee,
     unallocated_funding, last_funding_at, realized_net, realized_gross,
     tp1_done, tp2_done, opposing_events, last_market_at, last_thesis_at,
     last_exit_attempt_at, liquidation_threshold, leverage, signal_count,
     signal_sources_json, market_type) = row
    if market_at <= parse(last_market_at):
        return {"status": "DUPLICATE_OR_OLD_EVENT"}

    try:
        signal_sources = set(json.loads(signal_sources_json or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        signal_sources = set()
    flow_is_entire_thesis = (
        int(signal_count or 0) == 1 and signal_sources == {"MARKET_BEHAVIOR"}
    )
    raw_thesis = live.thesis_supported(direction) if flow_is_entire_thesis else None
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
    forced_liquidation = bool(
        market_type == "USD_M_PERPETUAL" and liquidation_threshold is not None
        and liquidation_breached(
            direction=direction, threshold=float(liquidation_threshold),
            low=float(live.last_price), high=float(live.last_price),
        )
    )
    if forced_liquidation:
        decision = Decision(
            "CLOSE", state.remaining_fraction,
            f"PAPER_{float(leverage):g}X_LIQUIDATION",
            decision.stop_price, decision.best_price, decision.opposing_events,
        )
    evidence = {
        "manager_version": VERSION, "symbol": live.symbol,
        "book_imbalance": live.book_imbalance, "taker_imbalance": live.taker_imbalance,
        "market_timestamp_ms": live.market_timestamp_ms,
        "position_decision": {
            "action": decision.action, "reason": decision.reason,
            "thesis_supported": thesis_signal,
            "thesis_signal_applicable": flow_is_entire_thesis,
            "opposing_events_before": int(opposing_events),
            "opposing_events_after": int(decision.opposing_events),
            "protective_stop": float(stop_price),
            "proposed_stop": float(decision.stop_price),
            "liquidation_threshold": liquidation_threshold,
            "forced_liquidation": forced_liquidation,
            "remaining_quantity": float(remaining_qty),
            "market_price": float(live.last_price),
        },
    }
    # Preserve the decision even when executable liquidity is missing.  A
    # rejected exit is NOT a successful close, but must remain auditable.
    if decision.action == "CLOSE" and (
        decision.reason == "THESIS_INVALIDATED"
        or decision.reason == "PROTECTIVE_STOP"
        or forced_liquidation
    ):
        _event(con, position_id, market_at, observed_at, "RISK_EXIT_DECIDED",
               decision.reason, live.last_price, None, 0, 0, 0, 0,
               stop_price, decision.stop_price, evidence)
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

    retry_seconds = max(0.1, float(os.getenv("AGCA_PAPER_EXIT_RETRY_SECONDS", "1")))
    if (last_exit_attempt_at is not None and
            (market_at - parse(last_exit_attempt_at)).total_seconds() < retry_seconds):
        con.execute("UPDATE paper_position_management SET last_market_at=? WHERE position_id=?",
                    (iso(market_at), position_id))
        con.commit()
        return {"status": "EXIT_RETRY_THROTTLED", "reason": decision.reason}
    exit_side = "SELL" if direction == "LONG" else "BUY"
    market = MarketSnapshot(
        reference_price=live.last_price,
        available_quote_volume=(live.ask_quote_volume if exit_side == "BUY" else live.bid_quote_volume),
        observed_at=iso(observed_at), best_bid=live.best_bid, best_ask=live.best_ask,
        quote_timestamp_ms=live.quote_timestamp_ms,
        liquidity_timestamp_ms=live.liquidity_timestamp_ms,
        market_type=market_type, source=live.quote_source,
    )
    reference, quote_reason = validate_quote(market, exit_side)
    evidence.update(quote_evidence(market, exit_side))
    if quote_reason or rules is None:
        reason = quote_reason or "EXCHANGE_RULES_UNAVAILABLE"
        record_execution(con, position_id, observed_at, "EXIT", live.symbol,
                         exit_side, market_type,
                         ExecutionDecision("REJECTED", reason, evidence=evidence))
        con.execute("UPDATE paper_position_management SET last_exit_attempt_at=? WHERE position_id=?",
                    (iso(market_at), position_id))
        con.commit()
        return {"status": "BLOCKED", "reason": reason}
    if market_type == "USD_M_PERPETUAL" and (live.quote_source or "").startswith("OKX_"):
        from paper_trading.execution_market_data import fetch_executable_quote
        try:
            market = fetch_executable_quote(live.symbol, market_type, exit_side)
        except ValueError:
            record_execution(con, position_id, observed_at, "EXIT", live.symbol,
                             exit_side, market_type,
                             ExecutionDecision("REJECTED", "EXECUTABLE_QUOTE_SOURCE_UNAVAILABLE"))
            con.execute("UPDATE paper_position_management SET last_exit_attempt_at=? WHERE position_id=?",
                        (iso(market_at), position_id))
            con.commit()
            return {"status": "BLOCKED", "reason": "EXECUTABLE_QUOTE_SOURCE_UNAVAILABLE"}
    reference, quote_reason = validate_quote(market, exit_side)
    if quote_reason:
        record_execution(con, position_id, observed_at, "EXIT", live.symbol,
                         exit_side, market_type, ExecutionDecision(
                             "REJECTED", quote_reason, evidence=quote_evidence(market, exit_side)))
        con.execute("UPDATE paper_position_management SET last_exit_attempt_at=? WHERE position_id=?",
                    (iso(market_at), position_id))
        con.commit()
        return {"status": "BLOCKED", "reason": quote_reason}
    target_qty = min(float(remaining_qty), float(original_qty) * decision.close_fraction)
    if decision.action == "REDUCE" and target_qty * reference < rules.min_notional:
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
    # A reduce-only BUY must budget for the adapter's adverse exit price;
    # otherwise one exact quantity step can round to zero. The accounted fill
    # is still capped to target_qty below, so this can never reverse exposure.
    exit_notional_buffer = 1.02 if direction == "SHORT" else 1.0
    execution = execute_paper(
        OrderIntent(live.symbol, exit_side, target_qty * reference * exit_notional_buffer,
                    reduce_only=True), market, rules,
    )
    fill = execution.fill
    evidence.update(execution.evidence)
    # Keep the normal execution/taker fee on the fill.  Liquidation is an
    # additional exchange charge and is accounted separately below; replacing
    # fill.fee here would silently discard the taker fee and then double-count
    # the liquidation fee.
    if fill is None:
        rejection_reason = execution.reason
        _event(con, position_id, market_at, observed_at, "EXIT_REJECTED",
               rejection_reason, reference, None, 0, 0, 0, 0,
               stop_price, stop_price, evidence)
        con.execute("""UPDATE paper_position_management SET
            last_market_at=?,last_exit_attempt_at=? WHERE position_id=?""",
                    (iso(market_at), iso(market_at), position_id))
        con.commit()
        return {"status": "BLOCKED", "reason": rejection_reason}

    # Funding/exposure stops at the executable observation, not an older
    # trigger trade timestamp (quote acquisition may have taken time).
    observed_at = parse(market.observed_at)
    market_at = observed_at
    closed_qty = min(float(remaining_qty), target_qty, float(fill.quantity))
    # Funding lookup stays outside the latency-critical path. A segment exists
    # only after a real fill; rejected/retried exits cannot pollute the ledger.
    funding_pending = market_type == "USD_M_PERPETUAL"
    new_funding = 0.0
    if funding_pending:
        con.execute("""INSERT OR IGNORE INTO paper_funding_reconciliation_segments(
            position_id,start_at,end_at,direction,quantity,status
        ) VALUES(?,?,?,?,?,'PENDING')""", (
            position_id, last_funding_at, iso(market_at), direction, remaining_qty,
        ))
        evidence["funding_status"] = "PENDING_RECONCILIATION"
    else:
        evidence["funding_status"] = "SPOT_NOT_APPLICABLE"
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
    liquidation_fee = (
        closed_qty * fill.price * float(rules.liquidation_fee_rate)
        if forced_liquidation else 0.0
    )
    # The execution model's normal taker fee and the exchange liquidation fee
    # are distinct costs.  A forced liquidation must pay both; previously the
    # liquidation fee was only reported in a column and never deducted from
    # cash/net PnL.
    # remaining_cost_basis is allocated isolated margin, not gross notional.
    # The account cannot recover less than zero from that isolated margin.
    # Derive realized net PnL from the actual cash movement so a gap through
    # liquidation cannot create a paper loss larger than the margin that was
    # actually at risk (entry fee was already paid separately at entry).
    recovery = max(
        0.0, cost_basis + gross - exit_fee - liquidation_fee - funding_part
    )
    net = recovery - cost_basis - entry_fee_part
    new_qty = float(remaining_qty) - closed_qty
    final = new_qty <= max(1e-12, rules.quantity_step / 2)
    con.execute("""UPDATE paper_position_management SET
        last_market_at=?,last_funding_at=?,last_exit_attempt_at=?,current_stop_price=?,best_price=?,
        remaining_quantity=?,remaining_cost_basis=?,remaining_entry_fee=?,
        unallocated_funding=?,realized_net_pnl=realized_net_pnl+?,
        realized_gross_pnl=realized_gross_pnl+?,tp1_done=?,tp2_done=?,opposing_events=?,
        last_thesis_at=?,funding_reconciliation_required=MAX(
            funding_reconciliation_required,?)
        WHERE position_id=?""", (
        iso(market_at), iso(market_at), iso(market_at), decision.stop_price, decision.best_price,
        max(0.0, new_qty), float(remaining_basis) - cost_basis,
        float(remaining_entry_fee) - entry_fee_part, funding_pool - funding_part,
        net, gross, int(bool(tp1_done) or decision.reason == "TAKE_PROFIT_1R"),
        int(bool(tp2_done) or decision.reason == "TAKE_PROFIT_2R"),
        decision.opposing_events, next_thesis_at, int(funding_pending), position_id,
    ))
    con.execute("""UPDATE paper_agent_portfolio SET cash=cash+?,
        realized_pnl=realized_pnl+?,total_fees=total_fees+? WHERE portfolio_id=1""",
        (recovery, net, exit_fee + liquidation_fee))
    con.execute("""UPDATE paper_agent_positions SET exit_fee=exit_fee+?,
        exit_spread_cost=exit_spread_cost+?,exit_slippage_cost=exit_slippage_cost+?,
        funding_cost=funding_cost+?,gross_pnl=?,net_pnl=?,
        liquidation_fee=liquidation_fee+?,liquidated=MAX(liquidated,?)
        WHERE position_id=?""", (
        exit_fee, spread_cost, slippage_cost, funding_part,
        float(realized_gross) + gross, float(realized_net) + net,
        liquidation_fee, int(forced_liquidation), position_id,
    ))
    evidence.update({"decision_at": iso(observed_at), "quantity": closed_qty,
                     "fee": exit_fee, "slippage": slippage_cost,
                     "notional": closed_qty * fill.price,
                     "status": "FILLED" if final else "PARTIAL",
                     "partial": not final})
    event_type = "POSITION_CLOSED" if final else "POSITION_REDUCED"
    _event(con, position_id, market_at, observed_at, event_type, decision.reason,
           reference, fill.price, closed_qty, exit_fee, spread_cost,
           slippage_cost, stop_price, decision.stop_price, evidence)
    if final:
        con.execute("""UPDATE paper_agent_positions SET status='CLOSED',exit_time=?,
            exit_price=?,close_reason=?,funding_source=?
            WHERE position_id=? AND status='OPEN'""",
            (iso(market_at), fill.price, decision.reason,
             "PENDING_RECONCILIATION" if funding_pending else "SPOT_NOT_APPLICABLE",
             position_id))
    con.commit()
    return {"status": event_type, "reason": decision.reason,
            "quantity": closed_qty, "net_pnl": net, "final": final}


def reconcile_pending_funding(con, funding_loader, now):
    """Apply previously unavailable funding without reopening trade outcomes."""
    # Schema setup belongs to startup/migration.  This reconciliation runs from
    # the realtime maintenance loop, so repeating DDL/backfill here creates an
    # avoidable SQLite writer-lock path exactly where execution must stay live.
    rows = con.execute("""SELECT s.segment_id,s.position_id,s.start_at,s.end_at,
               s.direction,s.quantity
        FROM paper_funding_reconciliation_segments s
        JOIN paper_agent_positions p USING(position_id)
        WHERE s.status='PENDING' AND p.market_type='USD_M_PERPETUAL'
        ORDER BY position_id,start_at,segment_id""").fetchall()
    resolved, blocked = 0, 0
    grouped = {}
    for row in rows:
        grouped.setdefault(row[1], []).append(row)
    for position_id, segments in grouped.items():
        symbol = con.execute(
            "SELECT symbol FROM paper_agent_positions WHERE position_id=?", (position_id,)
        ).fetchone()
        if not symbol:
            blocked += len(segments)
            continue
        try:
            events = funding_loader(
                symbol=symbol[0], start=min(parse(row[2]) for row in segments),
                end=max(parse(row[3]) for row in segments),
            )
        except FuturesCostError:
            blocked += len(segments)
            continue
        total_cost = 0.0
        for segment_id, _, start_at, end_at, direction, quantity in segments:
            start_ms = int(parse(start_at).timestamp() * 1000)
            end_ms = int(parse(end_at).timestamp() * 1000)
            segment_events = [event for event in events
                              if start_ms < event.funding_time_ms <= end_ms]
            cost = directional_funding_payment(
                direction=direction, quantity=float(quantity), events=segment_events,
            ) if segment_events else 0.0
            total_cost += cost
            con.execute("""UPDATE paper_funding_reconciliation_segments SET
                status='RESOLVED',funding_cost=?,resolved_at=? WHERE segment_id=?""",
                (cost, iso(now), segment_id))
        con.execute("""UPDATE paper_agent_positions SET funding_cost=funding_cost+?,
            net_pnl=net_pnl-? WHERE position_id=?""", (total_cost, total_cost, position_id))
        con.execute("""UPDATE paper_position_management SET
            realized_net_pnl=realized_net_pnl-? WHERE position_id=?""",
            (total_cost, position_id))
        con.execute("""UPDATE paper_agent_portfolio SET cash=cash-?,
            realized_pnl=realized_pnl-? WHERE portfolio_id=1""", (total_cost, total_cost))
        remaining_pending = con.execute("""
            SELECT COUNT(*) FROM paper_funding_reconciliation_segments
            WHERE position_id=? AND status='PENDING'
        """, (position_id,)).fetchone()[0]
        if int(remaining_pending) == 0:
            con.execute("""UPDATE paper_position_management SET
                funding_reconciliation_required=0 WHERE position_id=?""", (position_id,))
            con.execute("""UPDATE paper_agent_positions SET
                funding_source='BINANCE_USD_M_FUNDING_HISTORY'
                WHERE position_id=?""", (position_id,))
        resolved += len(segments)
    con.commit()
    return {"resolved": resolved, "blocked": blocked}
