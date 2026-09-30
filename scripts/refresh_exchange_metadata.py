"""Refresh each PAPER route independently using bounded official transport."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

from runtime.process_identity import PROCESS_SOURCE

from paper_trading.exchange_metadata import (
    ExchangeMetadataError, load_market_caches, refresh_cache_bounded,
)


def main():
    database_url = os.getenv("AGCA_REMOTE_DATABASE_URL", "")
    paths = {
        "USD_M_PERPETUAL": os.getenv(
            "AGCA_EXCHANGE_METADATA_CACHE", "/var/lib/agca/exchange-info-usdm.json",
        ),
        "SPOT": os.getenv(
            "AGCA_SPOT_EXCHANGE_METADATA_CACHE", "/var/lib/agca/exchange-info-spot.json",
        ),
    }
    results = {}
    for market, path in paths.items():
        attempted_at = datetime.now(timezone.utc).isoformat()
        try:
            metadata = refresh_cache_bounded(path, market, database_url)
            results[market] = {
                "status": "READY", "symbols": len(metadata.symbols),
                "fetched_at": metadata.fetched_at,
                "payload_sha256": metadata.payload_sha256,
                "last_success_at": metadata.fetched_at, "last_error_at": None,
                "error": None,
            }
        except (ExchangeMetadataError, OSError) as exc:
            _, cached = load_market_caches({market: path})
            results[market] = {
                **cached[market], "refresh_status": "FAILED",
                "last_success_at": cached[market].get("fetched_at"),
                "last_error_at": attempted_at,
                # Transport errors use controlled codes; never print a DSN or
                # raw network exception which could contain credentials.
                "error": str(exc) if isinstance(exc, ExchangeMetadataError)
                else "METADATA_CACHE_WRITE_FAILED",
            }
    health = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "contract_version": "EXCHANGE_METADATA_V1",
        "runtime_source": PROCESS_SOURCE,
        "markets": results, "real_order_allowed": 0,
    }
    health_path = Path(os.getenv(
        "AGCA_EXCHANGE_METADATA_HEALTH", "/var/lib/agca/exchange-metadata-health.json",
    ))
    health_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = health_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(health, sort_keys=True), encoding="utf-8")
    temporary.replace(health_path)
    print(json.dumps(health, sort_keys=True))
    # Failure remains observable through a failed oneshot, with no worker
    # dependency/restart storm; both markets were attempted independently.
    return int(any(value.get("refresh_status") == "FAILED" for value in results.values()))


if __name__ == "__main__":
    raise SystemExit(main())
