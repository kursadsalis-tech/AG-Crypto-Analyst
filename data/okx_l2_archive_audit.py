"""Stream-validate one OKX historical 400-level L2 archive.

The validator reads the compressed tar member without extracting it into the
repository.  It checks the event schema and timestamp ordering only; it does
not calculate a trading feature, train a model, or change product policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import tarfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


VERSION = "OKX_L2_ARCHIVE_AUDIT_V1"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid_levels(levels):
    if not isinstance(levels, list):
        return False
    for level in levels:
        if not isinstance(level, list) or len(level) != 3:
            return False
        try:
            price, quantity = float(level[0]), float(level[1])
            orders = int(level[2])
        except (TypeError, ValueError):
            return False
        if not math.isfinite(price) or not math.isfinite(quantity):
            return False
        if price < 0 or quantity < 0 or orders < 0:
            return False
        if price == 0 and (quantity != 0 or orders != 0):
            return False
    return True


def audit_archive(path, expected_instrument=None):
    actions = Counter()
    row_count = 0
    invalid_rows = 0
    backwards = 0
    first_timestamp = None
    last_timestamp = None
    maximum_gap_ms = 0
    first_action = None
    first_snapshot_ask_levels = None
    first_snapshot_bid_levels = None
    member_name = None
    with tarfile.open(path, mode="r|gz") as archive:
        members = []
        for member in archive:
            if not member.isfile():
                continue
            members.append(member.name)
            if len(members) > 1:
                raise ValueError("archive must contain exactly one data file")
            member_name = member.name
            handle = archive.extractfile(member)
            if handle is None:
                raise ValueError("unable to stream archive member")
            for binary in handle:
                row_count += 1
                try:
                    row = json.loads(binary)
                    instrument = row["instId"]
                    action = row["action"]
                    timestamp = int(row["ts"])
                    asks = row["asks"]
                    bids = row["bids"]
                    valid = (
                        action in {"snapshot", "update"}
                        and (expected_instrument is None
                             or instrument == expected_instrument)
                        and valid_levels(asks)
                        and valid_levels(bids)
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    valid = False
                if not valid:
                    invalid_rows += 1
                    continue
                if first_action is None:
                    first_action = action
                if action == "snapshot" and first_snapshot_ask_levels is None:
                    first_snapshot_ask_levels = len(asks)
                    first_snapshot_bid_levels = len(bids)
                actions[action] += 1
                if last_timestamp is not None:
                    delta = timestamp - last_timestamp
                    if delta < 0:
                        backwards += 1
                    else:
                        maximum_gap_ms = max(maximum_gap_ms, delta)
                if first_timestamp is None:
                    first_timestamp = timestamp
                last_timestamp = timestamp
    if member_name is None:
        raise ValueError("archive contains no data file")
    span_hours = (
        (last_timestamp - first_timestamp) / 3_600_000.0
        if first_timestamp is not None and last_timestamp is not None else 0.0
    )
    gates = {
        "rows_gte_100000": row_count >= 100_000,
        "first_event_is_snapshot": first_action == "snapshot",
        "snapshot_has_400_ask_levels": first_snapshot_ask_levels == 400,
        "snapshot_has_400_bid_levels": first_snapshot_bid_levels == 400,
        "has_incremental_updates": actions["update"] > 0,
        "all_rows_schema_valid": invalid_rows == 0,
        "timestamps_never_go_backwards": backwards == 0,
        "covers_at_least_23_hours": span_hours >= 23.0,
    }
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "archive": {
            "path_name": Path(path).name,
            "sha256": sha256(path),
            "compressed_size_bytes": Path(path).stat().st_size,
            "member_name": member_name,
        },
        "instrument": expected_instrument,
        "rows": row_count,
        "actions": dict(actions),
        "invalid_rows": invalid_rows,
        "backwards_timestamps": backwards,
        "first_timestamp_ms": first_timestamp,
        "last_timestamp_ms": last_timestamp,
        "span_hours": span_hours,
        "maximum_observed_gap_ms": maximum_gap_ms,
        "first_snapshot_ask_levels": first_snapshot_ask_levels,
        "first_snapshot_bid_levels": first_snapshot_bid_levels,
        "quality_gates": gates,
        "schema_and_sequence_ready": all(gates.values()),
        "ready_for_historical_model_test": False,
        "next_gate": "MULTI_DAY_MULTI_COIN_COVERAGE_AND_GAP_AUDIT",
        "model_or_policy_changed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--instrument")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = audit_archive(args.archive, args.instrument)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
