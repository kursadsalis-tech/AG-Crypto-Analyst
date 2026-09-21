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

from data.binance_ws_market_collector import BinanceWsMinuteCollector
from paper_trading.exchange_metadata import load_cache
from data.rolling_behavior_window import MINUTE_MS, aggregate_closed_minute
from database.market_behavior_store import persist_closed_minute
from database.causal_evidence_store import evidence_for_closed_minute


HEALTH_CONTRACT_VERSION = "AGCA_MARKET_BEHAVIOR_HEALTH_V1"


def verified_exchange_info():
    """Pin official exchangeInfo payload to the validated existing cache.

    Never query Binance REST from the GCE VM and never invent a symbol. Reject
    a concurrent metadata file rotation instead of mixing digest and payload.
    """
    path = Path(os.getenv(
        "AGCA_EXCHANGE_METADATA_CACHE", "/var/lib/agca/exchange-info-usdm.json"
    ))
    age = float(os.getenv("AGCA_EXCHANGE_METADATA_MAX_AGE_SECONDS", "7200"))
    validated = load_cache(path, "USD_M_PERPETUAL", max_age_seconds=age)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if (doc.get("payload_sha256") != validated.payload_sha256
            or doc.get("fetched_at") != validated.fetched_at
            or doc.get("market") != "USD_M_PERPETUAL"):
        raise RuntimeError("EXCHANGE_INFO_CACHE_ROTATED_DURING_READ")
    return doc["payload"], validated.fetched_at


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route")
    parser.add_argument("--universe")
    parser.add_argument("--refresh-live-universe", action="store_true")
    parser.add_argument("--shadow-only", action="store_true",
                        help="Read-only full-minute acceptance: no report, DB or health writes")
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
    if not args.refresh_live_universe or args.route or args.universe:
        raise SystemExit("Safety gate: only validated dynamic Binance universe is supported")
    info, fetched_at = verified_exchange_info()
    collector = BinanceWsMinuteCollector(
        info, fetched_at,
        database_url=os.getenv("AGCA_REMOTE_DATABASE_URL", ""),
    )
    collected = asyncio.run(
        collector.run(samples=args.samples, cadence_seconds=args.cadence_seconds)
    )
    universe, route = collected["universe"], collected["route"]
    minute_end_ms = collected["minute_end_ms"]
    time.sleep(max(0, (minute_end_ms - int(time.time() * 1000)) / 1000 + 0.02))
    result = aggregate_closed_minute(collected["bundles"], minute_end_ms)
    result["market_transport"] = collected["transport"]
    result["universe_coverage"] = {
        "eligible_contract_count": len(collector.allowed),
        "fresh_pair_count": universe["received_fresh_ws_pair_count"],
        "broad_scan_instrument_count": route["broad_scan_instrument_count"],
        "deep_stream_instrument_count": route["deep_stream_instrument_count"],
    }
    result["source_exchange"] = "BINANCE_USD_M"
    if args.shadow_only:
        # Do not persist, write health, or settle historical outcomes.
        print(json.dumps({
            "mode": "READ_ONLY_SHADOW_NO_DB_WRITES",
            "source_exchange": "BINANCE_USD_M",
            "row_count": result["row_count"],
            "eligible_row_count": result["eligible_row_count"],
            "minute_end_timestamp_ms": minute_end_ms,
            "transport": collected["transport"],
            "universe_coverage": result["universe_coverage"],
            "failed_gates": {
                row["instrument"]: [name for name, ok in row["quality_gates"].items()
                                    if not ok]
                for row in result["rows"] if not row["eligible_for_learning"]
            },
        }, sort_keys=True))
        return
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
        "liquidation_connection": "ONE_CONTINUOUS_BINANCE_USD_M_WEBSOCKET_FOR_MINUTE",
        "market_transport": "BINANCE_USD_M_VALIDATED_PUBLIC_MARKET_WEBSOCKETS",
        "oi_transport": "BOUNDED_EXISTING_PRIVATE_SUPABASE_BINANCE_HTTP",
        "liquidation_coverage": "AT_MOST_ONE_ORDER_PER_SYMBOL_PER_SECOND",
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
            "status": "HEALTHY" if (result["row_count"] > 0
                      and result["eligible_row_count"] == result["row_count"]) else "DEGRADED",
            "source_exchange": "BINANCE_USD_M",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "execution_mode": "PAPER_ONLY",
            "broad_scan_instrument_count": route["broad_scan_instrument_count"],
            "deep_stream_instrument_count": route["deep_stream_instrument_count"],
            "row_count": result["row_count"],
            "eligible_row_count": result["eligible_row_count"],
            "minute_end_timestamp_ms": result["minute_end_timestamp_ms"],
            "report_sha256": hashlib.sha256(canonical).hexdigest(),
            "raw_market_payloads_persisted": False,
            "oi_error_count": collected["transport"]["oi_error_count"],
            "websocket_errors": collected["transport"]["websocket_errors"],
            "liquidation_state": collected["transport"]["liquidation_state"],
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
