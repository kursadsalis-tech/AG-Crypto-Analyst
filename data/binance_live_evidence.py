"""Bounded Binance-native REST snapshots for the existing closed-minute observer.

One Binance USD-M symbol stays unchanged through discovery, evidence, risk,
and PAPER. Do not infer another venue's contract identity. The snapshots
are observation-only; missing or late source data fails learning eligibility.
"""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


VERSION = "BINANCE_USDM_LIVE_EVIDENCE_V1"
BASE_URL = "https://fapi.binance.com/fapi/v1"
MAX_REST_RTT_MS = 2_000
MAX_BOOK_AGE_MS = 5_000
MAX_OI_AGE_MS = 15_000
MAX_FUNDING_AGE_MS = 30_000
MAX_TRADE_AGE_MS = 15_000


def _float(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _timestamp(value, received_ms, max_age):
    try:
        stamp = int(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return stamp if 0 <= received_ms - stamp <= max_age else None


def fetch_json(path, params):
    """One fixed-path public Binance GET; never accept user-provided URLs."""
    if path not in {"depth", "trades", "openInterest", "premiumIndex"}:
        raise ValueError("UNAPPROVED_BINANCE_MARKET_PATH")
    started = int(time.time() * 1000)
    try:
        result = requests.get(
            f"{BASE_URL}/{path}", params=params, timeout=3,
            headers={"User-Agent": "AGCA-PAPER-BINANCE-OBSERVER/1"},
        )
        result.raise_for_status()
        payload = result.json()
    except (requests.RequestException, ValueError):
        payload = None
    received = int(time.time() * 1000)
    return {"payload": payload, "received_ms": received,
            "round_trip_ms": received - started}


def _valid_levels(raw, reverse):
    if not isinstance(raw, list) or len(raw) < 20:
        return None
    result = []
    for row in raw:
        if not isinstance(row, list) or len(row) != 2:
            return None
        price, qty = _float(row[0]), _float(row[1])
        if price is None or qty is None or price <= 0 or qty <= 0:
            return None
        result.append((price, qty))
    if any(
        result[i][0] <= result[i+1][0] if reverse
        else result[i][0] >= result[i+1][0]
        for i in range(len(result) - 1)
    ):
        return None
    return result


def _snapshot_is_timely(response):
    return (isinstance(response, dict)
            and isinstance(response.get("received_ms"), int)
            and isinstance(response.get("round_trip_ms"), int)
            and 0 <= response["round_trip_ms"] <= MAX_REST_RTT_MS)


def summarize_instrument(row, *, depth, trades, open_interest, funding):
    """Translate four exact-symbol Binance REST responses to existing features.

    Depth E is an exchange output timestamp (not request time). The recent
    trades list has no guarantee of a complete 10-second trade window, so
    trade imbalance is explicitly recent-N sampled, not total market flow.
    """
    symbol = row["instrument"]
    snapshots = (depth, trades, open_interest, funding)
    now_ms = max((r.get("received_ms", 0) for r in snapshots if isinstance(r, dict)),
                 default=0)
    sources = {"order_book": "MISSING", "recent_trades": "MISSING",
               "open_interest": "MISSING", "funding": "MISSING",
               "liquidations": "SEPARATE_BINANCE_WEBSOCKET"}
    empty_depths = {str(size): {"bid_visible_notional_usd_estimate": 0.,
                                "ask_visible_notional_usd_estimate": 0.,
                                "imbalance": 0.} for size in (5, 20, 50)}
    book_summary = {"exchange_timestamp_ms": 0, "age_ms": None,
                    "levels_bid": 0, "levels_ask": 0,
                    "best_bid": 0., "best_ask": 0., "spread_bps": None,
                    "depths": empty_depths}
    trade_summary = {"count": 0, "newest_exchange_timestamp_ms": 0,
                     "oldest_exchange_timestamp_ms": 0,
                     "buy_notional_usd_estimate": 0.,
                     "sell_notional_usd_estimate": 0., "taker_imbalance": 0.,
                     "coverage": "RECENT_N_TRADES_SAMPLE_NOT_COMPLETE_WINDOW"}
    oi_summary = {"exchange_timestamp_ms": 0, "contracts": 0.,
                  "base_currency": 0., "usd": 0.}
    funding_summary = {"rate": 0., "next_funding_timestamp_ms": 0,
                       "observation_time_basis": "BINANCE_EXCHANGE_TIME"}

    if _snapshot_is_timely(depth):
        p = depth.get("payload")
        if isinstance(p, dict):
            stamp = _timestamp(p.get("E"), depth["received_ms"], MAX_BOOK_AGE_MS)
            tx_stamp = _timestamp(p.get("T"), depth["received_ms"], MAX_BOOK_AGE_MS)
            bids = _valid_levels(p.get("bids"), reverse=True)
            asks = _valid_levels(p.get("asks"), reverse=False)
            if (stamp is not None and tx_stamp is not None and bids and asks
                    and bids[0][0] < asks[0][0] and p.get("lastUpdateId")):
                midpoint = (bids[0][0] + asks[0][0]) / 2
                for size in (5, 20, 50):
                    bid_total = sum(price * qty for price, qty in bids[:size])
                    ask_total = sum(price * qty for price, qty in asks[:size])
                    total = bid_total + ask_total
                    empty_depths[str(size)] = {
                        "bid_visible_notional_usd_estimate": bid_total,
                        "ask_visible_notional_usd_estimate": ask_total,
                        "imbalance": (bid_total-ask_total)/total if total else 0.0,
                    }
                book_summary = {
                    "exchange_timestamp_ms": stamp,
                    "age_ms": depth["received_ms"] - stamp,
                    "levels_bid": len(bids), "levels_ask": len(asks),
                    "best_bid": bids[0][0], "best_ask": asks[0][0],
                    "spread_bps": (asks[0][0]-bids[0][0])/midpoint*10_000,
                    "depths": empty_depths,
                }
                sources["order_book"] = "AVAILABLE"

    if _snapshot_is_timely(trades) and isinstance(trades.get("payload"), list):
        buy, sell, stamps = 0., 0., []
        rows = trades["payload"]
        valid = bool(rows)
        for trade in rows:
            if not isinstance(trade, dict) or not isinstance(trade.get("isBuyerMaker"), bool):
                valid = False
                break
            stamp = _timestamp(trade.get("time"), trades["received_ms"], 60_000)
            price, qty = _float(trade.get("price")), _float(trade.get("qty"))
            if stamp is None or price is None or qty is None or price <= 0 or qty <= 0:
                valid = False
                break
            stamps.append(stamp)
            if trade["isBuyerMaker"]:
                sell += price*qty
            else:
                buy += price*qty
        if valid and stamps and trades["received_ms"] - max(stamps) <= MAX_TRADE_AGE_MS:
            total = buy + sell
            trade_summary.update({
                "count": len(stamps),
                "newest_exchange_timestamp_ms": max(stamps),
                "oldest_exchange_timestamp_ms": min(stamps),
                "buy_notional_usd_estimate": buy,
                "sell_notional_usd_estimate": sell,
                "taker_imbalance": (buy-sell)/total if total else 0.,
            })
            sources["recent_trades"] = "AVAILABLE"

    if _snapshot_is_timely(open_interest):
        p = open_interest.get("payload")
        if isinstance(p, dict) and p.get("symbol") == symbol:
            stamp = _timestamp(p.get("time"), open_interest["received_ms"], MAX_OI_AGE_MS)
            quantity = _float(p.get("openInterest"))
            # OI is reported in Binance native listed base units. Not the
            # same as an OKX per-contract lot; avoid a synthetic lot mapping.
            if stamp is not None and quantity is not None and quantity >= 0 and sources["order_book"] == "AVAILABLE":
                mid = (book_summary["best_bid"]+book_summary["best_ask"])/2
                oi_summary = {"exchange_timestamp_ms": stamp, "contracts": quantity,
                              "base_currency": quantity, "usd": quantity*mid}
                sources["open_interest"] = "AVAILABLE"

    if _snapshot_is_timely(funding):
        p = funding.get("payload")
        if isinstance(p, dict) and p.get("symbol") == symbol:
            stamp = _timestamp(p.get("time"), funding["received_ms"], MAX_FUNDING_AGE_MS)
            rate = _float(p.get("lastFundingRate"))
            try:
                next_funding = int(p.get("nextFundingTime"))
            except (ValueError, TypeError, OverflowError):
                next_funding = 0
            if stamp is not None and rate is not None and next_funding > 0:
                funding_summary["rate"] = rate
                funding_summary["next_funding_timestamp_ms"] = next_funding
                sources["funding"] = "AVAILABLE"

    return {
        "instrument": symbol, "source_exchange": "BINANCE_USD_M",
        "collected_timestamp_ms": now_ms,
        "source_states": sources,
        "required_sources_available": all(
            sources[k] == "AVAILABLE"
            for k in ("order_book", "recent_trades", "open_interest", "funding")),
        "order_book": book_summary, "recent_trades": trade_summary,
        "open_interest": oi_summary, "funding": funding_summary,
        "trade_signal_generated": False,
    }


def collect(route, universe, *, workers=8, fetch=fetch_json):
    """Fetch only current native Binance route symbols; no DB or order calls."""
    known = {row["instrument"]: row for row in universe.get("selected", [])}
    requested = route.get("deep_stream_instruments", [])
    if len(requested) > 32 or len(set(requested)) != len(requested):
        raise ValueError("UNBOUNDED_OR_DUPLICATED_ATTENTION_ROUTE")
    if any(symbol not in known or known[symbol].get("exchange") != "BINANCE_USD_M"
           for symbol in requested):
        raise ValueError("NON_BINANCE_INSTRUMENT_IN_ROUTE")
    tasks = {
        "depth": ("depth", lambda s: {"symbol": s, "limit": 50}),
        "trades": ("trades", lambda s: {"symbol": s, "limit": 100}),
        "open_interest": ("openInterest", lambda s: {"symbol": s}),
        "funding": ("premiumIndex", lambda s: {"symbol": s}),
    }
    results = {symbol: {} for symbol in requested}
    with ThreadPoolExecutor(max_workers=min(workers, 8)) as pool:
        futures = {pool.submit(fetch, path, args(symbol)): (symbol, name)
                   for symbol in requested for name, (path, args) in tasks.items()}
        for task in as_completed(futures):
            symbol, name = futures[task]
            try:
                results[symbol][name] = task.result()
            except Exception:
                results[symbol][name] = {}
    summaries = [summarize_instrument(known[symbol], **results[symbol])
                 for symbol in requested]
    return {
        "version": VERSION,
        "instrument_count": len(summaries),
        "complete_instrument_count": sum(row["required_sources_available"] for row in summaries),
        "instruments": summaries, "raw_payloads_persisted": False,
        "trade_signal_generated": False, "paper_only_changed": False,
        "trading_authority_changed": False,
    }
