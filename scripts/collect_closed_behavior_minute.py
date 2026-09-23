#!/usr/bin/env python3
"""Collect one closed live minute in memory and persist only compact features."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
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
    """Return the behavior-minute close and any skipped partial close."""
    next_end_ms = (int(now_ms) // MINUTE_MS + 1) * MINUTE_MS
    remaining = (next_end_ms - int(now_ms)) / 1000.0
    if remaining >= float(required_span_seconds):
        return next_end_ms, None
    return next_end_ms + MINUTE_MS, next_end_ms


def refresh_market_route():
    tickers = asyncio.run(fetch_market_tickers())
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


async def collect_minute(route, universe, contract_values, args, minute_end_ms):
    duration = max(1, int((minute_end_ms - int(time.time() * 1000)) / 1000))
    liquidation_task = asyncio.create_task(probe(duration, contract_values))
    market_task = asyncio.create_task(collect_minute_snapshots(
        route,
        universe,
        samples=args.samples,
        cadence_seconds=args.cadence_seconds,
        closed_bar_sink=None,
        observe_until_ms=None,
    ))
    market_snapshots, liquidations = await asyncio.gather(
        market_task, liquidation_task
    )
    return [build_bundle(market, liquidations) for market in market_snapshots]


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
    args = parser.parse_args()
    if os.getenv("AGCA_EXECUTION_MODE", "PAPER_ONLY") != "PAPER_ONLY":
        raise SystemExit("Safety gate: market observer accepts PAPER_ONLY exclusively")
    lock_handle = None
    if args.lock_file:
        lock_path = Path(args.lock_file)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_handle = lock_path.open("w")
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit("Another market behavior observer owns the lock") from exc
    if args.refresh_live_universe:
        universe, route = refresh_market_route()
    else:
        if not args.route or not args.universe:
            parser.error("--route and --universe are required without --refresh-live-universe")
        route = json.loads(Path(args.route).read_text(encoding="utf-8"))
        universe = json.loads(Path(args.universe).read_text(encoding="utf-8"))
    contract_values = {row["instrument"]: row.get("contract_value", 1.0)
                       for row in universe.get("selected", [])}

    required_span = (args.samples - 1) * args.cadence_seconds + 8
    now_ms = int(time.time() * 1000)
    minute_end_ms, _ = collection_alignment(now_ms, required_span)

    bundles = asyncio.run(
        collect_minute(route, universe, contract_values, args, minute_end_ms))

    time.sleep(max(0, (minute_end_ms - int(time.time() * 1000)) / 1000 + 0.02))
    result = aggregate_closed_minute(bundles, minute_end_ms)
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
    }
    if args.database:
        result["sqlite_persistence"] = persist_closed_minute(
            args.database, result, args.retention_days)
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
            "forward_outcomes_settled": (
                result.get("sqlite_persistence", {})
                .get("forward_outcomes", {}).get("settled", 0)
            ),
            "forward_outcome_row_count": (
                result.get("sqlite_persistence", {})
                .get("forward_outcomes", {}).get("outcome_row_count", 0)
            ),
            "forward_independent_event_count": (
                result.get("sqlite_persistence", {})
                .get("forward_outcomes", {}).get("independent_event_count", 0)
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


if __name__ == "__main__":
    main()
