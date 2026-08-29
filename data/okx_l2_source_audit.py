"""Audit OKX public historical L2 metadata without downloading bulk files.

OKX exposes the same public endpoints used by its Historical Market Data page.
This module records instrument coverage and concrete CDN artifacts.  It does
not claim that an artifact is usable until a later checksum/schema sample gate
has inspected the actual file.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


VERSION = "OKX_L2_SOURCE_AUDIT_V1"
DEFAULT_COHORT = (
    "BTC-USDT", "ETH-USDT", "ARKM-USDT", "FIDA-USDT", "WLD-USDT",
    "TIA-USDT", "ETHFI-USDT", "BIO-USDT", "LQTY-USDT", "ILV-USDT",
    "SLP-USDT", "UMA-USDT", "IO-USDT",
)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def audit(instruments_payload, download_payloads, cohort=DEFAULT_COHORT):
    if str(instruments_payload.get("code")) != "0":
        raise ValueError("OKX instruments response was not successful")
    listed = set(instruments_payload.get("data", {}).get("instList", ()))
    artifacts = {}
    for payload in download_payloads:
        if str(payload.get("code")) != "0":
            continue
        for detail in payload.get("data", {}).get("details", ()):
            family = detail.get("instFamily")
            for group in detail.get("groupDetails", ()):
                url = group.get("url")
                filename = group.get("filename")
                if not family or not url or not filename:
                    continue
                artifacts.setdefault(family, []).append({
                    "filename": filename,
                    "size_mb": float(group["sizeMB"]),
                    "url": url,
                    "date_timestamp": group.get("dateTs"),
                })
    available = sorted(symbol for symbol in cohort if symbol in listed)
    missing = sorted(symbol for symbol in cohort if symbol not in listed)
    verified = sorted(symbol for symbol in cohort if artifacts.get(symbol))
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "provider": "OKX",
            "data_contract": "HIGH_RESOLUTION_L2_400_LEVEL_METADATA",
            "instruments_endpoint": (
                "/priapi/v5/broker/public/trade-data/instruments"
            ),
            "download_endpoint": (
                "/priapi/v5/broker/public/trade-data/download-link"
            ),
        },
        "cohort": {
            "requested": list(cohort),
            "listed": available,
            "missing": missing,
            "listed_count": len(available),
            "requested_count": len(cohort),
            "listed_ratio": len(available) / len(cohort) if cohort else 0.0,
            "artifact_verified_symbols": verified,
            "artifact_verified_count": len(verified),
        },
        "artifacts": artifacts,
        "actual_file_schema_verified": False,
        "ready_for_historical_model_test": False,
        "next_gate": "DOWNLOAD_SMALLEST_ARTIFACT_AND_VALIDATE_L2_SCHEMA_SEQUENCE_GAPS",
        "model_or_policy_changed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instruments", required=True)
    parser.add_argument("--download-response", action="append", default=[])
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = audit(
        load_json(args.instruments),
        [load_json(path) for path in args.download_response],
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
