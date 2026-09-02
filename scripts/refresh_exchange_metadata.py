"""Refresh the validated PAPER exchange-rule cache through private DB egress."""
from __future__ import annotations

import json
import os

from paper_trading.exchange_metadata import refresh_cache_via_postgres


def main():
    database_url = os.getenv("AGCA_REMOTE_DATABASE_URL", "")
    results = []
    for market, path in (
        ("USD_M_PERPETUAL", os.getenv(
            "AGCA_EXCHANGE_METADATA_CACHE",
            "/var/lib/agca/exchange-info-usdm.json",
        )),
        ("SPOT", os.getenv(
            "AGCA_SPOT_EXCHANGE_METADATA_CACHE",
            "/var/lib/agca/exchange-info-spot.json",
        )),
    ):
        metadata = refresh_cache_via_postgres(
            path, market, database_url,
        )
        results.append({
            "market": metadata.market,
            "symbols": len(metadata.symbols),
            "fetched_at": metadata.fetched_at,
            "payload_sha256": metadata.payload_sha256,
        })
    print(json.dumps(results, sort_keys=True))


if __name__ == "__main__":
    main()
