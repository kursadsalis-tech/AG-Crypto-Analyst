"""Collect canonical Binance USD-M microstructure evidence.

Only compact normalized snapshots leave this module; raw payloads are never
persisted by the runtime observer.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter


VERSION = "BINANCE_LIVE_EVIDENCE_V1"
BASE_URL = "https://fapi.binance.com"
_SESSION = requests.Session()
_SESSION.mount("https://", HTTPAdapter(pool_connections=32, pool_maxsize=32))


def _book_notional(levels, depth):
    total = 0.0
    for row in levels[:depth]:
        total += float(row[0]) * float(row[1])
    return total


def _instrument_symbol(instrument_row):
    symbol = str(instrument_row.get("symbol") or "").upper()
    if symbol:
        return symbol
    instrument = str(instrument_row["instrument"])
    return instrument.replace("-USDT-SWAP", "USDT").replace("-", "")


def summarize_instrument(
    instrument_row, books, trades, open_interest, funding, collected_ms
):
    instrument = instrument_row["instrument"]
    symbol = _instrument_symbol(instrument_row)
    bids = books.get("bids", []) if isinstance(books, dict) else []
    asks = books.get("asks", []) if isinstance(books, dict) else []
    trade_rows = trades if isinstance(trades, list) else []
    best_bid = float(bids[0][0]) if bids else 0.0
    best_ask = float(asks[0][0]) if asks else 0.0
    midpoint = (best_bid + best_ask) / 2.0 if best_bid and best_ask else 0.0

    depths = {}
    for depth in (5, 20, 50):
        bid_value = _book_notional(bids, depth)
        ask_value = _book_notional(asks, depth)
        total = bid_value + ask_value
        depths[str(depth)] = {
            "bid_visible_notional_usd_estimate": bid_value,
            "ask_visible_notional_usd_estimate": ask_value,
            "imbalance": (bid_value - ask_value) / total if total else 0.0,
        }

    buy_notional = sell_notional = 0.0
    trade_timestamps = []
    for row in trade_rows:
        try:
            price = float(row["p"])
            quantity = float(row["q"])
            timestamp = int(row["T"])
        except (KeyError, TypeError, ValueError):
            continue
        value = price * quantity
        # Binance aggTrade m=true => buyer is maker => aggressive sell.
        if bool(row.get("m")):
            sell_notional += value
        else:
            buy_notional += value
        trade_timestamps.append(timestamp)
    trade_total = buy_notional + sell_notional

    oi_contracts = (
        float(open_interest.get("openInterest") or 0.0)
        if isinstance(open_interest, dict) else 0.0
    )
    oi_time = (
        int(open_interest.get("time") or 0)
        if isinstance(open_interest, dict) else 0
    )
    funding_rate = (
        float(funding.get("lastFundingRate") or 0.0)
        if isinstance(funding, dict) else 0.0
    )
    next_funding = (
        int(funding.get("nextFundingTime") or 0)
        if isinstance(funding, dict) else 0
    )
    funding_time = (
        int(funding.get("time") or 0)
        if isinstance(funding, dict) else 0
    )
    book_time = int(books.get("E") or books.get("T") or 0) if isinstance(books, dict) else 0

    source_states = {
        "order_book": "AVAILABLE" if bids and asks else "MISSING",
        "recent_trades": "AVAILABLE" if trade_rows else "MISSING",
        "open_interest": "AVAILABLE" if oi_contracts > 0 else "MISSING",
        "funding": "AVAILABLE" if isinstance(funding, dict) and funding else "MISSING",
        "liquidations": "SEPARATE_WEBSOCKET",
    }
    required_available = all(
        source_states[key] == "AVAILABLE"
        for key in ("order_book", "recent_trades", "open_interest", "funding")
    )
    return {
        "instrument": instrument,
        "symbol": symbol,
        "collected_timestamp_ms": int(collected_ms),
        "source_states": source_states,
        "required_sources_available": required_available,
        "order_book": {
            "exchange_timestamp_ms": book_time,
            "age_ms": int(collected_ms) - book_time if book_time else None,
            "levels_bid": len(bids),
            "levels_ask": len(asks),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread_bps": (
                (best_ask - best_bid) / midpoint * 10_000.0
                if midpoint else None
            ),
            "depths": depths,
        },
        "recent_trades": {
            "count": len(trade_rows),
            "newest_exchange_timestamp_ms": (
                max(trade_timestamps) if trade_timestamps else 0
            ),
            "oldest_exchange_timestamp_ms": (
                min(trade_timestamps) if trade_timestamps else 0
            ),
            "buy_notional_usd_estimate": buy_notional,
            "sell_notional_usd_estimate": sell_notional,
            "taker_imbalance": (
                (buy_notional - sell_notional) / trade_total
                if trade_total else 0.0
            ),
        },
        "open_interest": {
            "exchange_timestamp_ms": oi_time,
            "contracts": oi_contracts,
            "base_currency": oi_contracts,
            "usd": oi_contracts * midpoint if midpoint else 0.0,
        },
        "funding": {
            "rate": funding_rate,
            "next_funding_timestamp_ms": next_funding,
            "exchange_timestamp_ms": funding_time,
            "observation_time_basis": "BINANCE_PREMIUM_INDEX",
        },
        "trade_signal_generated": False,
    }


def fetch_json(path, params=None, timeout=None):
    timeout = float(
        timeout or os.getenv("AGCA_BINANCE_REQUEST_TIMEOUT_SECONDS", "3")
    )
    try:
        response = _SESSION.get(
            f"{BASE_URL}{path}", params=params or {}, timeout=timeout,
            headers={"User-Agent": "AG-Crypto-Analyst/binance-live-evidence-v1",
                     "Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()
    except requests.Timeout:
        return {"_transport_error": "TIMEOUT"}
    except (requests.RequestException, ValueError):
        return {"_transport_error": "ERROR"}


def collect(route, universe, workers=8, fetch=fetch_json):
    universe_rows = {
        row["instrument"]: row for row in universe.get("selected", [])
    }
    instruments = route.get("deep_stream_instruments", [])
    request_contracts = {
        "books": ("/fapi/v1/depth", lambda symbol: {
            "symbol": symbol, "limit": 50,
        }),
        "trades": ("/fapi/v1/aggTrades", lambda symbol: {
            "symbol": symbol, "limit": 100,
        }),
        "open_interest": ("/fapi/v1/openInterest", lambda symbol: {
            "symbol": symbol,
        }),
        "funding": ("/fapi/v1/premiumIndex", lambda symbol: {
            "symbol": symbol,
        }),
    }
    payloads = {instrument: {} for instrument in instruments}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for instrument in instruments:
            row = universe_rows[instrument]
            symbol = _instrument_symbol(row)
            for name, (path, params) in request_contracts.items():
                future = pool.submit(fetch, path, params(symbol))
                futures[future] = (instrument, name)
        for future in as_completed(futures):
            instrument, name = futures[future]
            try:
                payloads[instrument][name] = future.result()
            except Exception:
                payloads[instrument][name] = {"_transport_error": "EXCEPTION"}

    collected_ms = int(time.time() * 1000)
    results = []
    for instrument in instruments:
        row = universe_rows[instrument]
        data = payloads[instrument]
        results.append(summarize_instrument(
            row,
            books=data.get("books") if isinstance(data.get("books"), dict) else {},
            trades=data.get("trades") if isinstance(data.get("trades"), list) else [],
            open_interest=(
                data.get("open_interest")
                if isinstance(data.get("open_interest"), dict) else {}
            ),
            funding=(
                data.get("funding")
                if isinstance(data.get("funding"), dict) else {}
            ),
            collected_ms=collected_ms,
        ))
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": "BINANCE",
        "instrument_count": len(results),
        "complete_instrument_count": sum(
            row["required_sources_available"] for row in results
        ),
        "instruments": results,
        "raw_payloads_persisted": False,
        "liquidation_contract_state": "SEPARATE_PUBLIC_WEBSOCKET",
        "trade_signal_generated": False,
        "paper_only_changed": False,
        "trading_authority_changed": False,
    }
