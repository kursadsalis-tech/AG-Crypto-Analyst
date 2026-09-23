"""Single-process real-time manager for open PAPER positions."""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sqlite3
import string
import subprocess
import tempfile
import time
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from database.runtime_store import connect
from paper_trading.crypto_profit_agent import (
    DB_NAME, FORWARD_COHORT, cancel_expired_pending_entries, ensure_tables,
    fill_pending_from_live_state, select_new_positions,
)
from paper_trading.exchange_metadata import ExchangeMetadataError, load_cache, rules_for
from paper_trading.futures_costs import fetch_funding_history_via_postgres
from paper_trading.governance import validate_agent_authority
from paper_trading import position_management, realtime_market


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


def _local_sqlite_connection(timeout_seconds=30.0):
    """Plain local SQLite connection for already-initialized realtime hot paths.

    The canonical startup connection installs schemas/outbox triggers once.
    Repeated RuntimeConnection construction also runs outbox/trigger DDL and can
    block behind the continuously writing technical-kline worker. Existing
    SQLite triggers still enqueue mirrored table changes from this plain
    connection, so local execution remains authoritative and async mirroring is
    preserved.
    """
    timeout_seconds = max(0.1, float(timeout_seconds))
    db = sqlite3.connect(DB_NAME, timeout=timeout_seconds)
    db.execute(
        f"PRAGMA busy_timeout={max(100, int(timeout_seconds * 1000))}"
    )
    return db


def worker_process_source_state(repo_path=None):
    """Capture the git source identity once for this worker process lifetime."""
    root = Path(repo_path or Path(__file__).resolve().parents[1])
    base = {"git_sha": None, "git_dirty": None}
    try:
        sha = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False, capture_output=True, text=True, timeout=2,
        )
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            check=False, capture_output=True, text=True, timeout=2,
        )
        if sha.returncode != 0 or dirty.returncode != 0:
            return base
        value = sha.stdout.strip()
        if len(value) != 40 or any(ch not in string.hexdigits for ch in value):
            return base
        return {"git_sha": value.lower(), "git_dirty": bool(dirty.stdout.strip())}
    except (OSError, subprocess.SubprocessError):
        return base


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


def subscription_symbols(con):
    """Return every market route the canonical realtime worker must currently observe.

    Pending entries are execution obligations, not optional telemetry.  In
    particular a 1x LONG may be routed to SPOT while its originating behavior
    evidence came from a USD-M swap.  Excluding PENDING_ENTRY would therefore
    strand the selected SPOT order and silently bias the runtime back toward
    derivatives.
    """
    active = {tuple(row) for row in con.execute(
        """SELECT symbol,market_type FROM paper_agent_positions
        WHERE status IN ('PENDING_ENTRY','OPEN') ORDER BY symbol,market_type"""
    ).fetchall()}
    feature_columns = {row[1] for row in con.execute(
        "PRAGMA table_info(market_behavior_minute_features)"
    )}
    latest = (
        con.execute(
            """SELECT MAX(minute_end_timestamp_ms)
               FROM market_behavior_minute_features
               WHERE market_data_provider='BINANCE'
                 AND eligible_for_learning=1"""
        ).fetchone()[0]
        if "market_data_provider" in feature_columns else None
    )
    monitored = set()
    if latest is not None:
        for (instrument,) in con.execute(
            """SELECT DISTINCT instrument FROM market_behavior_minute_features
            WHERE minute_end_timestamp_ms=? AND market_data_provider='BINANCE'
              AND eligible_for_learning=1""",
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


async def run():
    process_runtime_source = worker_process_source_state()
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
        max_workers=1, thread_name_prefix="agca-paper-execution"
    )
    scan_executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="agca-paper-scan"
    )
    universe_executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="agca-symbol-universe"
    )
    decision_local = threading.local()
    scan_local = threading.local()
    universe_local = threading.local()
    stop_event = asyncio.Event()
    last_health_at = None
    last_quote_received_ms = None
    quote_metrics = {
        "SPOT": realtime_market.new_quote_metrics(),
        "USD_M_PERPETUAL": realtime_market.new_quote_metrics(),
    }
    last_action_status = None
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
    last_scan_started_at = None
    last_scan_completed_at = None
    last_scan_duration_ms = None
    last_scan_error = None
    last_scan_error_detail = None
    last_scan_sqlite_error_name = None
    last_scan_sqlite_error_code = None

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
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop_event.set)

    def load_symbols(db):
        observed_at = now()
        cancel_expired_pending_entries(db, observed_at)
        position_management.initialize_open_positions(db, observed_at)
        return subscription_symbols(db)

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
                "endpoint": metric.get("endpoint"),
                "failover_count": int(metric.get("failover_count") or 0),
                "last_disconnect_error": metric.get("last_disconnect_error"),
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
            "scan_runtime": {
                "started_at": last_scan_started_at,
                "completed_at": last_scan_completed_at,
                "duration_ms": last_scan_duration_ms,
                "error": last_scan_error,
                "error_detail": last_scan_error_detail,
                "sqlite_error_name": last_scan_sqlite_error_name,
                "sqlite_error_code": last_scan_sqlite_error_code,
            },
        })
        details["process_runtime_source"] = process_runtime_source
        write_health(status, **details)

    def universe_connection():
        db = getattr(universe_local, "connection", None)
        if db is None:
            # Schema was initialized on the canonical startup connection before
            # worker threads were created.  Thread-local hot paths must not
            # repeat DDL against the live SQLite writer.
            db = _local_sqlite_connection(30)
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
            # Runtime schema is initialized once before concurrent tasks start.
            db = _local_sqlite_connection(30)
            decision_local.connection = db
        return db

    def scan_connection():
        db = getattr(scan_local, "connection", None)
        if db is None:
            # Opportunity discovery is non-authoritative and completion-paced.
            # Under writer contention it should fail/retry quickly rather than
            # disappear into a 30-second SQLite busy wait per operation.
            timeout_seconds = max(
                0.1, float(os.getenv("AGCA_SCAN_SQLITE_TIMEOUT_SECONDS", "2"))
            )
            db = _local_sqlite_connection(timeout_seconds)
            scan_local.connection = db
        return db

    def reset_scan_connection():
        db = getattr(scan_local, "connection", None)
        if db is not None:
            try:
                db.rollback()
            except sqlite3.Error:
                pass
            try:
                db.close()
            except sqlite3.Error:
                pass
        scan_local.connection = None

    def scan_sqlite_busy(exc):
        return (
            isinstance(exc, sqlite3.OperationalError)
            and getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY
        )

    def latest_any_execution_state():
        with latest_execution_lock:
            if not latest_execution_states:
                return None
            return max(
                latest_execution_states.values(),
                key=lambda item: item[1],
            )

    def scan_once():
        """Discover/select opportunities without ever blocking execution ticks."""
        nonlocal last_scan_started_at, last_scan_completed_at
        nonlocal last_scan_duration_ms, last_scan_error
        nonlocal last_scan_error_detail, last_scan_sqlite_error_name
        nonlocal last_scan_sqlite_error_code
        observed_at = now()
        started = loop.time()
        last_scan_started_at = observed_at.isoformat()
        last_scan_error = None
        last_scan_error_detail = None
        last_scan_sqlite_error_name = None
        last_scan_sqlite_error_code = None
        attempts = max(
            1, int(os.getenv("AGCA_SCAN_BUSY_RETRY_ATTEMPTS", "4"))
        )
        selected = None
        selection_result = {}
        work_con = None
        try:
            for attempt in range(1, attempts + 1):
                work_con = scan_connection()
                selection_result = {}
                try:
                    selected = select_new_positions(
                        work_con, observed_at, metadata,
                        telemetry=selection_result,
                    )
                    break
                except Exception as exc:
                    if not scan_sqlite_busy(exc) or attempt >= attempts:
                        raise
                    # A DEFERRED read->write promotion can return SQLITE_BUSY
                    # immediately even with busy_timeout because waiting would
                    # risk a lock-upgrade deadlock.  Roll back the snapshot,
                    # discard the connection, and retry from a fresh SQLite
                    # transaction after a short bounded backoff.
                    reset_scan_connection()
                    work_con = None
                    time.sleep(min(0.05 * attempt, 0.20))
            if selected is None:
                raise RuntimeError("scan retry loop exited without result")
        except Exception as exc:
            last_scan_error = type(exc).__name__
            last_scan_error_detail = str(exc)[:240]
            last_scan_sqlite_error_name = getattr(
                exc, "sqlite_errorname", None
            )
            last_scan_sqlite_error_code = getattr(
                exc, "sqlite_errorcode", None
            )
            raise
        finally:
            completed = now()
            last_scan_completed_at = completed.isoformat()
            last_scan_duration_ms = round(
                (loop.time() - started) * 1000.0, 3
            )
        latest_scan = work_con.execute(
            """SELECT core_status,candidate_count,selected_count,
                      active_position_count,capacity,available_exposure,
                      reason_counts_json
               FROM paper_behavior_selection_runs
               ORDER BY run_id DESC LIMIT 1"""
        ).fetchone()
        latest_state = latest_any_execution_state()
        brain_state = latest_state[0] if latest_state else None
        write_brain_state({
            "phase": "OPPORTUNITY_SCAN",
            "message": "Piyasa taranıyor; fırsatlar değerlendiriliyor.",
            "observed_symbol": brain_state.symbol if brain_state else None,
            "observed_market_type": (
                brain_state.market_type if brain_state else None
            ),
            "observed_price": brain_state.last_price if brain_state else None,
            "monitored_symbols": len(subscription_symbols(work_con)),
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
                "NEW_POSITION_SELECTED" if selected else "CONTINUE_SCANNING"
            ),
        })
        return selected

    async def scan_loop():
        """Completion-paced scanning on a dedicated worker thread."""
        nonlocal last_scan_error, last_scan_error_detail
        while not stop_event.is_set():
            try:
                await loop.run_in_executor(scan_executor, scan_once)
            except Exception as exc:
                last_scan_error = type(exc).__name__
                last_scan_error_detail = str(exc)[:240]
                emit_health(
                    "DEGRADED",
                    error=f"OPPORTUNITY_SCAN_{type(exc).__name__}",
                    scan_error_detail=last_scan_error_detail,
                    scan_sqlite_error_name=getattr(
                        exc, "sqlite_errorname", None
                    ),
                    scan_sqlite_error_code=getattr(
                        exc, "sqlite_errorcode", None
                    ),
                )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    def latest_execution_state(state, observed_ms):
        key = (state.symbol, state.market_type)
        with latest_execution_lock:
            return latest_execution_states.get(key, (state, observed_ms))

    def process_state(state, observed_ms):
        """Run DB/decision/PAPER work off the websocket event loop."""
        nonlocal last_health_at
        work_con = decision_connection()
        observed_at = datetime.fromtimestamp(
            observed_ms / 1000, tz=timezone.utc
        )
        # Execution obligations always run before and independently from
        # opportunity discovery. A slow scan may not delay pending replans,
        # fills, stops, targets, or position management.
        # Always refresh from the ingest cache immediately before any PAPER
        # execution or position-management action.
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

        market_metadata = (
            metadata.get(execution_state.market_type)
            if isinstance(metadata, dict) else None
        )
        try:
            rule = (
                rules_for(market_metadata, execution_state.symbol)
                if market_metadata is not None else None
            )
        except ExchangeMetadataError:
            # A newly listed/delisted/temporarily missing symbol rule is a
            # per-symbol execution-data failure, not a reason to kill the
            # canonical realtime worker. Fail closed for this symbol and keep
            # processing every other live execution obligation.
            rule = None
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
        # Startup already initialized schema and remote-mirror triggers.
        # Repeating RuntimeConnection construction here every minute would run
        # local DDL and can contend with realtime writers.
        maintenance_con = _local_sqlite_connection(30)
        try:
            reconciled = position_management.reconcile_pending_funding(
                maintenance_con,
                lambda **kwargs: fetch_funding_history_via_postgres(
                    database_url=os.getenv("AGCA_REMOTE_DATABASE_URL", ""), **kwargs,
                ),
                now(),
            )
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
            scan_loop(),
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

        def close_scan_connection():
            reset_scan_connection()

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
                scan_executor, close_scan_connection
            )
            await loop.run_in_executor(
                universe_executor, close_universe_connection
            )
        finally:
            decision_executor.shutdown(wait=True)
            scan_executor.shutdown(wait=True)
            universe_executor.shutdown(wait=True)
            con.close()
            emit_health("STOPPED")


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
