"""Create a deterministic, market-wide learning-data acquisition plan.

This is a data curriculum, not a trading strategy.  It covers liquid leaders
and several liquidity bands without selecting symbols from later returns.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


VERSION = "MARKET_LEARNING_CURRICULUM_V1"
DAY_MS = 86_400_000
DEFAULT_DATES = (
    "2025-08-01", "2025-11-01", "2026-02-01", "2026-05-01", "2026-08-01",
)


def _systematic(rows, count):
    """Pick evenly spaced rows without randomness or outcome information."""
    if not rows or count <= 0:
        return []
    count = min(count, len(rows))
    return [rows[((2 * index + 1) * len(rows)) // (2 * count)] for index in range(count)]


def build_curriculum(universe, dates=DEFAULT_DATES, core_count=8, bands=4, per_band=3):
    contract = universe.get("selection_contract", {})
    if contract.get("uses_future_returns_or_model_scores") is not False:
        raise ValueError("universe does not prove outcome-independent selection")
    rows = list(universe.get("selected", []))
    if not rows:
        raise ValueError("universe has no selected instruments")
    rows.sort(key=lambda row: (-row["quote_volume_24h_estimate_usd"], row["instrument"]))
    core = rows[:core_count]
    remainder = rows[core_count:]
    exploration = []
    band_details = []
    for band in range(bands):
        start = len(remainder) * band // bands
        end = len(remainder) * (band + 1) // bands
        members = remainder[start:end]
        chosen = _systematic(members, per_band)
        exploration.extend(chosen)
        band_details.append({
            "band": band + 1,
            "population": len(members),
            "selected": [row["instrument"] for row in chosen],
        })
    curriculum_rows = core + exploration
    seen = set()
    curriculum_rows = [row for row in curriculum_rows
                       if not (row["instrument"] in seen or seen.add(row["instrument"]))]

    archives = []
    excluded_not_yet_mature = []
    for date in dates:
        date_ms = int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp() * 1000)
        compact = date.replace("-", "")
        for row in curriculum_rows:
            listed_ms = int(row.get("listing_timestamp_ms") or 0)
            if not listed_ms or date_ms - listed_ms < 90 * DAY_MS:
                excluded_not_yet_mature.append({"date": date, "instrument": row["instrument"]})
                continue
            family = row["instrument"].removesuffix("-SWAP")
            archives.append({
                "date": date,
                "instrument": row["instrument"],
                "liquidity_role": "CORE" if row in core else "EXPLORATION",
                "url": (
                    "https://static.okx.com/cdn/okx/match/orderbook/L2/400lv/daily/"
                    f"{compact}/{family}-SWAP-L2orderbook-400lv-{date}.tar.gz"
                ),
                "availability": "UNPROBED",
            })
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_universe_version": universe.get("version"),
        "source_universe_snapshot_timestamp_ms": universe.get("snapshot_timestamp_ms"),
        "selection_contract": {
            "core": f"top {core_count} by contemporaneous estimated quote volume",
            "exploration": f"{per_band} systematic samples from each of {bands} remaining liquidity bands",
            "uses_future_returns_or_model_scores": False,
            "uses_user_example_coin_list": False,
            "minimum_listing_age_at_archive_date_days": 90,
        },
        "core_instruments": [row["instrument"] for row in core],
        "exploration_bands": band_details,
        "curriculum_instruments": [row["instrument"] for row in curriculum_rows],
        "curriculum_instrument_count": len(curriculum_rows),
        "planned_dates": list(dates),
        "archive_candidates": archives,
        "archive_candidate_count": len(archives),
        "excluded_not_yet_mature": excluded_not_yet_mature,
        "historical_bias_warning": (
            "The current live universe omits markets delisted before the snapshot. "
            "Use this only for pipeline learning until point-in-time universe history is added."
        ),
        "ready_for_archive_probe": True,
        "ready_for_unbiased_historical_model_test": False,
        "forward_collection_scope": "ALL_DYNAMIC_UNIVERSE_INSTRUMENTS",
        "paper_only_changed": False,
        "trading_authority_changed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    universe = json.loads(Path(args.universe).read_text(encoding="utf-8"))
    result = build_curriculum(universe)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
