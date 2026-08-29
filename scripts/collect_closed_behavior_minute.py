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
from data.okx_liquidation_channel import probe
from data.okx_live_evidence import collect, fetch_json
from data.okx_market_universe import build_universe
from data.rolling_behavior_window import MINUTE_MS, aggregate_closed_minute
from database.market_behavior_store import persist_closed_minute
from database.causal_evidence_store import evidence_for_closed_minute


HEALTH_CONTRACT_VERSION = "AGCA_MARKET_BEHAVIOR_HEALTH_V1"


def refresh_market_route():
    instruments = fetch_json("public/instruments", {"instType": "SWAP"})
    tickers = fetch_json("market/tickers", {"instType": "SWAP"})
    snapshot_ms = int(time.time() * 1000)
    universe = build_universe(instruments, tickers, snapshot_ms=snapshot_ms)
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
    market_snapshots = []
    started = time.monotonic()
    for index in range(args.samples):
        target = started + index * args.cadence_seconds
        await asyncio.sleep(max(0, target - time.monotonic()))
        market_snapshots.append(await asyncio.to_thread(collect, route, universe))
    liquidations = await liquidation_task
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
    minute_end_ms = (now_ms // MINUTE_MS + 1) * MINUTE_MS
    if (minute_end_ms - now_ms) / 1000 < required_span:
        wait_seconds = (minute_end_ms - now_ms) / 1000 + 0.05
        time.sleep(max(0, wait_seconds))
        now_ms = int(time.time() * 1000)
        minute_end_ms = (now_ms // MINUTE_MS + 1) * MINUTE_MS

    bundles = asyncio.run(
        collect_minute(route, universe, contract_values, args, minute_end_ms))

    time.sleep(max(0, (minute_end_ms - int(time.time() * 1000)) / 1000 + 0.02))
    result = aggregate_closed_minute(bundles, minute_end_ms)
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
            "broad_scan_instrument_count": route["broad_scan_instrument_count"],
            "deep_stream_instrument_count": route["deep_stream_instrument_count"],
            "row_count": result["row_count"],
            "eligible_row_count": result["eligible_row_count"],
            "minute_end_timestamp_ms": result["minute_end_timestamp_ms"],
            "report_sha256": hashlib.sha256(canonical).hexdigest(),
            "raw_market_payloads_persisted": False,
            "may_send_real_orders": False,
        }
        write_json_atomic(args.health_output, health)
    print(json.dumps({
        "row_count": result["row_count"],
        "eligible_row_count": result["eligible_row_count"],
        "minute_end_timestamp_ms": result["minute_end_timestamp_ms"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
