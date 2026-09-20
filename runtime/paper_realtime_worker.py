"""Real-time PAPER position manager and live candidate subscription."""
from __future__ import annotations

import asyncio
import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path

from database.runtime_store import connect
from paper_trading.crypto_profit_agent import DB_NAME, FORWARD_COHORT, ensure_tables, run_behavior_selection
from paper_trading.exchange_metadata import load_cache, rules_for
from paper_trading.futures_costs import fetch_funding_history_via_postgres
from paper_trading.governance import validate_agent_authority
from paper_trading import experience_memory, position_management, realtime_market, trade_learning


HEALTH_PATH = Path(os.getenv(
    "AGCA_PAPER_REALTIME_HEALTH", "/var/lib/agca/paper-realtime-health.json"
))


def now():
    return datetime.now(timezone.utc)


def write_health(status, **details):
    payload = {"status": status, "at": now().isoformat(), **details,
               "execution_mode": "PAPER_ONLY", "real_order_allowed": False}
    HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = HEALTH_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, HEALTH_PATH)


async def run():
    validate_agent_authority(
        execution_mode=os.getenv("AGCA_EXECUTION_MODE", "PAPER_ONLY"),
        policy_version="PAPER_OPPORTUNITY_POLICY_V3_IMMEDIATE_EXECUTION",
        initial_capital=float(os.getenv("AGCA_PAPER_INITIAL_USDT", "10000")),
    )
    max_metadata_age = float(os.getenv(
        "AGCA_EXCHANGE_METADATA_MAX_AGE_SECONDS", "7200"
    ))
    metadata = {
        "USD_M_PERPETUAL": load_cache(
            os.getenv(
                "AGCA_EXCHANGE_METADATA_CACHE",
                "/var/lib/agca/exchange-info-usdm.json",
            ),
            "USD_M_PERPETUAL", max_age_seconds=max_metadata_age,
        ),
        "SPOT": load_cache(
            os.getenv(
                "AGCA_SPOT_EXCHANGE_METADATA_CACHE",
                "/var/lib/agca/exchange-info-spot.json",
            ),
            "SPOT", max_age_seconds=max_metadata_age,
        ),
    }
    con = connect(DB_NAME, timeout=30)
    ensure_tables(con)
    position_management.ensure_tables(con)
    stop_event = asyncio.Event()
    last_health_at = None
    selection_task = None
    selection_pending = False
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop_event.set)

    def symbols():
        position_management.initialize_open_positions(con, now())
        open_positions = [tuple(row) for row in con.execute(
            """SELECT symbol,market_type FROM paper_agent_positions
            WHERE status='OPEN' ORDER BY symbol"""
        ).fetchall()]
        # Keep live subscriptions active without open positions. Candidate symbols
        # are discovered from recent eligible market observations, not a fixed
        # coin allowlist. Open positions are never displaced by the cap.
        candidates = con.execute("""
            SELECT DISTINCT symbol FROM market_behavior_minute_features
            WHERE eligible_for_learning=1
              AND minute_end_timestamp_ms >= ?
            ORDER BY minute_end_timestamp_ms DESC LIMIT 100
        """, (int(now().timestamp() * 1000) - 180000,)).fetchall()
        return sorted(set(open_positions) | {(row[0], "SPOT") for row in candidates})

    def health(state, error):
        write_health(state, error=error, active_symbols=symbols())

    async def selection_once():
        nonlocal selection_pending
        try:
            while selection_pending and not stop_event.is_set():
                selection_pending = False
                try:
                    result = await asyncio.to_thread(run_behavior_selection)
                    write_health("CONNECTED", selection_trigger="LIVE_EVENT",
                                 decision_status=result.get("decision_status"),
                                 selected=result.get("selected", 0))
                except Exception as exc:
                    write_health("DEGRADED", error=f"LIVE_SELECTION_{type(exc).__name__}")
        finally:
            selection_pending = False

    async def on_state(state, observed_ms):
        nonlocal last_health_at, selection_task, selection_pending
        observed_at = datetime.fromtimestamp(observed_ms / 1000, tz=timezone.utc)
        rule = rules_for(metadata[state.market_type], state.symbol)
        if rule is None:
            # Missing rules must leave durable rejection evidence for an exit,
            # not just an overwritten health-file message.
            position_management.process_market_state(con, state, observed_at, None)
            write_health("DEGRADED", error="LIVE_SYMBOL_RULES_UNAVAILABLE",
                         symbol=state.symbol)
            return
        result = position_management.process_market_state(
            con, state, observed_at, rule,
        )
        # Exit management takes priority over entry selection on each event.
        # Coalesce events arriving during a selection into one fresh rerun.
        selection_pending = True
        if selection_task is None or selection_task.done():
            selection_task = asyncio.create_task(selection_once())
        if result.get("status") == "POSITION_CLOSED":
            reviews = experience_memory.review_closed_positions(con, observed_at)
            learning = trade_learning.run(con, FORWARD_COHORT, observed_at)
            result["new_experience_reviews"] = reviews
            result["learning_status"] = learning["status"]
        if result.get("status") not in {"HOLD", "NO_PRICE", "NO_OPEN_POSITION",
                                         "DUPLICATE_OR_OLD_EVENT"}:
            write_health("CONNECTED", symbol=state.symbol, last_action=result)
            last_health_at = observed_at
        elif last_health_at is None or (observed_at - last_health_at).total_seconds() >= 10:
            write_health("CONNECTED", symbol=state.symbol, last_action={"status": "HOLD"})
            last_health_at = observed_at

    def maintenance_once():
        maintenance_con = connect(DB_NAME, timeout=30)
        try:
            ensure_tables(maintenance_con)
            reconciled = position_management.reconcile_pending_funding(
                maintenance_con,
                lambda **kwargs: fetch_funding_history_via_postgres(
                    database_url=os.getenv("AGCA_REMOTE_DATABASE_URL", ""), **kwargs,
                ),
                now(),
            )
            if reconciled["resolved"]:
                experience_memory.review_closed_positions(maintenance_con, now())
                trade_learning.run(maintenance_con, FORWARD_COHORT, now())
            return reconciled
        finally:
            maintenance_con.close()

    async def maintenance():
        while not stop_event.is_set():
            try:
                await asyncio.to_thread(maintenance_once)
            except Exception as exc:
                write_health("DEGRADED", error=f"FUNDING_RECONCILIATION_{type(exc).__name__}")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    write_health("STARTING", active_symbols=symbols())
    try:
        await asyncio.gather(
            realtime_market.stream(symbols, on_state, stop_event, health),
            maintenance(),
        )
    finally:
        con.close()
        write_health("STOPPED")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
