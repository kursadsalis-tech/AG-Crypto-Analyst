"""Collect compact live microstructure evidence without persisting raw feeds."""
from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter


VERSION = "OKX_LIVE_EVIDENCE_V1"
BASE_URL = "https://www.okx.com/api/v5"
_SESSION = requests.Session()
_SESSION.mount("https://", HTTPAdapter(pool_connections=32, pool_maxsize=32))


def _payload_data(payload):
    if str(payload.get("code")) != "0":
        return []
    return payload.get("data", [])


def _book_notional(levels, depth, contract_value):
    return sum(float(row[0]) * float(row[1]) * contract_value for row in levels[:depth])


def summarize_instrument(instrument_row, books, trades, open_interest, funding, collected_ms):
    instrument = instrument_row["instrument"]
    contract_value = float(instrument_row.get("contract_value") or 1.0)
    book_rows = _payload_data(books)
    trade_rows = _payload_data(trades)
    oi_rows = _payload_data(open_interest)
    funding_rows = _payload_data(funding)
    book = book_rows[0] if book_rows else {}
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    best_bid = float(bids[0][0]) if bids else 0.0
    best_ask = float(asks[0][0]) if asks else 0.0
    midpoint = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0
    depths = {}
    for depth in (5, 20, 50):
        bid_value = _book_notional(bids, depth, contract_value)
        ask_value = _book_notional(asks, depth, contract_value)
        total = bid_value + ask_value
        depths[str(depth)] = {
            "bid_visible_notional_usd_estimate": bid_value,
            "ask_visible_notional_usd_estimate": ask_value,
            "imbalance": (bid_value - ask_value) / total if total else 0.0,
        }
    buy_notional = 0.0
    sell_notional = 0.0
    trade_timestamps = []
    for row in trade_rows:
        value = float(row["px"]) * float(row["sz"]) * contract_value
        if row.get("side") == "buy":
            buy_notional += value
        elif row.get("side") == "sell":
            sell_notional += value
        if row.get("ts"):
            trade_timestamps.append(int(row["ts"]))
    trade_total = buy_notional + sell_notional
    oi = oi_rows[0] if oi_rows else {}
    fund = funding_rows[0] if funding_rows else {}
    source_states = {
        "order_book": "AVAILABLE" if bids and asks else "MISSING",
        "recent_trades": "AVAILABLE" if trade_rows else "MISSING",
        "open_interest": "AVAILABLE" if oi else "MISSING",
        "funding": "AVAILABLE" if fund else "MISSING",
        "liquidations": "NOT_IN_REST_CONTRACT",
    }
    required_available = all(source_states[key] == "AVAILABLE" for key in
                             ("order_book", "recent_trades", "open_interest", "funding"))
    return {
        "instrument": instrument,
        "collected_timestamp_ms": int(collected_ms),
        "source_states": source_states,
        "required_sources_available": required_available,
        "order_book": {
            "exchange_timestamp_ms": int(book.get("ts") or 0),
            "age_ms": int(collected_ms) - int(book.get("ts") or 0) if book.get("ts") else None,
            "levels_bid": len(bids), "levels_ask": len(asks),
            "best_bid": best_bid, "best_ask": best_ask,
            "spread_bps": ((best_ask - best_bid) / midpoint * 10_000) if midpoint else None,
            "depths": depths,
        },
        "recent_trades": {
            "count": len(trade_rows),
            "newest_exchange_timestamp_ms": max(trade_timestamps) if trade_timestamps else 0,
            "oldest_exchange_timestamp_ms": min(trade_timestamps) if trade_timestamps else 0,
            "buy_notional_usd_estimate": buy_notional,
            "sell_notional_usd_estimate": sell_notional,
            "taker_imbalance": ((buy_notional - sell_notional) / trade_total) if trade_total else 0.0,
        },
        "open_interest": {
            "exchange_timestamp_ms": int(oi.get("ts") or 0),
            "contracts": float(oi.get("oi") or 0),
            "base_currency": float(oi.get("oiCcy") or 0),
            "usd": float(oi.get("oiUsd") or 0),
        },
        "funding": {
            "rate": float(fund.get("fundingRate") or 0),
            "next_funding_timestamp_ms": int(fund.get("fundingTime") or 0),
            "observation_time_basis": "HTTP_RETRIEVAL_TIME",
        },
        "trade_signal_generated": False,
    }


def fetch_json(path, params, timeout=None):
    timeout = float(timeout or os.getenv("AGCA_OKX_REQUEST_TIMEOUT_SECONDS", "3"))
    url = f"{BASE_URL}/{path}?{urlencode(params)}"
    try:
        response = _SESSION.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.Timeout:
        return {"code": "TRANSPORT_TIMEOUT", "data": []}
    except (requests.RequestException, ValueError):
        return {"code": "TRANSPORT_ERROR", "data": []}


def collect(route, universe, workers=8, fetch=fetch_json):
    universe_rows = {row["instrument"]: row for row in universe.get("selected", [])}
    instruments = route.get("deep_stream_instruments", [])

    requests = {
        "books": ("market/books", lambda instrument: {"instId": instrument, "sz": "50"}),
        "trades": ("market/trades", lambda instrument: {"instId": instrument, "limit": "100"}),
        "open_interest": ("public/open-interest", lambda instrument: {
            "instType": "SWAP", "instId": instrument,
        }),
        "funding": ("public/funding-rate", lambda instrument: {"instId": instrument}),
    }
    payloads = {instrument: {} for instrument in instruments}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for instrument in instruments:
            for name, (path, params) in requests.items():
                future = pool.submit(fetch, path, params(instrument))
                futures[future] = (instrument, name)
        for future in as_completed(futures):
            instrument, name = futures[future]
            try:
                payloads[instrument][name] = future.result()
            except Exception as exc:
                payloads[instrument][name] = {
                    "code": "TRANSPORT_EXCEPTION", "data": [],
                    "error_type": type(exc).__name__,
                }
    collected_ms = int(time.time() * 1000)
    results = [
        summarize_instrument(
            universe_rows[instrument], collected_ms=collected_ms,
            **payloads[instrument],
        )
        for instrument in instruments
    ]
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "instrument_count": len(results),
        "complete_instrument_count": sum(row["required_sources_available"] for row in results),
        "instruments": results,
        "raw_payloads_persisted": False,
        "liquidation_contract_state": "SEPARATE_PUBLIC_WEBSOCKET_VALIDATION_REQUIRED",
        "trade_signal_generated": False,
        "paper_only_changed": False,
        "trading_authority_changed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", required=True)
    parser.add_argument("--universe", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    route = json.loads(Path(args.route).read_text(encoding="utf-8"))
    universe = json.loads(Path(args.universe).read_text(encoding="utf-8"))
    result = collect(route, universe)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
