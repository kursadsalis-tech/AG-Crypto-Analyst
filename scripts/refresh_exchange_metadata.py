"""Refresh the validated PAPER exchange-rule cache through private DB egress."""
from __future__ import annotations

import json
import os

from paper_trading.exchange_metadata import refresh_cache_via_postgres


def main():
    metadata = refresh_cache_via_postgres(
        os.getenv(
            "AGCA_EXCHANGE_METADATA_CACHE",
            "/var/lib/agca/exchange-info-usdm.json",
        ),
        "USD_M_PERPETUAL",
        os.getenv("AGCA_REMOTE_DATABASE_URL", ""),
    )
    print(json.dumps({
        "market": metadata.market,
        "symbols": len(metadata.symbols),
        "fetched_at": metadata.fetched_at,
        "payload_sha256": metadata.payload_sha256,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
