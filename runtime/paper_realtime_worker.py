"""Single-process real-time manager for open PAPER positions."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import tempfile
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
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
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(payload, sort_keys=True) + "\n")
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def heartbeat_market_status(active_symbols, last_quote_received_ms, wall_clock_ms):
    """Report market freshness, not merely whether the Python process is alive."""
    if not active_symbols:
        return "IDLE", None
    if last_quote_received_ms is None:
        return "DEGRADED", "NO_LIVE_QUOTES_YET"
    age_ms = wall_clock_ms - last_quote_received_ms
    if age_ms < -500 or age_ms > realtime_market.MAX_EVENT_AGE_MS:
        return "DEGRADED", "LIVE_QUOTES_STALE"
    return "CONNECTED", None


def stale_quote_symbols(active_symbols, live_states, wall_clock_ms, max_age_ms=30_000):
    """Return subscribed instruments without a recent valid executable quote."""
    stale = []
    for symbol, market_type in active_symbols:
        stamp = live_states.get((symbol, market_type), {}).get("quote_timestamp_ms")
        if (
            stamp is None or wall_clock_ms - stamp > max_age_ms
            or wall_clock_ms - stamp < -500
        ):
            stale.append((symbol, market_type))
    return stale


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
    decision_executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="agca-paper-decision"
    )
    universe_executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="agca-symbol-universe"
    )
    decision_local = threading.local()
    universe_local = threading.local()
    stop_event = asyncio.Event()
    last_health_at = None
    last_quote_received_ms = None
    quote_metrics = {
        "SPOT": realtime_market.new_quote_metrics(),
        "USD_M_PERPETUAL": realtime_market.new_quote_metrics(),
    }
    last_action_status = None
    last_selection_attempt_at = None
    live_states = {}
    last_market_state_publish_at = 0.0
    latest_execution_states = {}
    latest_execution_lock = threading.Lock()
    pending_decision_states = {}
    pending_decision_order = deque()
    pending_decision_keys = set()
    decision_wakeup = asyncio.Event()
    symbol_cache_lock = threading.Lock()
    cached_symbols = ()

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
            "trade_timestamp_ms": state.trade_timestamp_ms,
            "visible_quote_volume": state.visible_quote_volume,
            "bid_quote_volume": state.bid_quote_volume,
            "ask_quote_volume": state.ask_quote_volume,
            "book_imbalance": state.book_imbalance,
            "taker_imbalance": state.taker_imbalance,
            "observed_timestamp_ms": observed_ms,
        }

    def live_state_snapshot():
        # Retired symbols must not remain indefinitely in live dashboard data.
        active_keys = set(symbols())
        return [live_states[key] for key in sorted(live_states)
                if key in active_keys]

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

    def load_symbols(db):
        position_management.initialize_open_positions(db, now())
        active = {tuple(row) for row in db.execute(
            """SELECT symbol,market_type FROM paper_agent_positions
            WHERE status='OPEN' ORDER BY symbol"""
        ).fetchall()}
        latest = db.execute(
            "SELECT MAX(minute_end_timestamp_ms) FROM market_behavior_minute_features"
        ).fetchone()[0]
        monitored = set()
        if latest is not None:
            for (instrument,) in db.execute(
                """SELECT DISTINCT instrument FROM market_behavior_minute_features
                WHERE minute_end_timestamp_ms=? AND eligible_for_learning=1""",
                (latest,),
            ).fetchall():
                symbol = str(instrument).replace(
                    "-USDT-SWAP", "USDT"
                ).replace("-", "")
                market_type = (
                    "USD_M_PERPETUAL"
                    if str(instrument).endswith("-SWAP") else "SPOT"
                )
                monitored.add((symbol, market_type))
        return tuple(sorted(active | monitored))

    def symbols():
        # Pure in-memory hot-path read. No SQLite work is allowed here because
        # Binance calls this from subscription/health tasks on the event loop.
        with symbol_cache_lock:
            return list(cached_symbols)

    def quote_ingest_snapshot(wall_clock_ms):
        """Separate exchange receiver progress from PAPER dispatch progress."""
        result = {}
        for market_type, metric in quote_metrics.items():
            last_received = metric["last_received_ms"]
            symbol_received = metric["received_by_symbol_ms"].copy()
            result[market_type] = {
                "received_total": metric["received_total"],
                "accepted_total": metric["accepted_total"],
                "stale_received_total": metric["stale_received_total"],
                "stale_dispatch_total": metric["stale_dispatch_total"],
                "dispatched_total": metric["dispatched_total"],
                "last_receive_lag_ms": metric["last_receive_lag_ms"],
                "max_receive_lag_ms": metric["max_receive_lag_ms"],
                "receiver_age_ms": (
                    wall_clock_ms - last_received
                    if last_received is not None else None
                ),
                "received_by_symbol_age_ms": {
                    symbol: wall_clock_ms - received
                    for symbol, received in symbol_received.items()
                },
            }
        return result

    def emit_health(status, **details):
        """One canonical status contract for stream, decision and heartbeat."""
        nonlocal last_action_status
        active = symbols()
        wall_clock_ms = int(now().timestamp() * 1000)
        feed_status, feed_error = heartbeat_market_status(
            active, last_quote_received_ms, wall_clock_ms,
        )
        # A recent BTC quote must not conceal an ETH subscription stalled for
        # minutes. Allow quiet symbols 30 seconds; expose affected symbols.
        stalled_symbols = stale_quote_symbols(active, live_states, wall_clock_ms)
        if status == "CONNECTED":
            status = feed_status
            if feed_error:
                details["error"] = feed_error
            elif stalled_symbols:
                status = "DEGRADED"
                details["error"] = "LIVE_SYMBOL_QUOTES_STALE"
        action = details.get("last_action")
        if isinstance(action, dict) and action.get("status"):
            last_action_status = str(action["status"])
        details.update({
            "active_symbols": active,
            "stale_symbols": stalled_symbols,
            "last_quote_received_ms": last_quote_received_ms,
            "quote_receive_age_ms": (
                wall_clock_ms - last_quote_received_ms
                if last_quote_received_ms is not None else None
            ),
            "action": last_action_status,
            "quote_ingest": quote_ingest_snapshot(wall_clock_ms),
        })
        write_health(status, **details)

    def universe_connection():
        db = getattr(universe_local, "connection", None)
        if db is None:
            db = connect(DB_NAME, timeout=30)
            ensure_tables(db)
            position_management.ensure_tables(db)
            universe_local.connection = db
        return db

    def refresh_symbol_cache_once():
        nonlocal cached_symbols
        refreshed = load_symbols(universe_connection())
        with symbol_cache_lock:
            cached_symbols = refreshed
        return refreshed

    async def refresh_symbol_cache():
        while not stop_event.is_set():
            try:
                await loop.run_in_executor(
                    universe_executor, refresh_symbol_cache_once
                )
            except Exception as exc:
                emit_health(
                    "DEGRADED",
                    error=f"SYMBOL_UNIVERSE_REFRESH_{type(exc).__name__}",
                )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    def health(state, error):
        emit_health(
            state, error=error, active_symbols=symbols(),
        )

    def decision_connection():
        db = getattr(decision_local, "connection", None)
        if db is None:
            db = connect(DB_NAME, timeout=30)
            ensure_tables(db)
            position_management.ensure_tables(db)
            decision_local.connection = db
        return db

    def latest_execution_state(state, observed_ms):
        key = (state.symbol, state.market_type)
        with latest_execution_lock:
            return latest_execution_states.get(key, (state, observed_ms))

    def process_state(state, observed_ms):
        """Run DB/decision/PAPER work off the websocket event loop."""
        nonlocal last_health_at, last_selection_attempt_at, last_learning_at
        work_con = decision_connection()
        observed_at = datetime.fromtimestamp(
            observed_ms / 1000, tz=timezone.utc
        )
        # The decision core still consumes completed causal minute evidence;
        # this exact latest normalized state is used for fill/management.
        if (
            last_selection_attempt_at is None
            or (observed_at - last_selection_attempt_at).total_seconds() >= 1.0
        ):
            selection_result = {}
            selected = select_new_positions(
                work_con, observed_at, metadata, telemetry=selection_result
            )
            latest_scan = work_con.execute(
                """SELECT core_status,candidate_count,selected_count,
                          active_position_count,capacity,available_exposure,
                          reason_counts_json
                   FROM paper_behavior_selection_runs
                   ORDER BY run_id DESC LIMIT 1"""
            ).fetchone()
            brain_state, _ = latest_execution_state(state, observed_ms)
            write_brain_state({
                "phase": "OPPORTUNITY_SCAN",
                "message": "Piyasa taranıyor; fırsatlar değerlendiriliyor.",
                "observed_symbol": brain_state.symbol,
                "observed_market_type": brain_state.market_type,
                "observed_price": brain_state.last_price,
                "monitored_symbols": len(load_symbols(work_con)),
                "scan": ({
                    "core_status": latest_scan[0],
                    "candidate_count": latest_scan[1],
                    "selected_count": latest_scan[2],
                    "active_position_count": latest_scan[3],
                    "capacity": latest_scan[4],
                    "available_exposure": latest_scan[5],
                    "reason_counts": json.loads(latest_scan[6] or "{}"),
                } if latest_scan else None),
                "candidates": selection_result.get("candidates", []),
                "open_positions": selection_result.get("open_positions", []),
                "portfolio_capacity": selection_result.get(
                    "portfolio_capacity", {}
                ),
                "rejection_reasons": selection_result.get(
                    "rejection_reasons", {}
                ),
                "decision": (
                    "NEW_POSITION_SELECTED"
                    if selected else "CONTINUE_SCANNING"
                ),
            })
            # Cadence is based on completion time, not the event that started
            # a potentially slow scan. Otherwise a multi-second scan retriggers
            # immediately and starves position/fill management.
            last_selection_attempt_at = now()

        if (
            last_learning_at is None
            or (observed_at - last_learning_at).total_seconds() >= 60.0
        ):
            trade_learning.run(work_con, FORWARD_COHORT, observed_at)
            strategy_allocator.rebalance(work_con, observed_at)
            last_learning_at = now()

        # A scan can take seconds. Never fill/manage against the state that
        # started that scan; refresh from the ingest cache immediately before
        # any PAPER execution or position-management action.
        execution_state, execution_observed_ms = latest_execution_state(
            state, observed_ms
        )
        execution_stamp_ms = execution_state.market_timestamp_ms
        wall_clock_ms = int(now().timestamp() * 1000)
        if (
            execution_stamp_ms is None
            or wall_clock_ms - execution_stamp_ms
            > realtime_market.MAX_EVENT_AGE_MS
        ):
            emit_health(
                "DEGRADED",
                error="LIVE_STATE_STALE_BEFORE_EXECUTION",
                symbol=execution_state.symbol,
            )
            return
        execution_at = datetime.fromtimestamp(
            execution_observed_ms / 1000, tz=timezone.utc
        )

        live_filled, live_cancelled = fill_pending_from_live_state(
            work_con, execution_at, execution_state, metadata
        )
        if live_filled or live_cancelled:
            position_management.initialize_open_positions(
                work_con, execution_at
            )

        rule = rules_for(
            metadata[execution_state.market_type], execution_state.symbol
        )
        if rule is None:
            position_management.process_market_state(
                work_con, execution_state, execution_at, None
            )
            emit_health(
                "DEGRADED",
                error="LIVE_SYMBOL_RULES_UNAVAILABLE",
                symbol=execution_state.symbol,
            )
            return

        result = position_management.process_market_state(
            work_con, execution_state, execution_at, rule
        )
        if result.get("status") == "POSITION_CLOSED":
            reviews = experience_memory.review_closed_positions(
                work_con, execution_at
            )
            learning = trade_learning.run(
                work_con, FORWARD_COHORT, execution_at
            )
            result["new_experience_reviews"] = reviews
            result["learning_status"] = learning["status"]

        if result.get("status") not in {
            "HOLD", "NO_PRICE", "NO_OPEN_POSITION", "DUPLICATE_OR_OLD_EVENT"
        }:
            emit_health(
                "CONNECTED", symbol=execution_state.symbol, last_action=result
            )
            last_health_at = execution_at
        elif (
            last_health_at is None
            or (observed_at - last_health_at).total_seconds() >= 10
        ):
            emit_health(
                "CONNECTED",
                symbol=execution_state.symbol,
                last_action={"status": "HOLD"},
            )
            last_health_at = observed_at

    async def on_state(state, observed_ms):
        nonlocal last_quote_received_ms
        last_quote_received_ms = observed_ms
        # True ingest hot path: publish/cache the newest state and return.
        # Decision/PAPER work is consumed separately and can never block the
        # websocket receiver or telemetry freshness.
        publish_live_state(state, observed_ms)
        key = (state.symbol, state.market_type)
        with latest_execution_lock:
            latest_execution_states[key] = (state, observed_ms)
        pending_decision_states[key] = (state, observed_ms)
        if key not in pending_decision_keys:
            pending_decision_order.append(key)
            pending_decision_keys.add(key)
        decision_wakeup.set()

    async def decision_loop():
        while not stop_event.is_set():
            if not pending_decision_order:
                decision_wakeup.clear()
                try:
                    await asyncio.wait_for(
                        decision_wakeup.wait(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue
            if not pending_decision_order:
                continue
            key = pending_decision_order.popleft()
            pending_decision_keys.discard(key)
            item = pending_decision_states.pop(key, None)
            if item is None:
                continue
            state, observed_ms = item
            stamp_ms = state.market_timestamp_ms
            wall_clock_ms = int(now().timestamp() * 1000)
            if (
                stamp_ms is None
                or wall_clock_ms - stamp_ms
                > realtime_market.MAX_EVENT_AGE_MS
            ):
                continue
            await loop.run_in_executor(
                decision_executor, process_state, state, observed_ms
            )

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
                emit_health("DEGRADED", error=f"FUNDING_RECONCILIATION_{type(exc).__name__}")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    async def heartbeat():
        # Report liveness independently; emit_health checks actual quote age
        # and coverage before permitting a CONNECTED label.
        while not stop_event.is_set():
            emit_health("CONNECTED", heartbeat=True)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass

    with symbol_cache_lock:
        cached_symbols = load_symbols(con)
    emit_health(
        "STARTING", active_symbols=symbols(),
    )
    try:
        await asyncio.gather(
            realtime_market.stream(
                symbols, on_state, stop_event, health, quote_metrics=quote_metrics,
            ),
            decision_loop(),
            refresh_symbol_cache(),
            maintenance(),
            heartbeat(),
        )
    finally:
        def close_decision_connection():
            db = getattr(decision_local, "connection", None)
            if db is not None:
                db.close()
                decision_local.connection = None

        def close_universe_connection():
            db = getattr(universe_local, "connection", None)
            if db is not None:
                db.close()
                universe_local.connection = None

        try:
            await loop.run_in_executor(
                decision_executor, close_decision_connection
            )
            await loop.run_in_executor(
                universe_executor, close_universe_connection
            )
        finally:
            decision_executor.shutdown(wait=True)
            universe_executor.shutdown(wait=True)
            con.close()
            emit_health("STOPPED")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
