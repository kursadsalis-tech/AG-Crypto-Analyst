"""Choose a bounded multi-date archive slice without using file size or returns."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


VERSION = "BOUNDED_ARCHIVE_PLAN_V1"


def build_plan(availability, per_date_per_role=2):
    available = availability.get("available", [])
    dates = sorted({row["date"] for row in available})
    roles = ("CORE", "EXPLORATION")
    pools = {
        role: sorted({row["instrument"] for row in available if row["liquidity_role"] == role})
        for role in roles
    }
    lookup = {(row["date"], row["instrument"]): row for row in available}
    chosen = []
    offsets = {role: 0 for role in roles}
    for date in dates:
        for role in roles:
            pool = pools[role]
            picked = 0
            inspected = 0
            while picked < per_date_per_role and inspected < len(pool):
                instrument = pool[(offsets[role] + inspected) % len(pool)]
                row = lookup.get((date, instrument))
                inspected += 1
                if row:
                    chosen.append(row)
                    picked += 1
            offsets[role] = (offsets[role] + inspected) % len(pool) if pool else 0
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "selection_contract": {
            "method": "ROUND_ROBIN_WITHIN_PREDECLARED_LIQUIDITY_ROLES",
            "per_date_per_role": per_date_per_role,
            "uses_archive_file_size_for_selection": False,
            "uses_future_returns_or_model_scores": False,
        },
        "dates": dates,
        "selected": chosen,
        "selected_count": len(chosen),
        "compressed_bytes": sum(row["content_length_bytes"] for row in chosen),
        "ready_for_download": bool(chosen),
        "ready_for_unbiased_historical_model_test": False,
        "paper_only_changed": False,
        "trading_authority_changed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--availability", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source = json.loads(Path(args.availability).read_text(encoding="utf-8"))
    result = build_plan(source)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
