"""Live-source, in-memory acceptance check for dynamic PAPER execution."""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from paper_trading import crypto_profit_agent as agent


def _candidate_row(symbol: str, planned: datetime):
    selected = planned - timedelta(minutes=15)
    due = planned + timedelta(hours=4)
    return (
        -abs(hash((symbol, planned.isoformat()))) % 2_000_000_000,
        agent.AGENT_VERSION, "PAPER_EXECUTION_READINESS_V1", symbol, 4, "LONG",
        "PENDING_ENTRY", agent.iso(selected), agent.iso(selected),
        agent.iso(planned), agent.iso(due), 25.0, 0.60, 0.60, 0.40, 1.0,
        "READINESS_ONLY", "0" * 64, "IN_MEMORY_DYNAMIC_EXECUTION_CANARY",
        "USD_M_PERPETUAL", agent.FORWARD_COHORT, 1, "READINESS_ONLY",
        "MARKET_BEHAVIOR_CORE", None,
    )


def check_symbol(symbol: str, planned: datetime):
    con = sqlite3.connect(":memory:")
    try:
        agent.ensure_tables(con)
        con.execute("""
            INSERT INTO paper_agent_positions(
                forecast_id,agent_version,policy_version,symbol,horizon_hours,
                direction,status,selected_at,as_of,planned_entry_time,due_time,
                notional,confidence,p_up,p_down,target_price,model_version,
                artifact_hash,selection_reason,market_type,validation_cohort,
                evidence_eligible,strategy_id,decision_source,decision_reference_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, _candidate_row(symbol, planned))
        filled, cancelled = agent.fill_pending(
            con, planned + timedelta(minutes=2), exchange_metadata=None
        )
        position = con.execute("""SELECT position_id,status,entry_time,entry_price,
            quantity,notional,entry_fee FROM paper_agent_positions""").fetchone()
        observation = con.execute("""SELECT source,payload_sha256,real_order_allowed
            FROM paper_execution_market_observations
            WHERE position_id=? AND purpose='ENTRY'""", (position[0],)).fetchone()
        portfolio = con.execute("""SELECT initial_capital,cash,total_fees
            FROM paper_agent_portfolio WHERE portfolio_id=1""").fetchone()
        expected_cash = float(portfolio[0]) - float(position[5]) - float(position[6])
        passed = bool(
            filled == 1 and cancelled == 0 and position[1] == "OPEN"
            and position[2] == agent.iso(planned)
            and float(position[3]) > 0 and float(position[4]) > 0
            and observation is not None and len(observation[1]) == 64
            and int(observation[2]) == 0
            and abs(float(portfolio[1]) - expected_cash) < 1e-8
            and abs(float(portfolio[2]) - float(position[6])) < 1e-8
        )
        return {
            "symbol": symbol, "passed": passed, "status": position[1],
            "entry_time": position[2], "source": observation[0] if observation else None,
            "real_order_allowed": int(observation[2]) if observation else None,
        }
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="CRVUSDT,DASHUSDT,UNIUSDT")
    args = parser.parse_args()
    symbols = tuple(dict.fromkeys(
        item.strip().upper() for item in args.symbols.split(",") if item.strip()
    ))
    planned = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    results = [check_symbol(symbol, planned) for symbol in symbols]
    payload = {
        "schema": "PAPER_DYNAMIC_EXECUTION_READINESS_V1",
        "planned_entry_time": agent.iso(planned),
        "authoritative_database_mutated": False,
        "real_orders_allowed": False,
        "results": results,
        "passed": bool(results) and all(item["passed"] for item in results),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if payload["passed"] else 1)


if __name__ == "__main__":
    main()
