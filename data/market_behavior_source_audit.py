"""Fail-closed audit for historical market-behaviour source samples.

This is a data-readiness tool, not a backtest or a signal generator.  It
validates one Binance Vision ``bookDepth`` archive before AGCA is allowed to
describe it as usable historical evidence.  The archive is a percentage-band
depth summary; it is deliberately never labelled a full L2 order book.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


VERSION = "MARKET_BEHAVIOR_SOURCE_AUDIT_V1"
EXPECTED_COLUMNS = ("timestamp", "percentage", "depth", "notional")
EXPECTED_BANDS = (-5, -4, -3, -2, -1, 1, 2, 3, 4, 5)
REPORT_PATH = Path("reports/market_behavior_source_pilot.json")


def expected_checksum(path):
    value = Path(path).read_text(encoding="utf-8").strip().split()[0].lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("invalid checksum file")
    return value


def actual_checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_timestamp(value):
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=timezone.utc)


def _finite_positive(value, label):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"invalid {label}")
    return number


def load_snapshots(path):
    snapshots = defaultdict(dict)
    row_count = 0
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError("archive must contain exactly one CSV")
        with archive.open(names[0]) as binary:
            import io
            reader = csv.DictReader(io.TextIOWrapper(binary, encoding="utf-8-sig"))
            if tuple(reader.fieldnames or ()) != EXPECTED_COLUMNS:
                raise ValueError("unexpected bookDepth schema")
            for row in reader:
                timestamp = parse_timestamp(row["timestamp"])
                band = int(row["percentage"])
                if band not in EXPECTED_BANDS:
                    raise ValueError("unexpected percentage band")
                if band in snapshots[timestamp]:
                    raise ValueError("duplicate timestamp/band row")
                snapshots[timestamp][band] = {
                    "depth": _finite_positive(row["depth"], "depth"),
                    "notional": _finite_positive(row["notional"], "notional"),
                }
                row_count += 1
    return snapshots, row_count


def monotonic(snapshot, field):
    bid = [snapshot[band][field] for band in (-1, -2, -3, -4, -5)]
    ask = [snapshot[band][field] for band in (1, 2, 3, 4, 5)]
    return all(right >= left for left, right in zip(bid, bid[1:])) and all(
        right >= left for left, right in zip(ask, ask[1:])
    )


def audit_book_depth(archive_path, checksum_path, source_url,
                     liquidation_http_status=None, symbol_probes=None):
    expected = expected_checksum(checksum_path)
    actual = actual_checksum(archive_path)
    snapshots, rows = load_snapshots(archive_path)
    times = sorted(snapshots)
    complete = sum(set(snapshot) == set(EXPECTED_BANDS)
                   for snapshot in snapshots.values())
    monotonic_count = sum(
        set(snapshot) == set(EXPECTED_BANDS)
        and monotonic(snapshot, "depth")
        and monotonic(snapshot, "notional")
        for snapshot in snapshots.values()
    )
    cadences = [
        (right - left).total_seconds() for left, right in zip(times, times[1:])
    ]
    median_cadence = statistics.median(cadences) if cadences else None
    maximum_cadence = max(cadences, default=None)
    gates = {
        "checksum_matches": actual == expected,
        "snapshots_gte_2800": len(times) >= 2800,
        "all_snapshots_have_10_bands": complete == len(times),
        "all_depth_bands_are_monotonic": monotonic_count == len(times),
        "median_cadence_lte_35_seconds": (
            median_cadence is not None and median_cadence <= 35.0
        ),
        "maximum_gap_lte_120_seconds": (
            maximum_cadence is not None and maximum_cadence <= 120.0
        ),
    }
    symbol_probes = dict(symbol_probes or {})
    available_symbols = sorted(
        symbol for symbol, status in symbol_probes.items() if int(status) == 200
    )
    sample_ready = all(gates.values())
    cohort_ready = bool(
        symbol_probes
        and len(available_symbols) == len(symbol_probes)
        and sample_ready
        and liquidation_http_status == 200
    )
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "provider": "Binance Vision",
            "url": source_url,
            "archive_sha256": actual,
            "expected_sha256": expected,
            "data_contract": "PERCENTAGE_BAND_DEPTH_SUMMARY_NOT_FULL_L2",
        },
        "book_depth": {
            "rows": rows,
            "snapshots": len(times),
            "first_timestamp": times[0].isoformat() if times else None,
            "last_timestamp": times[-1].isoformat() if times else None,
            "complete_snapshots": complete,
            "monotonic_snapshots": monotonic_count,
            "median_cadence_seconds": median_cadence,
            "maximum_gap_seconds": maximum_cadence,
            "bands": list(EXPECTED_BANDS),
        },
        "liquidation_archive_probe": {
            "http_status": liquidation_http_status,
            "usable": liquidation_http_status == 200,
        },
        "symbol_coverage_probe": {
            "date_contract": "same archive day as the audited sample",
            "http_status_by_symbol": symbol_probes,
            "available_symbols": available_symbols,
            "available_count": len(available_symbols),
            "requested_count": len(symbol_probes),
            "coverage_ratio": (
                len(available_symbols) / len(symbol_probes) if symbol_probes else None
            ),
        },
        "quality_gates": gates,
        "book_depth_ready_for_feature_feasibility": sample_ready,
        "cohort_ready_for_historical_model_test": cohort_ready,
        "full_l2_order_book_available": False,
        "historical_liquidations_available": liquidation_http_status == 200,
        "model_or_policy_changed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--checksum", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--liquidation-http-status", type=int)
    parser.add_argument(
        "--symbol-probe", action="append", default=[], metavar="SYMBOL=STATUS",
        help="Record a read-only archive availability probe",
    )
    parser.add_argument("--output", default=str(REPORT_PATH))
    args = parser.parse_args()
    probes = {}
    for value in args.symbol_probe:
        symbol, separator, status = value.partition("=")
        if not separator or not symbol or not status.isdigit():
            raise ValueError("--symbol-probe must be SYMBOL=STATUS")
        probes[symbol] = int(status)
    report = audit_book_depth(
        args.archive,
        args.checksum,
        args.source_url,
        args.liquidation_http_status,
        probes,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
