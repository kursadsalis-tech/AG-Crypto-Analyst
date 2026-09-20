"""Single-process real-time manager for open PAPER positions."""
from __future__ import annotations

import asyncio
import json
import os
import signal
from datetime import datetime, timezone
from pathlib import Path

from database.runtime_store import connect
from paper_trading.crypto_profit_agent import (
    DB_NAME, FORWARD_COHORT, ensure_tables, fill_pending_from_live_state,
    select_new_positions,
)
from paper_trading.exchange_metadata import load_cache, rules_for
from paper_trading.futures_costs import fetch_funding_history_via_postgres
from paper_trading.governance import validate_agent_authority
from paper_trading import experience_memory, position_management, realtime_market, trade_learning
from paper_trading import strategy_allocator


HEALTH_PATH = Path(os.getenv(
    "AGCA_PAPER_REALTIME_HEALTH", "/var/lib/agca/paper-realtime-health.json"
))
MARKET_STATE_PATH = Path(os.getenv(
    "AGCA_PAPER_REALTIME_MARKET_STATE",
    "/var/lib/agca/paper-realtime-market-state.json",
))
BRAIN_STATE_PATH = Path(os.getenv(
    "AGCA_PAPER_REALTIME_BRAIN_STATE",
    "/var/lib/agca/paper-realtime-brain-state.json",
))
BRAIN_TIMELINE_PATH = Path(os.getenv(
    "AGCA_PAPER_REALTIME_BRAIN_TIMELINE",
    "/var/lib/agca/paper-realtime-brain-timeline.json",
))


def now():
    return datetime.now(timezone.utc)


def _atomic_json_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def write_health(status, **details):
    payload = {"status": status, "at": now().isoformat(), **details,
               "execution_mode": "PAPER_ONLY", "real_order_allowed": False}
    _atomic_json_write(HEALTH_PATH, payload)


def write_brain_state(payload):
    """Best-effort X-ray of the canonical worker; never a decision dependency."""
    try:
        event = {
            "at": now().isoformat(), "execution_mode": "PAPER_ONLY",
            "real_order_allowed": False, **payload,
        }
        _atomic_json_write(BRAIN_STATE_PATH, event)
        try:
            previous = json.loads(BRAIN_TIMELINE_PATH.read_text(encoding="utf-8"))
            timeline = previous if isinstance(previous, list) else []
        except (OSError, ValueError, TypeError):
            timeline = []
        signature = (
            event.get("phase"), event.get("observed_symbol"),
            event.get("decision"), (event.get("scan") or {}).get("candidate_count"),
            (event.get("scan") or {}).get("selected_count"),
        )
        if not timeline or tuple(timeline[-1].get("signature", [])) != signature:
            timeline.append({
                "at": event["at"], "phase": event.get("phase"),
                "message": event.get("message"),
                "symbol": event.get("observed_symbol"),
                "market_type": event.get("observed_market_type"),
                "price": event.get("observed_price"),
                "decision": event.get("decision"),
                "scan": event.get("scan"),
                "signature": list(signature),
            })
            _atomic_json_write(BRAIN_TIMELINE_PATH, timeline[-100:])
    except Exception:
        pass


def write_market_state(states):
    """Best-effort read-only telemetry; never an execution dependency."""
    payload = {
        "at": now().isoformat(),
        "execution_mode": "PAPER_ONLY",
        "real_order_allowed": False,
        "live_market": states,
    }
    try:
        _atomic_json_write(MARKET_STATE_PATH, payload)
    except Exception:
        # Observability must never stop or degrade canonical PAPER execution.
        pass


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
    last_selection_attempt_at = None
    live_states = {}
    last_market_state_publish_at = 0.0

    def live_state_payload(state, observed_ms):
        """Operator view of the exact normalized market state used by PAPER."""
        return {
            "symbol": state.symbol,
            "market_type": state.market_type,
            "last_price": state.last_price,
            "best_bid": state.best_bid,
            "best_ask": state.best_ask,
            "quote_source": state.quote_source,
            "market_timestamp_ms": state.market_timestamp_ms,
            "quote_timestamp_ms": state.quote_timestamp_ms,
            "liquidity_timestamp_ms": state.liquidity_timestamp_ms,
            "visible_quote_volume": state.visible_quote_volume,
            "bid_quote_volume": state.bid_quote_volume,
            "ask_quote_volume": state.ask_quote_volume,
            "book_imbalance": state.book_imbalance,
            "taker_imbalance": state.taker_imbalance,
            "observed_timestamp_ms": observed_ms,
        }

    def live_state_snapshot():
        return [live_states[key] for key in sorted(live_states)]

    def publish_live_state(state, observed_ms):
        nonlocal last_market_state_publish_at
        live_states[(state.symbol, state.market_type)] = live_state_payload(
            state, observed_ms
        )
        # Keep every in-memory state current, but cap disk telemetry at 2 Hz.
        # Observability is never allowed to become a trading-path dependency.
        current = loop.time()
        if current - last_market_state_publish_at < 0.5:
            return
        last_market_state_publish_at = current
        # This is deliberately the same state the canonical worker consumes:
        # no second feed, price authority, database, or trading lifecycle.
        write_market_state(live_state_snapshot())

    last_selection_attempt_at = None
    last_learning_at = None
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop_event.set)

    def symbols():
        position_management.initialize_open_positions(con, now())
        active = {tuple(row) for row in con.execute(
            """SELECT symbol,market_type FROM paper_agent_positions
            WHERE status='OPEN' ORDER BY symbol"""
        ).fetchall()}
        # Keep observing the latest completed behavior universe even when the
        # portfolio is empty; otherwise no live event could ever trigger the
        # next decision/entry.
        latest = con.execute(
            "SELECT MAX(minute_end_timestamp_ms) FROM market_behavior_minute_features"
        ).fetchone()[0]
        monitored = set()
        if latest is not None:
            for (instrument,) in con.execute(
                """SELECT DISTINCT instrument FROM market_behavior_minute_features
                WHERE minute_end_timestamp_ms=? AND eligible_for_learning=1""",
                (latest,),
            ).fetchall():
                symbol = str(instrument).replace("-USDT-SWAP", "USDT").replace("-", "")
                market_type = (
                    "USD_M_PERPETUAL"
                    if str(instrument).endswith("-SWAP") else "SPOT"
                )
                monitored.add((symbol, market_type))
        return sorted(active | monitored)

    def health(state, error):
        write_health(
            state, error=error, active_symbols=symbols(),
        )

    async def on_state(state, observed_ms):
        nonlocal last_health_at, last_selection_attempt_at, last_learning_at
        observed_at = datetime.fromtimestamp(observed_ms / 1000, tz=timezone.utc)
        publish_live_state(state, observed_ms)
        # One authoritative live callback now drives decision -> PAPER entry ->
        # open-position management. The decision core still consumes only
        # completed causal minute evidence; the fill uses this exact live state.
        if (last_selection_attempt_at is None or
                (observed_at - last_selection_attempt_at).total_seconds() >= 1.0):
            selected = select_new_positions(con, observed_at, metadata)
            latest_scan = con.execute(
                """SELECT core_status,candidate_count,selected_count,
                          active_position_count,capacity,available_exposure,
                          reason_counts_json
                   FROM paper_behavior_selection_runs
                   ORDER BY run_id DESC LIMIT 1"""
            ).fetchone()
            write_brain_state({
                "phase": "OPPORTUNITY_SCAN",
                "message": "Piyasa taranıyor; fırsatlar değerlendiriliyor.",
                "observed_symbol": state.symbol,
                "observed_market_type": state.market_type,
                "observed_price": state.last_price,
                "monitored_symbols": len(symbols()),
                "scan": ({
                    "core_status": latest_scan[0],
                    "candidate_count": latest_scan[1],
                    "selected_count": latest_scan[2],
                    "active_position_count": latest_scan[3],
                    "capacity": latest_scan[4],
                    "available_exposure": latest_scan[5],
                    "reason_counts": json.loads(latest_scan[6] or "{}"),
                } if latest_scan else None),
                "decision": (
                    "NEW_POSITION_SELECTED" if selected else "CONTINUE_SCANNING"
                ),
            })
            last_selection_attempt_at = observed_at
        # Learning is part of the live lifecycle, not a separate research job.
        # It remains PAPER-only and evidence-gated; challengers get no real-order
        # authority and cannot silently mutate the active policy.
        if (last_learning_at is None or
                (observed_at - last_learning_at).total_seconds() >= 60.0):
            learning = trade_learning.run(con, FORWARD_COHORT, observed_at)
            strategy_allocator.rebalance(con, observed_at)
            last_learning_at = observed_at
        live_filled, live_cancelled = fill_pending_from_live_state(
            con, observed_at, state, metadata
        )
        if live_filled or live_cancelled:
            position_management.initialize_open_positions(con, observed_at)
        rule = rules_for(metadata[state.market_type], state.symbol)
        if rule is None:
            # Missing exchange rules must still reach the position manager so an
            # open position records durable fail-closed exit evidence; a health
            # message alone is not sufficient audit state.
            position_management.process_market_state(con, state, observed_at, None)
            write_health("DEGRADED", error="LIVE_SYMBOL_RULES_UNAVAILABLE",
                         symbol=state.symbol)
            return
        result = position_management.process_market_state(
            con, state, observed_at, rule,
        )
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

    async def heartbeat():
        # Liveness must not depend on receiving a market callback. A quiet or
        # reconnecting stream can otherwise make a healthy worker look stale.
        while not stop_event.is_set():
            write_health(
                "CONNECTED", active_symbols=symbols(), heartbeat=True,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass

    write_health(
        "STARTING", active_symbols=symbols(),
    )
    try:
        await asyncio.gather(
            realtime_market.stream(symbols, on_state, stop_event, health),
            maintenance(),
            heartbeat(),
        )
    finally:
        con.close()
        write_health("STOPPED")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
