"""Combine audited OKX L2 feature days into one provenance-bound dataset."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from data.okx_l2_archive_audit import sha256
from data.okx_l2_feature_extractor import VERSION


DATASET_VERSION = "OKX_L2_CAUSAL_DATASET_V1"


def combine(paths):
    rows = []
    fieldnames = None
    seen = set()
    sources = []
    instruments = set()
    for source in paths:
        path = Path(source)
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            current_fields = tuple(reader.fieldnames or ())
            if fieldnames is None:
                fieldnames = current_fields
            elif current_fields != fieldnames:
                raise ValueError("feature schemas differ")
            count = 0
            for row in reader:
                if row.get("version") != VERSION:
                    raise ValueError("unexpected feature version")
                key = (row["instrument"], row["boundary_time"])
                if key in seen:
                    raise ValueError("duplicate instrument/boundary")
                seen.add(key)
                instruments.add(row["instrument"])
                rows.append(row)
                count += 1
        sources.append({
            "path_name": path.name,
            "sha256": sha256(path),
            "rows": count,
        })
    if not rows or fieldnames is None:
        raise ValueError("no feature rows")
    rows.sort(key=lambda row: (row["boundary_time"], row["instrument"]))
    return fieldnames, rows, sources, sorted(instruments)


def write(output_path, manifest_path, input_paths):
    fieldnames, rows, sources, instruments = combine(input_paths)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    gates = {
        "at_least_6_source_days": len(sources) >= 6,
        "at_least_8640_rows": len(rows) >= 8640,
        "single_instrument_pilot": len(instruments) == 1,
    }
    manifest = {
        "version": DATASET_VERSION,
        "feature_version": VERSION,
        "rows": len(rows),
        "source_days": len(sources),
        "instruments": instruments,
        "first_boundary": rows[0]["boundary_time"],
        "last_boundary": rows[-1]["boundary_time"],
        "sources": sources,
        "dataset_sha256": sha256(output),
        "quality_gates": gates,
        "single_coin_pilot_ready": all(gates.values()),
        "ready_for_historical_model_test": False,
        "next_gate": "MULTI_COIN_COVERAGE_WITH_FIXED_DATE_COHORT",
        "model_or_policy_changed": False,
    }
    manifest_file = Path(manifest_path)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()
    print(json.dumps(write(args.output, args.manifest, args.input),
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
