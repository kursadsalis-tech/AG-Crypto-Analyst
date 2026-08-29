"""Build a same-minute, fixed-symbol OKX L2 microstructure cohort."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from data.okx_l2_archive_audit import sha256
from data.okx_l2_feature_dataset import combine


VERSION = "OKX_L2_FIXED_COHORT_V1"


def build(input_paths, output_path, manifest_path, expected_instruments):
    fields, rows, sources, instruments = combine(input_paths)
    expected = sorted(expected_instruments)
    if instruments != expected:
        raise ValueError("fixed instrument cohort mismatch")
    boundaries = defaultdict(set)
    counts = Counter()
    for row in rows:
        boundaries[row["instrument"]].add(row["boundary_time"])
        counts[row["instrument"]] += 1
    common = set.intersection(*(boundaries[name] for name in expected))
    union = set.union(*(boundaries[name] for name in expected))
    gates = {
        "at_least_5_instruments": len(expected) >= 5,
        "exactly_1440_rows_per_instrument": all(
            counts[name] == 1440 for name in expected
        ),
        "all_instruments_share_same_boundaries": common == union,
        "exactly_1440_common_minutes": len(common) == 1440,
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        "version": VERSION,
        "instruments": expected,
        "rows": len(rows),
        "rows_by_instrument": dict(sorted(counts.items())),
        "common_minutes": len(common),
        "first_boundary": min(common) if common else None,
        "last_boundary": max(common) if common else None,
        "sources": sources,
        "dataset_sha256": sha256(output),
        "quality_gates": gates,
        "fixed_cohort_feature_research_ready": all(gates.values()),
        "ready_for_historical_model_test": False,
        "next_gate": "SAME_FIXED_COHORT_ACROSS_MULTIPLE_PREDECLARED_DATES",
        "excluded_for_archive_unavailability": ["SLP-USDT-SWAP"],
        "deferred_for_bulk_cost": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
        "model_or_policy_changed": False,
    }
    target = Path(manifest_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--instrument", action="append", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    print(json.dumps(build(
        args.input, args.output, args.manifest, args.instrument
    ), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
