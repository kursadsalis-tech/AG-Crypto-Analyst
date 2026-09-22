"""Single-process real-time manager for open PAPER positions and opportunities."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sqlite3
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from database.runtime_store import connect
from paper_trading.crypto_profit_agent import (
    DB_NAME, FORWARD_COHORT, cancel_expired_pending_entries, ensure_tables,
    fill_pending_from_live_state, select_new_positions,
)
from paper_trading.exchange_metadata import load_cache, rules_for
from paper_trading.futures_costs import fetch_funding_history_via_postgres
from paper_trading.governance import validate_agent_authority
from paper_trading import behavior_decision_core, experience_memory, position_management, realtime_market, trade_learning
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
WORKER_DB_TIMEOUT_SECONDS = max(
    1.0, float(os.getenv("AGCA_PAPER_DB_TIMEOUT_SECONDS", "5"))
)


def worker_connect(stop_requested=None):
    db = connect(DB_NAME, timeout=WORKER_DB_TIMEOUT_SECONDS)
    db.execute(
        f"PRAGMA busy_timeout={int(WORKER_DB_TIMEOUT_SECONDS * 1000)}"
    )
    if stop_requested is not None:
        db.set_progress_handler(lambda: int(stop_requested.is_set()), 1000)
    return db


async def run_until_stopped(stop_event, *coroutines):
    """Cancel idle sockets/backoff immediately when the service receives TERM."""
    tasks = [asyncio.create_task(coro) for coro in coroutines]
    stopped = asyncio.create_task(stop_event.wait())
    try:
        done, _ = await asyncio.wait(
            [*tasks, stopped], return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            if task is not stopped:
                task.result()
    finally:
        stop_event.set()
        for task in [*tasks, stopped]:
            task.cancel()
        await asyncio.gather(*tasks, stopped, return_exceptions=True)


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
            (event.get("observed_event_ms") or 0) // 5000,
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
                "observed_event_ms": event.get("observed_event_ms"),
                "context_window_start_ms": event.get("context_window_start_ms"),
                "decision_time_ms": event.get("decision_time_ms"),
                "rejection_reasons": event.get("rejection_reasons"),
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
    shutdown_requested = threading.Event()
    con = worker_connect(shutdown_requested)
    ensure_tables(con)
    position_management.ensure_tables(con)
    decision_executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="agca-paper-decision"
    )
    universe_executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="agca-symbol-universe"
    )
    learning_executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="agca-paper-learning"
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
    last_selection_by_symbol = {}
    rolling_states = {}
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
    pattern_cache_lock = threading.Lock()
    cached_patterns = behavior_decision_core.learned_patterns(con, now())
    cached_patterns_at = time.monotonic()

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
    loop = asyncio.get_running_loop()
    def request_stop():
        shutdown_requested.set()
        stop_event.set()

    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, request_stop)

    def load_symbols(db):
        position_management.initialize_open_positions(db, now())
        active = {tuple(row) for row in db.execute(
            """SELECT symbol,market_type FROM paper_agent_positions
            WHERE status IN ('OPEN','PENDING_ENTRY') ORDER BY symbol"""
        ).fetchall()}
        monitored = set()
        # The current, risk-covered Binance universe authorises subscriptions.
        # Historical minute evidence is learning material, never the discovery
        # clock or the subscription gate.
        try:
            risks = behavior_decision_core._latest_risk(db, now())
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            risks = {}
        for symbol, risk in risks.items():
            if (risk["allowed"] and symbol.endswith("USDT") and "-" not in symbol
                    and rules_for(metadata["USD_M_PERPETUAL"], symbol) is not None):
                monitored.add((symbol, "USD_M_PERPETUAL"))
        return tuple(sorted(active | monitored))

    def live_decision_context(state, observed_ms):
        """Build causal rolling context on the ingest loop without I/O."""
        key = (state.symbol, state.market_type)
        history = rolling_states.setdefault(key, deque(maxlen=1600))
        if state.last_price is None:
            return None
        if not history or observed_ms - history[-1][0] >= 200:
            history.append((observed_ms, state.last_price, state.taker_imbalance))
        if (state.best_bid is None or state.best_ask is None
                or state.book_imbalance is None or state.taker_imbalance is None):
            return None
        reference = next((sample for sample in reversed(history)
                          if sample[0] <= observed_ms - 5000), None)
        if reference is None or reference[1] <= 0:
            return None
        recent_flow = [sample[2] for sample in history
                       if sample[0] >= observed_ms - 30000 and sample[2] is not None]
        if not recent_flow:
            return None
        mid = (state.best_bid + state.best_ask) / 2
        return {
            "symbol": state.symbol, "market_type": state.market_type,
            "observed_event_ms": state.market_timestamp_ms,
            "quote_ms": state.quote_timestamp_ms,
            "window_start_ms": reference[0], "mid": mid,
            "book_imbalance": state.book_imbalance,
            "taker_imbalance": sum(recent_flow) / len(recent_flow),
            "spread_bps": 10000 * (state.best_ask - state.best_bid) / mid,
            "move_5s_pct": 100 * (mid / reference[1] - 1),
        }

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
            db = worker_connect(shutdown_requested)
            ensure_tables(db)
            position_management.ensure_tables(db)
            universe_local.connection = db
        return db

    def refresh_symbol_cache_once():
        nonlocal cached_symbols, cached_patterns, cached_patterns_at
        try:
            db = universe_connection()
            refreshed = load_symbols(db)
            refreshed_patterns = None
            current = time.monotonic()
            if current - cached_patterns_at >= 30.0:
                refreshed_patterns = behavior_decision_core.learned_patterns(db, now())
        except Exception:
            db = getattr(universe_local, "connection", None)
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    pass
                try:
                    db.close()
                except Exception:
                    pass
                universe_local.connection = None
            raise
        with symbol_cache_lock:
            cached_symbols = refreshed
        if refreshed_patterns is not None:
            with pattern_cache_lock:
                cached_patterns = refreshed_patterns
                cached_patterns_at = current
        return refreshed

    def live_patterns_snapshot():
        with pattern_cache_lock:
            return dict(cached_patterns)

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
            db = worker_connect(shutdown_requested)
            ensure_tables(db)
            position_management.ensure_tables(db)
            decision_local.connection = db
        return db

    def latest_execution_state(state, observed_ms):
        key = (state.symbol, state.market_type)
        with latest_execution_lock:
            return latest_execution_states.get(key, (state, observed_ms))

    def process_state(state, observed_ms, live_context=None):
        """Run DB/decision/PAPER work off the websocket event loop."""
        nonlocal last_health_at, last_selection_attempt_at
        work_con = decision_connection()
        observed_at = datetime.fromtimestamp(
            observed_ms / 1000, tz=timezone.utc
        )
        # Candidate evaluation is triggered by the live state. Rolling windows
        # add context; no minute-close record is needed.
        key = (state.symbol, state.market_type)
        if (
            live_context is not None
            and (last_selection_attempt_at is None
                 or (observed_at - last_selection_attempt_at).total_seconds() >= 1.0)
            and (key not in last_selection_by_symbol
                 or (observed_at - last_selection_by_symbol[key]).total_seconds() >= 30.0)
        ):
            decision_started_at = now()
            decision_started_ms = int(decision_started_at.timestamp() * 1000)
            event_ms = int(live_context.get("observed_event_ms") or 0)
            queue_delay_ms = decision_started_ms - event_ms
            if queue_delay_ms < -500 or queue_delay_ms > realtime_market.MAX_EVENT_AGE_MS:
                write_brain_state({
                    "phase": "OPPORTUNITY_SCAN",
                    "message": "Canlı fırsat olayı karar kuyruğunda eskidi; işlem yetkisi verilmedi.",
                    "observed_symbol": state.symbol,
                    "observed_market_type": state.market_type,
                    "observed_price": state.last_price,
                    "observed_event_ms": event_ms,
                    "context_window_start_ms": live_context.get("window_start_ms"),
                    "decision_time_ms": decision_started_ms,
                    "queue_delay_ms": queue_delay_ms,
                    "evaluation_ms": 0,
                    "decision": "LIVE_EVENT_STALE_BEFORE_DECISION",
                })
            else:
                selection_result = {}
                evaluation_started = time.monotonic()
                selected = select_new_positions(
                    work_con, decision_started_at, metadata,
                    telemetry=selection_result, live_context=live_context,
                    live_patterns=live_patterns_snapshot(), freshness_now=now,
                )
                evaluation_ms = int((time.monotonic() - evaluation_started) * 1000)
                latest_scan = work_con.execute(
                """SELECT core_status,candidate_count,selected_count,
                          active_position_count,capacity,available_exposure,
                          reason_counts_json
                   FROM paper_behavior_selection_runs
                   ORDER BY run_id DESC LIMIT 1"""
            ).fetchone()
                brain_state, _ = latest_execution_state(state, observed_ms)
                decision_finished_ms = int(now().timestamp() * 1000)
                write_brain_state({
                    "phase": "OPPORTUNITY_SCAN",
                    "message": "Piyasa taranıyor; fırsatlar değerlendiriliyor.",
                    "observed_symbol": brain_state.symbol,
                    "observed_market_type": brain_state.market_type,
                    "observed_price": brain_state.last_price,
                    "observed_event_ms": live_context["observed_event_ms"],
                    "context_window_start_ms": live_context["window_start_ms"],
                    "decision_time_ms": decision_finished_ms,
                    "queue_delay_ms": queue_delay_ms,
                    "evaluation_ms": evaluation_ms,
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
                if selected:
                    last_selection_by_symbol[key] = last_selection_attempt_at

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
            write_brain_state({
                "phase": "PAPER_EXECUTION",
                "message": "Canlı fiyatla PAPER giriş sonucu işlendi.",
                "observed_symbol": execution_state.symbol,
                "observed_market_type": execution_state.market_type,
                "observed_price": execution_state.last_price,
                "observed_event_ms": execution_stamp_ms,
                "decision_time_ms": int(now().timestamp() * 1000),
                "decision": "PAPER_FILLED" if live_filled else "PAPER_CANCELLED",
                "filled_count": live_filled, "cancelled_count": live_cancelled,
            })
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
            result["new_experience_reviews"] = reviews
            result["learning_status"] = "DEFERRED_TO_BACKGROUND_REVIEW"

        if result.get("status") not in {
            "HOLD", "NO_PRICE", "NO_OPEN_POSITION", "DUPLICATE_OR_OLD_EVENT"
        }:
            write_brain_state({
                "phase": "POSITION_MANAGEMENT",
                "message": "Açık PAPER pozisyon canlı fiyatla yönetildi.",
                "observed_symbol": execution_state.symbol,
                "observed_market_type": execution_state.market_type,
                "observed_price": execution_state.last_price,
                "observed_event_ms": execution_stamp_ms,
                "decision_time_ms": int(now().timestamp() * 1000),
                "decision": result.get("status"),
            })

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

    def learning_once():
        """Run non-authoritative learning off the execution path."""
        db = None
        try:
            db = worker_connect(shutdown_requested)
            trade_learning.run(db, FORWARD_COHORT, now())
            strategy_allocator.rebalance(db, now())
            return None
        except sqlite3.OperationalError as exc:
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    pass
            return f"LEARNING_SQLITE_{str(exc).upper().replace(' ', '_')}"
        except Exception as exc:
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    pass
            return f"LEARNING_{type(exc).__name__}"
        finally:
            if db is not None:
                db.close()

    def process_state_guarded(state, observed_ms, live_context=None):
        try:
            process_state(state, observed_ms, live_context)
            return None
        except Exception as exc:
            db = getattr(decision_local, "connection", None)
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    pass
                try:
                    db.close()
                except Exception:
                    pass
                decision_local.connection = None
            prefix = (
                "DECISION_SQLITE"
                if isinstance(exc, sqlite3.OperationalError)
                else "DECISION"
            )
            return f"{prefix}_{type(exc).__name__}:{exc}"

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
        pending_decision_states[key] = (state, observed_ms,
                                        live_decision_context(state, observed_ms))
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
            state, observed_ms, live_context = item
            stamp_ms = state.market_timestamp_ms
            wall_clock_ms = int(now().timestamp() * 1000)
            if (
                stamp_ms is None
                or wall_clock_ms - stamp_ms
                > realtime_market.MAX_EVENT_AGE_MS
            ):
                continue
            decision_error = await loop.run_in_executor(
                decision_executor, process_state_guarded, state, observed_ms,
                live_context,
            )
            if decision_error:
                emit_health("DEGRADED", error=decision_error)

    def maintenance_once():
        maintenance_con = worker_connect(shutdown_requested)
        try:
            # Startup already installed the schema; do not repeat DDL/backfill
            # in the recurring funding path.
            reconciled = position_management.reconcile_pending_funding(
                maintenance_con,
                lambda **kwargs: fetch_funding_history_via_postgres(
                    database_url=os.getenv("AGCA_REMOTE_DATABASE_URL", ""),
                    timeout_seconds=5, **kwargs,
                ),
                now(), stop_requested=shutdown_requested.is_set,
            )
            if reconciled["resolved"]:
                experience_memory.review_closed_positions(maintenance_con, now())
            return reconciled
        finally:
            maintenance_con.close()

    async def learning_loop():
        # Execution/fill/position management always has priority over research.
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=30)
            return
        except asyncio.TimeoutError:
            pass
        while not stop_event.is_set():
            learning_error = await loop.run_in_executor(
                learning_executor, learning_once
            )
            if learning_error:
                write_brain_state({
                    "phase": "LEARNING_REVIEW",
                    "message": "Öğrenme incelemesi ertelendi; işlem yolu etkilenmedi.",
                    "decision": learning_error,
                })
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

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

    expired_on_startup = cancel_expired_pending_entries(con, now())
    with symbol_cache_lock:
        cached_symbols = load_symbols(con)
    emit_health(
        "STARTING", active_symbols=symbols(),
    )
    try:
        await run_until_stopped(
            stop_event,
            realtime_market.stream(
                symbols, on_state, stop_event, health, quote_metrics=quote_metrics,
            ),
            decision_loop(),
            refresh_symbol_cache(),
            maintenance(),
            learning_loop(),
            heartbeat(),
        )
    finally:
        request_stop()

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
            learning_executor.shutdown(wait=True)
            con.close()
            emit_health("STOPPED")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
