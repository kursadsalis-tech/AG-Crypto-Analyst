"""Read-only operator projection for AGCA Live Control Center.

This module has no scheduler, market connection, trading authority, or database
write path. It projects canonical PAPER runtime state into operator-friendly data.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from paper_trading.crypto_profit_agent import DB_NAME
from paper_trading.position_policy import TP1_R, TP2_R

MARKET_STATE_PATH = Path(os.getenv(
    "AGCA_PAPER_REALTIME_MARKET_STATE",
    "/var/lib/agca/paper-realtime-market-state.json",
))
HEALTH_PATH = Path(os.getenv(
    "AGCA_PAPER_REALTIME_HEALTH",
    "/var/lib/agca/paper-realtime-health.json",
))


def _utcnow():
    return datetime.now(timezone.utc)


def _parse_iso(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_market_state(path=MARKET_STATE_PATH):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"at": None, "live_market": []}
    if payload.get("execution_mode") != "PAPER_ONLY":
        return {"at": payload.get("at"), "live_market": []}
    if payload.get("real_order_allowed") is not False:
        return {"at": payload.get("at"), "live_market": []}
    return payload


def load_health(path=HEALTH_PATH):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"status": "UNKNOWN", "at": None}
    if payload.get("execution_mode") != "PAPER_ONLY":
        return {"status": "UNKNOWN", "at": payload.get("at")}
    if payload.get("real_order_allowed") is not False:
        return {"status": "UNKNOWN", "at": payload.get("at")}
    return payload


def _state_index(payload):
    return {
        (row.get("symbol"), row.get("market_type")): row
        for row in payload.get("live_market", [])
        if row.get("symbol") and row.get("market_type")
    }


def _freshness_seconds(state, now):
    stamp_ms = state.get("quote_timestamp_ms") or state.get("market_timestamp_ms")
    if not stamp_ms:
        return None
    stamp = datetime.fromtimestamp(float(stamp_ms) / 1000, tz=timezone.utc)
    return max(0.0, (now - stamp).total_seconds())


def _mark_price(direction, state):
    # What an operator can actually close against now: LONG sells to bid,
    # SHORT buys from ask. This matches executable-side PAPER semantics.
    if direction == "LONG":
        return state.get("best_bid") or state.get("last_price")
    return state.get("best_ask") or state.get("last_price")


def _live_pnl(direction, entry_price, quantity, mark):
    if not all(value is not None for value in (entry_price, quantity, mark)):
        return None
    sign = 1.0 if direction == "LONG" else -1.0
    return sign * (float(mark) - float(entry_price)) * float(quantity)


def overview(db_path=DB_NAME, market_state_path=MARKET_STATE_PATH,
             health_path=HEALTH_PATH, now=None):
    """Return a read-only, human-facing snapshot of canonical PAPER state."""
    now = now or _utcnow()
    market = load_market_state(market_state_path)
    health = load_health(health_path)
    states = _state_index(market)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        portfolio = con.execute(
            """SELECT initial_capital,cash,realized_pnl,total_fees
               FROM paper_agent_portfolio WHERE portfolio_id=1"""
        ).fetchone()
        rows = con.execute(
            """SELECT p.position_id,p.symbol,p.market_type,p.direction,p.entry_time,
                      p.entry_price,p.quantity,p.notional,p.leverage,p.selection_reason,
                      p.decision_source,p.decision_reference_id,
                      m.stop_distance_pct,m.current_stop_price,m.tp1_done,m.tp2_done,
                      m.remaining_quantity
               FROM paper_agent_positions p
               LEFT JOIN paper_position_management m USING(position_id)
               WHERE p.status='OPEN' ORDER BY p.position_id"""
        ).fetchall()
        closed_rows = con.execute(
            """SELECT position_id,symbol,market_type,direction,entry_time,entry_price,
                      exit_time,exit_price,quantity,net_pnl,close_reason,total_fees
               FROM (
                   SELECT position_id,symbol,market_type,direction,entry_time,entry_price,
                          exit_time,exit_price,quantity,net_pnl,close_reason,
                          entry_fee + exit_fee AS total_fees
                   FROM paper_agent_positions WHERE status='CLOSED'
               ) ORDER BY exit_time DESC,position_id DESC LIMIT 100"""
        ).fetchall()
    finally:
        con.close()

    positions = []
    total_unrealized = 0.0
    for row in rows:
        (position_id, symbol, market_type, direction, entry_time, entry_price,
         quantity, notional, leverage, reason, decision_source, decision_reference_id,
         stop_distance_pct, current_stop_price, tp1_done, tp2_done,
         remaining_quantity) = row
        state = states.get((symbol, market_type), {})
        mark = _mark_price(direction, state)
        pnl = _live_pnl(direction, entry_price, quantity, mark)
        if pnl is not None:
            total_unrealized += pnl
        freshness = _freshness_seconds(state, now)
        positions.append({
            "position_id": position_id,
            "symbol": symbol,
            "market_type": market_type,
            "direction": direction,
            "entry_time": entry_time,
            "entry_price": entry_price,
            "last_price": state.get("last_price"),
            "best_bid": state.get("best_bid"),
            "best_ask": state.get("best_ask"),
            "mark_price": mark,
            "quantity": quantity,
            "notional": notional,
            "leverage": leverage,
            "unrealized_pnl": pnl,
            "quote_source": state.get("quote_source"),
            "freshness_seconds": freshness,
            "market_status": (
                "CANLI" if freshness is not None and freshness <= 5.0
                else "GECİKMİŞ"
            ),
            "decision_reason": reason,
            "decision_source": decision_source,
            "decision_reference_id": decision_reference_id,
            "decision_flow": {
                "observation": {
                    "status": "CANLI" if freshness is not None and freshness <= 5.0 else "GECİKMİŞ",
                    "source": state.get("quote_source"),
                    "last_price": state.get("last_price"),
                    "book_imbalance": state.get("book_imbalance"),
                    "taker_imbalance": state.get("taker_imbalance"),
                },
                "opportunity": {"reason": reason},
                "decision": {
                    "direction": direction,
                    "source": decision_source,
                    "reference_id": decision_reference_id,
                },
                "execution": {
                    "status": "AÇIK",
                    "entry_price": entry_price,
                    "mark_price": mark,
                    "unrealized_pnl": pnl,
                },
            },
            "current_stop_price": current_stop_price,
            "stop_distance_pct": stop_distance_pct,
            "tp1_price": (
                float(entry_price) * (1 + float(stop_distance_pct) * TP1_R / 100)
                if direction == "LONG" and entry_price is not None and stop_distance_pct is not None
                else float(entry_price) * (1 - float(stop_distance_pct) * TP1_R / 100)
                if entry_price is not None and stop_distance_pct is not None else None
            ),
            "tp2_price": (
                float(entry_price) * (1 + float(stop_distance_pct) * TP2_R / 100)
                if direction == "LONG" and entry_price is not None and stop_distance_pct is not None
                else float(entry_price) * (1 - float(stop_distance_pct) * TP2_R / 100)
                if entry_price is not None and stop_distance_pct is not None else None
            ),
            "tp1_done": bool(tp1_done),
            "tp2_done": bool(tp2_done),
            "remaining_quantity": remaining_quantity,
        })

    initial, cash, realized, fees = portfolio or (0.0, 0.0, 0.0, 0.0)
    fresh_count = sum(p["market_status"] == "CANLI" for p in positions)
    health_at = _parse_iso(health.get("at"))
    heartbeat_age = (
        max(0.0, (now - health_at).total_seconds()) if health_at else None
    )
    worker_status = health.get("status") or "UNKNOWN"
    worker_healthy = worker_status == "CONNECTED" and (
        heartbeat_age is not None and heartbeat_age <= 30.0
    )
    return {
        "system": {
            "execution_mode": "PAPER_ONLY",
            "real_order_allowed": False,
            "open_positions": len(positions),
            "live_positions": fresh_count,
            "market_data_status": (
                "CANLI" if positions and fresh_count == len(positions)
                else "GECİKMİŞ" if positions else "POZİSYON_YOK"
            ),
            "worker_status": worker_status,
            "worker_healthy": worker_healthy,
            "heartbeat_age_seconds": heartbeat_age,
            "last_action": health.get("action"),
            "active_symbols": health.get("active_symbols"),
        },
        "portfolio": {
            "initial_capital": initial,
            "cash": cash,
            "realized_pnl": realized,
            "total_fees": fees,
            "unrealized_pnl": total_unrealized,
        },
        "positions": positions,
        "closed_positions": [{
            "position_id": r[0], "symbol": r[1], "market_type": r[2],
            "direction": r[3], "entry_time": r[4], "entry_price": r[5],
            "exit_time": r[6], "exit_price": r[7], "quantity": r[8],
            "net_pnl": r[9], "close_reason": r[10], "total_fees": r[11],
        } for r in closed_rows],
    }
