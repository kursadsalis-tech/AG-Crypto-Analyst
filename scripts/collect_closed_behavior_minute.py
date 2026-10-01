#!/usr/bin/env python3
"""Collect one closed live minute in memory and persist only compact features."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import asyncio
import fcntl
import hashlib
import json
import os
import signal
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.process_identity import WorkerHealthState
from runtime.worker_progress import WorkProgress
from database.sqlite_policy import is_sqlite_busy

from data.adaptive_market_scanner import build_attention_route
from data.live_behavior_evidence_bundle import build_bundle
from data.binance_liquidation_channel import probe
from data.binance_live_evidence import (
    collect_minute_snapshots,
    fetch_market_tickers,
)
from data.binance_market_universe import build_universe
from data.rolling_behavior_window import MINUTE_MS, aggregate_closed_minute
from database.market_behavior_store import persist_closed_minute
from database.broad_market_store import load_universe
from database.causal_evidence_store import evidence_for_closed_minute


HEALTH_CONTRACT_VERSION = "AGCA_MARKET_BEHAVIOR_HEALTH_V2_BINANCE_CANONICAL"
_HEALTH_STATE = WorkerHealthState()
_PROGRESS = WorkProgress(lambda progress: None)
_LAST_COMPLETION_HEALTHY = False
TRANSIENT_RETRY_SECONDS = 2.0
_TRANSIENT_DEPENDENCY_MESSAGES = {
    "broad radar missing, future or stale",
}


def is_transient_dependency_error(exc):
    """Only known upstream-readiness gaps are recoverable in continuous mode."""
    return isinstance(exc, ValueError) and str(exc) in _TRANSIENT_DEPENDENCY_MESSAGES


def advance_progress(args, phase):
    """Only completed collection/processing steps extend the startup budget."""
    _PROGRESS.advance(phase)
    path = getattr(args, "health_output", None)
    if not path:
        return
    last = _HEALTH_STATE.last_success_at
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() if last else None
    ready = _LAST_COMPLETION_HEALTHY and age is not None and 0 <= age <= 180
    write_health(path, {"status": "HEALTHY" if ready else "RUNNING",
                       "phase": phase, "progress": _PROGRESS.last_progress}, success=False)


def collection_alignment(now_ms, required_span_seconds):
    """Return the target close and an optional not-before collection boundary.

    If the observer starts too late to fit every required sample in the current
    minute, the next minute is intentionally skipped and collection is delayed
    until that skipped boundary.  Starting collection immediately while merely
    relabeling it as the following minute produces fewer than the mandatory
    samples inside the target minute and makes every row ineligible.
    """
    next_end_ms = (int(now_ms) // MINUTE_MS + 1) * MINUTE_MS
    remaining = (next_end_ms - int(now_ms)) / 1000.0
    if remaining >= float(required_span_seconds):
        return next_end_ms, None
    return next_end_ms + MINUTE_MS, next_end_ms


def refresh_market_route(timings=None, *, database=None, opportunity_slots=10):
    snapshot_ms = int(time.time() * 1000)
    if database:
        universe = load_universe(database, now_ms=snapshot_ms)
    else:
        # Standalone offline/diagnostic CLI keeps its explicit direct source.
        tickers = asyncio.run(fetch_market_tickers(timings=timings))
        universe = build_universe(tickers, snapshot_ms=int(time.time() * 1000))
    if not universe.get("selected"):
        raise RuntimeError("Binance WebSocket market universe is empty")
    return universe, build_attention_route(
        universe, opportunity_slots=opportunity_slots, rotation_epoch=snapshot_ms // MINUTE_MS
    )


def write_json_atomic(path, payload):
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent,
                                     prefix=output.name + ".", delete=False) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.chmod(temporary, 0o600)
    os.replace(temporary, output)


def write_health(path, payload, *, success=False):
    global _LAST_COMPLETION_HEALTHY
    if success:
        _LAST_COMPLETION_HEALTHY = payload.get("status") == "HEALTHY"
    elif payload.get("status") in {"DEGRADED", "ERROR", "STARTUP_FAILED"}:
        _LAST_COMPLETION_HEALTHY = False
    health = _HEALTH_STATE.decorate({
        "health_contract_version": HEALTH_CONTRACT_VERSION,
        "producer_version": "BINANCE_MARKET_BEHAVIOR_PRODUCER_V1",
        "execution_mode": "PAPER_ONLY", "may_send_real_orders": False,
        "progress": _PROGRESS.last_progress,
        **payload,
    }, success=success)
    health["completed_at"] = health["last_success_at"]
    canonical = json.dumps(health, sort_keys=True, separators=(",", ":")).encode()
    health["health_sha256"] = hashlib.sha256(canonical).hexdigest()
    write_json_atomic(path, health)


async def _collect_minute_attempt(route, universe, contract_values, args, timings=None):
    required_span = (args.samples - 1) * args.cadence_seconds + 1.0
    if required_span >= MINUTE_MS / 1000:
        raise ValueError("requested samples and cadence cannot fit in one minute")
    minute_end_ms = None
    market_task = None
    alignment = {"alignment_wait_ms": 0, "late_start_recovery": False,
                 "sampling_rollover_count": 0}

    async def align_hydrated_sampling():
        nonlocal minute_end_ms
        if minute_end_ms is not None:
            alignment["sampling_rollover_count"] += 1
        started = time.monotonic()
        while True:
            now_ms = int(time.time() * 1000)
            target, not_before = collection_alignment(now_ms, required_span)
            if not_before is None:
                minute_end_ms = target
                break
            alignment["late_start_recovery"] = True
            await asyncio.sleep(max(0.0, (not_before - now_ms) / 1000.0) + 0.02)
        alignment["alignment_wait_ms"] += round((time.monotonic() - started) * 1000)
        advance_progress(args, "HYDRATED_WINDOW_ALIGNED")
        return minute_end_ms

    def liquidation_close():
        # Sampling can roll into another minute after a scheduling stall.
        # Only a completed market task makes its target close definitive.
        if minute_end_ms is not None and market_task is not None and market_task.done():
            return minute_end_ms
        return int(time.time() * 1000) + 2 * MINUTE_MS

    liquidation_task = asyncio.create_task(probe(
        contract_values=contract_values, timings=timings,
        observe_until_ms=liquidation_close,
    ))
    market_task = asyncio.create_task(collect_minute_snapshots(
        route,
        universe,
        samples=args.samples,
        cadence_seconds=args.cadence_seconds,
        closed_bar_sink=None,
        observe_until_ms=None,
        before_sampling=align_hydrated_sampling,
        progress_callback=lambda phase: advance_progress(args, phase),
        timings=timings,
    ))
    try:
        pending = {market_task, liquidation_task}
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            if liquidation_task in done:
                liquidations = liquidation_task.result()
                if liquidations.get("state") not in {
                    "AVAILABLE_EVENTS_OBSERVED", "AVAILABLE_NO_EVENT_IN_WINDOW"
                }:
                    raise ConnectionError("Binance liquidation channel failed")
                if not market_task.done():
                    raise ConnectionError("Binance liquidation channel ended before market collection")
            if market_task in done:
                market_snapshots = market_task.result()
                if minute_end_ms is None:
                    raise RuntimeError("market collection did not establish a target minute")
        alignment["sample_first_timestamp_ms"] = (
            market_snapshots[0]["collected_timestamp_ms"] if market_snapshots else None
        )
        alignment["sample_last_timestamp_ms"] = (
            market_snapshots[-1]["collected_timestamp_ms"] if market_snapshots else None
        )
        return ([build_bundle(market, liquidations) for market in market_snapshots],
                minute_end_ms, alignment)
    finally:
        for task in (market_task, liquidation_task):
            task.cancel()
        await asyncio.gather(market_task, liquidation_task, return_exceptions=True)


async def collect_minute(route, universe, contract_values, args, timings=None):
    """Retry failed transports with fresh streams inside the cycle deadline."""
    errors = []
    for attempt in range(3):
        try:
            bundles, minute_end_ms, alignment = await _collect_minute_attempt(
                route, universe, contract_values, args, timings
            )
        except ConnectionError as exc:
            errors.append(str(exc)[:500])
            if attempt == 2:
                raise ConnectionError(
                    "observer transport failed after 3 attempts: " + "; ".join(errors)
                ) from exc
            await asyncio.sleep(0.25 * (attempt + 1))
        else:
            alignment["transport_retry_count"] = len(errors)
            alignment["transport_retry_reasons"] = errors
            return bundles, minute_end_ms, alignment


def run_cycle(args):
    cycle_started = time.monotonic()
    transport_timings = {}
    route_started = time.monotonic()
    if args.refresh_live_universe:
        universe, route = refresh_market_route(
            transport_timings, database=args.database,
            opportunity_slots=args.opportunity_slots,
        )
    else:
        if not args.route or not args.universe:
            parser.error("--route and --universe are required without --refresh-live-universe")
        route = json.loads(Path(args.route).read_text(encoding="utf-8"))
        universe = json.loads(Path(args.universe).read_text(encoding="utf-8"))
    route_refresh_ms = round((time.monotonic() - route_started) * 1000.0, 3)
    advance_progress(args, "ROUTE_RESOLVED")
    contract_values = {row["instrument"]: row.get("contract_value", 1.0)
                       for row in universe.get("selected", [])}

    collection_started = time.monotonic()
    bundles, minute_end_ms, alignment = asyncio.run(
        collect_minute(route, universe, contract_values, args, transport_timings))
    advance_progress(args, "COLLECTION_COMPLETED")
    collection_ms = round(max(
        0.0, (time.monotonic() - collection_started) * 1000.0
        - alignment["alignment_wait_ms"],
    ), 3)

    close_wait_started = time.monotonic()
    time.sleep(max(0, (minute_end_ms - int(time.time() * 1000)) / 1000 + 0.02))
    close_wait_ms = round((time.monotonic() - close_wait_started) * 1000, 3)
    advance_progress(args, "MINUTE_CLOSED")
    aggregate_started = time.monotonic()
    result = aggregate_closed_minute(bundles, minute_end_ms)
    aggregate_ms = round((time.monotonic() - aggregate_started) * 1000, 3)
    advance_progress(args, "AGGREGATION_COMPLETED")
    causal_started = time.monotonic()
    result["market_data_provider"] = "BINANCE"
    result["causal_external_evidence"] = (
        evidence_for_closed_minute(args.database, minute_end_ms)
        if args.database else {
            "contract_version": "CAUSAL_MINUTE_EVIDENCE_V1",
            "minute_end_timestamp_ms": minute_end_ms,
            "assignment_clock": "AVAILABLE_AT", "event_count": 0,
            "counts": {kind: 0 for kind in ("MACRO", "NEWS", "ONCHAIN", "WHALE")},
            "events": [],
        }
    )
    causal_ms = round((time.monotonic() - causal_started) * 1000, 3)
    advance_progress(args, "CAUSAL_EVIDENCE_READ")
    result["collection_contract"] = {
        "samples_requested": args.samples,
        "cadence_seconds": args.cadence_seconds,
        "market_data_provider": "BINANCE",
        "market_transport": "WEBSOCKET_ONLY",
        "broad_market_stream": "ALL_MARKET_24H_TICKER",
        "broad_radar_owner": "TECHNICAL_KLINE_WORKER" if args.database else "STANDALONE",
        "broad_radar_snapshot_timestamp_ms": universe.get("snapshot_timestamp_ms"),
        "broad_radar_instrument_count": len(universe.get("selected", [])),
        "outside_deep_analysis_count": len(universe.get("selected", [])) - route["deep_stream_instrument_count"],
        "deep_shortlist_is_trade_universe": False,
        "routing_contract": route.get("routing_contract"),
        "deep_streams": [
            "DEPTH20", "AGG_TRADE", "MARK_PRICE_FUNDING"
        ],
        "open_interest_state": "UNAVAILABLE_CANONICAL_WS",
        "liquidation_connection": "ONE_CONTINUOUS_PUBLIC_WEBSOCKET_FOR_MINUTE",
        "raw_snapshots_lifetime": "MEMORY_ONLY_UNTIL_CLOSED_MINUTE_AGGREGATION",
        **alignment,
    }
    persistence_ms = None
    if args.database:
        persistence_started = time.monotonic()
        result["sqlite_persistence"] = persist_closed_minute(
            args.database, result, args.retention_days, include_counts=False)
        persistence_ms = round(
            (time.monotonic() - persistence_started) * 1000.0, 3
        )
        if int(result["sqlite_persistence"].get("upserted") or 0) > 0:
            advance_progress(args, "CLOSED_MINUTE_PERSISTED")
    result["transport_timings_ms"] = transport_timings
    result["runtime_timings_ms"] = {
        "route_refresh": route_refresh_ms,
        "alignment_wait": alignment["alignment_wait_ms"],
        "collection": collection_ms,
        "persistence": persistence_ms,
        "closed_minute_wait": close_wait_ms,
        "aggregation": aggregate_ms,
        "causal_evidence": causal_ms,
        "total_cycle": round((time.monotonic() - cycle_started) * 1000.0, 3),
    }
    write_json_atomic(args.output, result)
    if args.health_output:
        canonical = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        persisted = int(result.get("sqlite_persistence", {}).get("upserted") or 0)
        minute_age = int(time.time() * 1000) - int(result["minute_end_timestamp_ms"])
        ready = (result["row_count"] > 0 and result["eligible_row_count"] == result["row_count"]
                 and persisted >= result["row_count"] and 0 <= minute_age <= 180_000)
        health = {
            "health_contract_version": HEALTH_CONTRACT_VERSION,
            "status": "HEALTHY" if ready else "DEGRADED",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "execution_mode": "PAPER_ONLY",
            "market_data_provider": "BINANCE",
            "broad_scan_instrument_count": route["broad_scan_instrument_count"],
            "deep_stream_instrument_count": route["deep_stream_instrument_count"],
            "row_count": result["row_count"],
            "eligible_row_count": result["eligible_row_count"],
            "minute_end_timestamp_ms": result["minute_end_timestamp_ms"],
            "report_sha256": hashlib.sha256(canonical).hexdigest(),
            "raw_market_payloads_persisted": False,
            "may_send_real_orders": False,
            "runtime_timings_ms": result.get("runtime_timings_ms"),
            "collection_contract": result["collection_contract"],
            "transport_timings_ms": transport_timings,
            "sql_timings_ms": result.get("sqlite_persistence", {}).get("sql_timings_ms"),
            "write_attempts": result.get("sqlite_persistence", {}).get("write_attempts"),
            "forward_outcomes_settled": (
                result.get("sqlite_persistence", {})
                .get("forward_outcomes", {}).get("settled", 0)
            ),
            "forward_outcomes_queued": (
                result.get("sqlite_persistence", {})
                .get("forward_outcomes", {}).get("queued", 0)
            ),
            "forward_outcome_settlement_mode": (
                result.get("sqlite_persistence", {})
                .get("forward_outcomes", {}).get("mode")
            ),
            "forward_outcome_row_count": (
                result.get("sqlite_persistence", {})
                .get("forward_outcomes", {}).get("outcome_row_count")
            ),
            "forward_independent_event_count": (
                result.get("sqlite_persistence", {})
                .get("forward_outcomes", {}).get("independent_event_count")
            ),
        }
        write_health(args.health_output, health, success=ready)
    print(json.dumps({
        "row_count": result["row_count"],
        "eligible_row_count": result["eligible_row_count"],
        "minute_end_timestamp_ms": result["minute_end_timestamp_ms"],
    }, sort_keys=True))


def _cycle_timeout(signum, frame):
    # Exit the supervised process; channel-level `except Exception` handlers
    # must not swallow the whole-cycle deadline as a recoverable feed error.
    raise SystemExit("market behavior cycle exceeded its time budget")


def run_bounded_cycle(args):
    previous = signal.signal(signal.SIGALRM, _cycle_timeout)
    signal.alarm(args.cycle_timeout_seconds)
    try:
        return run_cycle(args)
    except (Exception, SystemExit) as exc:
        transient_dependency = (
            bool(getattr(args, "continuous", False))
            and is_transient_dependency_error(exc)
        )
        recoverable = is_sqlite_busy(exc) or transient_dependency
        if getattr(args, "health_output", None):
            write_health(args.health_output, {
                "status": "DEGRADED" if recoverable else "ERROR",
                "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                "fatal_error": not recoverable,
            })
        if recoverable:
            return None
        raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route")
    parser.add_argument("--universe")
    parser.add_argument("--refresh-live-universe", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--opportunity-slots", type=int, default=10)
    parser.add_argument("--cadence-seconds", type=float, default=10)
    parser.add_argument("--database")
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--health-output")
    parser.add_argument("--lock-file")
    parser.add_argument("--continuous", action="store_true",
                        help="Publish consecutive minutes in one observer process")
    parser.add_argument("--cycle-timeout-seconds", type=int, default=240)
    args = parser.parse_args()
    if not 1 <= args.opportunity_slots <= 10:
        parser.error("--opportunity-slots must be between 1 and 10")
    if args.cycle_timeout_seconds <= 0:
        parser.error("--cycle-timeout-seconds must be positive")
    if os.getenv("AGCA_EXECUTION_MODE", "PAPER_ONLY") != "PAPER_ONLY":
        raise SystemExit("Safety gate: market observer accepts PAPER_ONLY exclusively")
    with ExitStack() as stack:
        if args.lock_file:
            lock_path = Path(args.lock_file)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_handle = stack.enter_context(lock_path.open("w"))
            try:
                fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SystemExit("Another market behavior observer owns the lock") from exc
        if args.health_output:
            write_health(args.health_output, {"status": "STARTING"})
        while True:
            result = run_bounded_cycle(args)
            if not args.continuous:
                return
            if result is None:
                time.sleep(TRANSIENT_RETRY_SECONDS)


if __name__ == "__main__":
    main()
