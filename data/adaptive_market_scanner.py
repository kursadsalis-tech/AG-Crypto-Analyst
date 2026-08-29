"""Route scarce deep-market-data slots across a broad live universe.

This module produces attention candidates, not trade signals.  It keeps broad
scanning cheap and reserves full-depth processing for a small rotating set.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


VERSION = "ADAPTIVE_MARKET_SCANNER_V1"
CONTEXT_INSTRUMENTS = ("BTC-USDT-SWAP", "ETH-USDT-SWAP")


def _percentile_ranks(rows, key, reverse=False):
    ordered = sorted(rows, key=lambda row: (row[key], row["instrument"]), reverse=reverse)
    denominator = max(1, len(ordered) - 1)
    return {row["instrument"]: index / denominator for index, row in enumerate(ordered)}


def build_attention_route(universe, opportunity_slots=10):
    rows = list(universe.get("selected", []))
    if not rows:
        raise ValueError("dynamic universe is empty")
    movement_rows = [{**row, "absolute_change_24h_pct": abs(row.get("change_24h_pct", 0.0))}
                     for row in rows]
    movement_rank = _percentile_ranks(movement_rows, "absolute_change_24h_pct")
    volume_rank = _percentile_ranks(movement_rows, "quote_volume_24h_estimate_usd")
    # Lower spread is better, so its ascending percentile is inverted.
    spread_rank = _percentile_ranks(movement_rows, "spread_bps")
    scored = []
    for row in movement_rows:
        instrument = row["instrument"]
        score = (
            0.50 * movement_rank[instrument]
            + 0.30 * volume_rank[instrument]
            + 0.20 * (1.0 - spread_rank[instrument])
        )
        scored.append({
            "instrument": instrument,
            "attention_score": round(score, 8),
            "absolute_change_24h_pct": row["absolute_change_24h_pct"],
            "quote_volume_24h_estimate_usd": row["quote_volume_24h_estimate_usd"],
            "spread_bps": row["spread_bps"],
            "role": "OPPORTUNITY_CANDIDATE",
        })
    scored.sort(key=lambda row: (-row["attention_score"], row["instrument"]))
    opportunity = [row for row in scored if row["instrument"] not in CONTEXT_INSTRUMENTS]
    opportunity = opportunity[:opportunity_slots]
    context = [instrument for instrument in CONTEXT_INSTRUMENTS
               if any(row["instrument"] == instrument for row in rows)]
    deep = context + [row["instrument"] for row in opportunity]
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_universe_snapshot_timestamp_ms": universe.get("snapshot_timestamp_ms"),
        "broad_scan_instrument_count": len(rows),
        "context_instruments": context,
        "opportunity_candidates": opportunity,
        "deep_stream_instruments": deep,
        "deep_stream_instrument_count": len(deep),
        "routing_contract": {
            "purpose": "ATTENTION_ROUTING_NOT_TRADE_SIGNAL",
            "movement_weight": 0.50,
            "liquidity_weight": 0.30,
            "tight_spread_weight": 0.20,
            "uses_future_returns_or_model_scores": False,
            "uses_user_example_coin_list": False,
        },
        "storage_contract": {
            "persist_raw_order_book": False,
            "raw_deep_stream_buffer": "MEMORY_ONLY_ROLLING_WINDOW",
            "persisted_form": "ONE_MINUTE_CAUSAL_FEATURES_AND_PROVENANCE",
            "historical_archives": "LOCAL_TRANSIENT_ONE_AT_A_TIME_DELETE_AFTER_EXTRACTION",
            "upload_historical_archives_to_runtime_or_supabase": False,
        },
        "trade_signal_generated": False,
        "paper_only_changed": False,
        "trading_authority_changed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--opportunity-slots", type=int, default=10)
    args = parser.parse_args()
    universe = json.loads(Path(args.universe).read_text(encoding="utf-8"))
    result = build_attention_route(universe, args.opportunity_slots)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
