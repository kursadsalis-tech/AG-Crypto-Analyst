"""Build the dynamic Binance USD-M PAPER universe from Binance-native contracts.

This is a pure adapter: it does not subscribe, trade, persist, or infer symbol
equivalence with another venue. The market observer must supply current
official exchangeInfo, 24hr ticker, and bookTicker payloads separately.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


VERSION = "BINANCE_USDM_MARKET_UNIVERSE_V1"


def _number(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def build_universe(
    exchange_info, tickers, books, *,
    snapshot_ms, min_quote_volume_usd=5_000_000, max_spread_bps=20,
    min_listing_days=0, max_book_age_ms=5_000, max_ticker_age_ms=60_000,
):
    """Use exact USD-M symbols; reject missing/stale or mismatched book quotes.

    All-market ticker provides rolling 24h volume/movement, NOT a reliable
    bid/ask; all-market bookTicker supplies bid/ask/quantity and exchange time.
    No OKX-to-Binance mapping, static whitelist, or API activity occurs here.
    """
    if not isinstance(exchange_info, dict) or not isinstance(exchange_info.get("symbols"), list):
        raise ValueError("Binance exchangeInfo missing symbols")
    if not isinstance(tickers, list) or not isinstance(books, list):
        raise ValueError("Binance all-market ticker/bookTicker missing")
    if not isinstance(snapshot_ms, int) or snapshot_ms <= 0:
        raise ValueError("Invalid snapshot clock")
    if min_listing_days < 0 or max_book_age_ms <= 0 or max_ticker_age_ms <= 0:
        raise ValueError("Invalid universe policy")

    ticker_map = {row.get("symbol"): row for row in tickers
                  if isinstance(row, dict) and isinstance(row.get("symbol"), str)}
    book_map = {row.get("symbol"): row for row in books
                if isinstance(row, dict) and isinstance(row.get("symbol"), str)}
    selected, excluded = [], {}

    def reject(code):
        excluded[code] = excluded.get(code, 0) + 1

    for meta in exchange_info["symbols"]:
        if not isinstance(meta, dict):
            reject("INVALID_METADATA")
            continue
        symbol = meta.get("symbol")
        if (not isinstance(symbol, str) or not symbol.endswith("USDT")
                or meta.get("status") != "TRADING"
                or meta.get("contractType") != "PERPETUAL"
                or meta.get("quoteAsset") != "USDT"
                or meta.get("marginAsset") != "USDT"):
            reject("NOT_TRADING_USDT_PERPETUAL")
            continue

        ticker, book = ticker_map.get(symbol), book_map.get(symbol)
        if ticker is None or book is None:
            reject("MISSING_SAME_SYMBOL_QUOTES")
            continue
        # Merged UM/CM feeds may include an 'st' field. When present it must
        # identify USD-M; legacy REST payloads without 'st' are accepted.
        if book.get("st", 1) != 1:
            reject("WRONG_MARKET_TYPE")
            continue

        bid = _number(book.get("bidPrice"))
        ask = _number(book.get("askPrice"))
        bid_qty = _number(book.get("bidQty"))
        ask_qty = _number(book.get("askQty"))
        last = _number(ticker.get("lastPrice"))
        volume = _number(ticker.get("quoteVolume"))
        movement = _number(ticker.get("priceChangePercent"))
        if (any(x is None or x <= 0 for x in (bid, ask, bid_qty, ask_qty, last, volume))
                or ask <= bid or movement is None):
            reject("INVALID_TOP_OR_TICKER")
            continue
        try:
            book_ms = int(book["time"])
            ticker_ms = int(ticker["closeTime"])
            onboard_ms = int(meta.get("onboardDate") or 0)
        except (TypeError, ValueError, KeyError, OverflowError):
            reject("INVALID_EXCHANGE_TIMESTAMP")
            continue
        if (book_ms <= 0 or ticker_ms <= 0
                or not 0 <= snapshot_ms - book_ms <= max_book_age_ms
                or not 0 <= snapshot_ms - ticker_ms <= max_ticker_age_ms):
            reject("STALE_OR_FUTURE_QUOTE")
            continue
        if onboard_ms <= 0 or onboard_ms > snapshot_ms:
            reject("UNKNOWN_OR_FUTURE_LISTING")
            continue
        listing_days = (snapshot_ms - onboard_ms) / 86_400_000
        if listing_days < min_listing_days:
            reject("SHORT_LISTING_HISTORY")
            continue
        if volume < Decimal(str(min_quote_volume_usd)):
            reject("LOW_QUOTE_VOLUME")
            continue

        midpoint = (bid + ask) / 2
        spread_bps = (ask - bid) / midpoint * 10_000
        if spread_bps > Decimal(str(max_spread_bps)):
            reject("WIDE_SPREAD")
            continue
        selected.append({
            "instrument": symbol,  # Binance-native ID, including any 1000 prefix.
            "exchange": "BINANCE_USD_M",
            "market_type": "USD_M_PERPETUAL",
            "base_currency": meta.get("baseAsset"),
            "contract_value": 1.0,  # Binance quantity is in the listed baseAsset units.
            "contract_value_currency": meta.get("baseAsset"),
            "listing_timestamp_ms": onboard_ms,
            "listing_days": round(listing_days, 3),
            "quote_volume_24h_estimate_usd": float(volume),
            "spread_bps": float(spread_bps),
            "change_24h_pct": float(movement),
            "last_price": float(last),
            "bid": float(bid), "ask": float(ask),
            "bid_qty": float(bid_qty), "ask_qty": float(ask_qty),
            "quote_exchange_timestamp_ms": book_ms,
            "ticker_exchange_timestamp_ms": ticker_ms,
        })

    selected.sort(key=lambda row: (-row["quote_volume_24h_estimate_usd"], row["instrument"]))
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_timestamp_ms": snapshot_ms,
        "selection_contract": {
            "exchange": "BINANCE",
            "market_type": "USD_M_PERPETUAL",
            "symbol_authority": "BINANCE_EXCHANGE_INFO_EXACT_SYMBOL",
            "quote_source": "BINANCE_ALL_MARKET_BOOK_TICKER",
            "volume_source": "BINANCE_24HR_TICKER",
            "min_quote_volume_24h_estimate_usd": min_quote_volume_usd,
            "max_spread_bps": max_spread_bps,
            "min_listing_days": min_listing_days,
            "max_book_age_ms": max_book_age_ms,
            "max_ticker_age_ms": max_ticker_age_ms,
            "uses_user_example_coin_list": False,
        },
        "selected_count": len(selected),
        "selected": selected,
        "excluded_counts": dict(sorted(excluded.items())),
        "research_role": "DYNAMIC_MARKET_DISCOVERY_UNIVERSE",
        "trading_authority_changed": False,
        "paper_only_changed": False,
    }
