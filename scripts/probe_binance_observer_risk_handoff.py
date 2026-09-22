#!/usr/bin/env python3
"""Read-only actual-VM handoff: Binance WS minute -> dynamic USD-M risk.

This probe deliberately does NOT persist the closed minute, save a risk policy,
evaluate/insert PAPER decisions, change health files, or touch systemd. It only
proves that exact native symbols selected by the new observer can immediately
receive same-market validated hourly risk evidence through the existing private
egress.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data.binance_ws_market_collector import BinanceWsMinuteCollector
from data.rolling_behavior_window import aggregate_closed_minute
from paper_trading.exchange_metadata import load_cache
from risk_manager import risk_manager as risk


def _metadata():
    path = Path(os.getenv(
        "AGCA_EXCHANGE_METADATA_CACHE", "/var/lib/agca/exchange-info-usdm.json"
    ))
    cache = load_cache(
        path, "USD_M_PERPETUAL",
        max_age_seconds=float(os.getenv(
            "AGCA_EXCHANGE_METADATA_MAX_AGE_SECONDS", "7200"
        )),
    )
    doc = json.loads(path.read_text(encoding="utf-8"))
    canonical = json.dumps(
        doc.get("payload"), separators=(",", ":")
    ).encode()
    if (
        doc.get("market") != "USD_M_PERPETUAL"
        or doc.get("fetched_at") != cache.fetched_at
        or doc.get("payload_sha256") != cache.payload_sha256
        or hashlib.sha256(canonical).hexdigest() != cache.payload_sha256
    ):
        raise RuntimeError("EXCHANGE_INFO_CACHE_IDENTITY_MISMATCH")
    return doc["payload"], cache.fetched_at, frozenset(cache.symbols)


def run_probe(*, samples=4, cadence_seconds=10):
    if os.getenv("AGCA_EXECUTION_MODE", "PAPER_ONLY") != "PAPER_ONLY":
        raise RuntimeError("PAPER_ONLY_REQUIRED")
    database_url = os.getenv("AGCA_REMOTE_DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("PRIVATE_EGRESS_NOT_CONFIGURED")
    info, fetched_at, exact_rules = _metadata()
    collector = BinanceWsMinuteCollector(
        info, fetched_at, database_url=database_url
    )
    collected = asyncio.run(
        collector.run(samples=samples, cadence_seconds=cadence_seconds)
    )
    minute_end = int(collected["minute_end_ms"])
    time.sleep(max(0, (minute_end-int(time.time()*1000))/1000 + .02))
    report = aggregate_closed_minute(collected["bundles"], minute_end)
    eligible = tuple(sorted(
        row["instrument"] for row in report["rows"]
        if row.get("eligible_for_learning")
    ))
    invalid_identity = sorted(
        symbol for symbol in eligible
        if symbol not in exact_rules or symbol not in collector.allowed
    )
    if invalid_identity:
        return {
            "mode": "READ_ONLY_NO_DB_WRITES",
            "status": "BLOCKED_NATIVE_IDENTITY",
            "eligible_symbols": list(eligible),
            "invalid_identity": invalid_identity,
            "risk_symbols_computed": [],
            "snapshot_written": False,
            "paper_decision_written": False,
            "real_orders": False,
        }
    risk_rows, errors = [], {}
    for symbol in eligible:
        try:
            candles = risk.fetch_dynamic_closed_1h_klines(symbol)
            classified = risk.classify_asset_risk(
                risk.candle_metrics(symbol, candles)
            )
            risk_rows.append({
                "symbol": symbol,
                "risk_tier": classified["risk_tier"],
                "max_position_pct_before_global": (
                    classified["pre_global_max_position_pct"]
                ),
                "risk_as_of": classified["as_of"],
            })
        except Exception as exc:
            errors[symbol] = type(exc).__name__
    transport = collected["transport"]
    complete = (
        bool(eligible)
        and len(risk_rows) == len(eligible)
        and not errors
        and transport["oi_error_count"] == 0
        and not transport["websocket_errors"]
        and all(transport["subscription_confirmed"].values())
    )
    return {
        "mode": "READ_ONLY_NO_DB_WRITES",
        "status": "COMPLETE" if complete else "PARTIAL_OR_BLOCKED",
        "source_exchange": "BINANCE_USD_M",
        "minute_end_timestamp_ms": minute_end,
        "row_count": report["row_count"],
        "eligible_row_count": report["eligible_row_count"],
        "eligible_symbols": list(eligible),
        "risk_symbols_computed": [row["symbol"] for row in risk_rows],
        "dynamic_risk": risk_rows,
        "risk_error_types": errors,
        "transport": {
            "oi_error_count": transport["oi_error_count"],
            "websocket_errors": transport["websocket_errors"],
            "subscription_confirmed": transport["subscription_confirmed"],
            "liquidation_state": transport["liquidation_state"],
        },
        "snapshot_written": False,
        "paper_decision_written": False,
        "real_orders": False,
    }


if __name__ == "__main__":
    print(json.dumps(run_probe(), sort_keys=True))
