"""Live-source, in-memory acceptance test for real-time PAPER management."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import ssl
import time
from datetime import datetime, timedelta, timezone

import certifi
import websockets

from paper_trading.exchange_metadata import load_cache, rules_for
from paper_trading.position_management import (
    ensure_tables, initialize_open_positions, process_market_state,
)
from paper_trading.realtime_market import WS_URL, apply_message, subscription_args


async def live_state(symbol, timeout=15):
    context = ssl.create_default_context(cafile=certifi.where())
    states = {}
    async with websockets.connect(
        WS_URL, ssl=context, open_timeout=10, ping_interval=20, ping_timeout=10,
    ) as socket:
        await socket.send(json.dumps({
            "id": str(int(time.time() * 1000)), "op": "subscribe",
            "args": subscription_args([symbol]),
        }))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = await asyncio.wait_for(socket.recv(), timeout=3)
            received = int(time.time() * 1000)
            for state in apply_message(states, json.loads(raw), received):
                if (state.last_price and state.visible_quote_volume
                        and state.book_imbalance is not None
                        and state.taker_imbalance is not None):
                    return state
    raise RuntimeError("COMPLETE_REALTIME_STATE_UNAVAILABLE")


def memory_database(state):
    market_at = datetime.fromtimestamp(state.market_timestamp_ms / 1000, tz=timezone.utc)
    entry_at = market_at - timedelta(seconds=1)
    entry_price = state.last_price / 1.02
    quantity, notional, entry_fee = 1000.0 / entry_price, 1000.0, 0.5
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE paper_agent_portfolio(
            portfolio_id INTEGER PRIMARY KEY,cash REAL,realized_pnl REAL,total_fees REAL);
        CREATE TABLE paper_agent_positions(
            position_id INTEGER PRIMARY KEY,symbol TEXT,direction TEXT,status TEXT,
            entry_time TEXT,entry_price REAL,quantity REAL,notional REAL,entry_fee REAL,
            due_time TEXT,exit_fee REAL DEFAULT 0,exit_spread_cost REAL DEFAULT 0,
            exit_slippage_cost REAL DEFAULT 0,funding_cost REAL DEFAULT 0,
            funding_source TEXT,gross_pnl REAL,net_pnl REAL,exit_time TEXT,
            exit_price REAL,close_reason TEXT);
        CREATE TABLE asset_risk_snapshots(
            id INTEGER PRIMARY KEY,symbol TEXT,created_at TEXT,
            volatility_stop_reference_pct REAL);
    """)
    con.execute("INSERT INTO paper_agent_portfolio VALUES(1,?,?,?)",
                (10_000 - notional - entry_fee, 0, entry_fee))
    con.execute("""INSERT INTO paper_agent_positions(
        position_id,symbol,direction,status,entry_time,entry_price,quantity,
        notional,entry_fee,due_time
    ) VALUES(1,?,'LONG','OPEN',?,?,?,?,?,?)""", (
        state.symbol, entry_at.isoformat(), entry_price, quantity, notional,
        entry_fee, (entry_at + timedelta(hours=4)).isoformat(),
    ))
    con.execute("INSERT INTO asset_risk_snapshots VALUES(1,?,?,2.0)",
                (state.symbol, (entry_at - timedelta(seconds=1)).isoformat()))
    ensure_tables(con)
    return con, market_at


async def run(symbol):
    state = await live_state(symbol)
    metadata = load_cache(
        os.getenv("AGCA_EXCHANGE_METADATA_CACHE", "/var/lib/agca/exchange-info-usdm.json"),
        "USD_M_PERPETUAL",
        max_age_seconds=float(os.getenv("AGCA_EXCHANGE_METADATA_MAX_AGE_SECONDS", "7200")),
    )
    rules = rules_for(metadata, symbol)
    if rules is None:
        raise RuntimeError("LIVE_SYMBOL_RULES_UNAVAILABLE")
    con, market_at = memory_database(state)
    try:
        initialized = initialize_open_positions(con, market_at)
        result = process_market_state(con, state, market_at, rules)
        row = con.execute("""SELECT remaining_quantity,current_stop_price,
            real_order_allowed FROM paper_position_management""").fetchone()
        events = con.execute("""SELECT event_type,reason,real_order_allowed
            FROM paper_position_events ORDER BY event_id""").fetchall()
        if initialized["initialized"] != 1 or result["status"] != "POSITION_REDUCED":
            raise RuntimeError(
                "REALTIME_MANAGEMENT_ACCEPTANCE_FAILED " + json.dumps({
                    "initialized": initialized, "result": result,
                    "price": state.last_price,
                    "visible_quote_volume": state.visible_quote_volume,
                    "events": events,
                }, sort_keys=True)
            )
        if not row or row[2] != 0 or any(event[2] != 0 for event in events):
            raise RuntimeError("REAL_ORDER_GATE_VIOLATION")
        return {
            "status": "PASS", "symbol": symbol,
            "market_at": market_at.isoformat(), "reference_price": state.last_price,
            "book_imbalance": state.book_imbalance,
            "taker_imbalance": state.taker_imbalance,
            "management_result": result, "events": events,
            "authoritative_database_mutated": False, "real_order_allowed": False,
        }
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.symbol.upper())), sort_keys=True))


if __name__ == "__main__":
    main()
