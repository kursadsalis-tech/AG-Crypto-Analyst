"""Build a reproducible, market-wide OKX USDT perpetual research universe.

The universe is selected only with information that would have been available
at the snapshot time.  It deliberately does not use later returns, model
scores, or the user's example symbol list.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


VERSION = "OKX_MARKET_UNIVERSE_V1"


def _decimal(value, default="0"):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def build_universe(
    instruments_payload,
    tickers_payload,
    *,
    snapshot_ms,
    min_quote_volume_usd=5_000_000,
    max_spread_bps=20,
    min_listing_days=90,
):
    if str(instruments_payload.get("code")) != "0":
        raise ValueError("OKX instruments response was not successful")
    if str(tickers_payload.get("code")) != "0":
        raise ValueError("OKX tickers response was not successful")

    tickers = {row.get("instId"): row for row in tickers_payload.get("data", [])}
    selected = []
    excluded_counts = {}

    def exclude(reason):
        excluded_counts[reason] = excluded_counts.get(reason, 0) + 1

    for inst in instruments_payload.get("data", []):
        inst_id = inst.get("instId", "")
        if inst.get("instType") != "SWAP" or inst.get("state") != "live":
            exclude("NOT_LIVE_SWAP")
            continue
        # OKX labels native crypto derivatives as category 1. Categories 3
        # and 4 are equity- and commodity-linked contracts on the same API.
        if str(inst.get("instCategory") or inst.get("category")) != "1":
            exclude("NOT_CRYPTO_CATEGORY")
            continue
        if inst.get("settleCcy") != "USDT" or not inst_id.endswith("-USDT-SWAP"):
            exclude("NOT_LINEAR_USDT")
            continue
        ticker = tickers.get(inst_id)
        if not ticker:
            exclude("NO_TICKER")
            continue
        bid = _decimal(ticker.get("bidPx"))
        ask = _decimal(ticker.get("askPx"))
        last = _decimal(ticker.get("last"))
        volume_contracts = _decimal(ticker.get("vol24h"))
        contract_value = _decimal(inst.get("ctVal"), "1")
        open_24h = _decimal(ticker.get("open24h"))
        if bid <= 0 or ask <= bid or last <= 0:
            exclude("INVALID_TOP_OF_BOOK")
            continue
        midpoint = (bid + ask) / 2
        spread_bps = (ask - bid) / midpoint * Decimal(10_000)
        quote_volume = volume_contracts * contract_value * last
        list_ms = int(inst.get("listTime") or 0)
        listing_days = ((int(snapshot_ms) - list_ms) / 86_400_000) if list_ms else 0
        if quote_volume < Decimal(str(min_quote_volume_usd)):
            exclude("LOW_QUOTE_VOLUME")
            continue
        if spread_bps > Decimal(str(max_spread_bps)):
            exclude("WIDE_SPREAD")
            continue
        if listing_days < min_listing_days:
            exclude("SHORT_LISTING_HISTORY")
            continue
        selected.append({
            "instrument": inst_id,
            "base_currency": inst.get("baseCcy") or inst_id.split("-")[0],
            "contract_value": float(contract_value),
            "contract_value_currency": inst.get("ctValCcy"),
            "listing_timestamp_ms": list_ms,
            "listing_days": round(listing_days, 3),
            "quote_volume_24h_estimate_usd": float(quote_volume),
            "spread_bps": float(spread_bps),
            "change_24h_pct": float((last / open_24h - 1) * 100) if open_24h > 0 else 0.0,
            "last_price": float(last),
        })

    selected.sort(key=lambda row: (-row["quote_volume_24h_estimate_usd"], row["instrument"]))
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_timestamp_ms": int(snapshot_ms),
        "selection_contract": {
            "exchange": "OKX",
            "instrument_type": "USDT_LINEAR_PERPETUAL_SWAP",
            "okx_crypto_instrument_category": "1",
            "min_quote_volume_24h_estimate_usd": min_quote_volume_usd,
            "max_spread_bps": max_spread_bps,
            "min_listing_days": min_listing_days,
            "uses_future_returns_or_model_scores": False,
            "uses_user_example_coin_list": False,
        },
        "selected_count": len(selected),
        "selected": selected,
        "excluded_counts": dict(sorted(excluded_counts.items())),
        "research_role": "DYNAMIC_MARKET_DISCOVERY_UNIVERSE",
        "trading_authority_changed": False,
        "paper_only_changed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instruments", required=True)
    parser.add_argument("--tickers", required=True)
    parser.add_argument("--snapshot-ms", required=True, type=int)
    parser.add_argument("--output", required=True)
    parser.add_argument("--min-volume", type=float, default=5_000_000)
    parser.add_argument("--max-spread-bps", type=float, default=20)
    parser.add_argument("--min-listing-days", type=int, default=90)
    args = parser.parse_args()
    instruments = json.loads(Path(args.instruments).read_text(encoding="utf-8"))
    tickers = json.loads(Path(args.tickers).read_text(encoding="utf-8"))
    result = build_universe(
        instruments,
        tickers,
        snapshot_ms=args.snapshot_ms,
        min_quote_volume_usd=args.min_volume,
        max_spread_bps=args.max_spread_bps,
        min_listing_days=args.min_listing_days,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
