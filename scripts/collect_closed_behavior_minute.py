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
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

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
from database.causal_evidence_store import evidence_for_closed_minute


HEALTH_CONTRACT_VERSION = "AGCA_MARKET_BEHAVIOR_HEALTH_V2_BINANCE_CANONICAL"


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


def refresh_market_route(timings=None):
    tickers = asyncio.run(fetch_market_tickers(timings=timings))
    snapshot_ms = int(time.time() * 1000)
    universe = build_universe(tickers, snapshot_ms=snapshot_ms)
    if not universe.get("selected"):
        raise RuntimeError("Binance WebSocket market universe is empty")
    return universe, build_attention_route(universe, opportunity_slots=10)


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


async def collect_minute(route, universe, contract_values, args, timings=None):
    required_span = (args.samples - 1) * args.cadence_seconds + 1.0
    if required_span >= MINUTE_MS / 1000:
        raise ValueError("requested samples and cadence cannot fit in one minute")
    minute_end_ms = None
    alignment = {"alignment_wait_ms": 0, "late_start_recovery": False}

    async def align_hydrated_sampling():
        nonlocal minute_end_ms
        started = time.monotonic()
        while True:
            now_ms = int(time.time() * 1000)
            target, not_before = collection_alignment(now_ms, required_span)
            if not_before is None:
                minute_end_ms = target
                break
            alignment["late_start_recovery"] = True
            await asyncio.sleep(max(0.0, (not_before - now_ms) / 1000.0) + 0.02)
        alignment["alignment_wait_ms"] = round((time.monotonic() - started) * 1000)

    def liquidation_close():
        # Hydration and alignment may move the target. Keep the stream alive
        # until sample 1 establishes the actual closed-minute boundary.
        return minute_end_ms if minute_end_ms is not None else int(time.time() * 1000) + 2 * MINUTE_MS

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
        timings=timings,
    ))
    try:
        market_snapshots = await market_task
        if minute_end_ms is None:
            raise RuntimeError("market collection did not establish a target minute")
        liquidations = await liquidation_task
        return ([build_bundle(market, liquidations) for market in market_snapshots],
                minute_end_ms, alignment)
    finally:
        for task in (market_task, liquidation_task):
            task.cancel()
        await asyncio.gather(market_task, liquidation_task, return_exceptions=True)


def run_cycle(args):
    cycle_started = time.monotonic()
    transport_timings = {}
    route_started = time.monotonic()
    if args.refresh_live_universe:
        universe, route = refresh_market_route(transport_timings)
    else:
        if not args.route or not args.universe:
            parser.error("--route and --universe are required without --refresh-live-universe")
        route = json.loads(Path(args.route).read_text(encoding="utf-8"))
        universe = json.loads(Path(args.universe).read_text(encoding="utf-8"))
    route_refresh_ms = round((time.monotonic() - route_started) * 1000.0, 3)
    contract_values = {row["instrument"]: row.get("contract_value", 1.0)
                       for row in universe.get("selected", [])}

    collection_started = time.monotonic()
    bundles, minute_end_ms, alignment = asyncio.run(
        collect_minute(route, universe, contract_values, args, transport_timings))
    collection_ms = round(max(
        0.0, (time.monotonic() - collection_started) * 1000.0
        - alignment["alignment_wait_ms"],
    ), 3)

    close_wait_started = time.monotonic()
    time.sleep(max(0, (minute_end_ms - int(time.time() * 1000)) / 1000 + 0.02))
    close_wait_ms = round((time.monotonic() - close_wait_started) * 1000, 3)
    aggregate_started = time.monotonic()
    result = aggregate_closed_minute(bundles, minute_end_ms)
    aggregate_ms = round((time.monotonic() - aggregate_started) * 1000, 3)
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
    result["collection_contract"] = {
        "samples_requested": args.samples,
        "cadence_seconds": args.cadence_seconds,
        "market_data_provider": "BINANCE",
        "market_transport": "WEBSOCKET_ONLY",
        "broad_market_stream": "ALL_MARKET_24H_TICKER",
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
        health = {
            "health_contract_version": HEALTH_CONTRACT_VERSION,
            "status": "HEALTHY" if result["eligible_row_count"] == result["row_count"] else "DEGRADED",
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
        health_canonical = json.dumps(
            health, sort_keys=True, separators=(",", ":")
        ).encode()
        health["health_sha256"] = hashlib.sha256(health_canonical).hexdigest()
        write_json_atomic(args.health_output, health)
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
    parser.add_argument("--cadence-seconds", type=float, default=10)
    parser.add_argument("--database")
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--health-output")
    parser.add_argument("--lock-file")
    parser.add_argument("--continuous", action="store_true",
                        help="Publish consecutive minutes in one observer process")
    parser.add_argument("--cycle-timeout-seconds", type=int, default=240)
    args = parser.parse_args()
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
        while True:
            run_bounded_cycle(args)
            if not args.continuous:
                return


if __name__ == "__main__":
    main()
