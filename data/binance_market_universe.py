"""Build the canonical Binance USD-M perpetual market universe.

The runtime uses Binance as the single market-data authority.  This builder is
point-in-time and uses no future returns or model outputs.
"""
from __future__ import annotations

from datetime import datetime, timezone


VERSION = "BINANCE_MARKET_UNIVERSE_V1"


def _float(value, default=0.0):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result


def _normalized_instrument(symbol):
    value = str(symbol).upper()
    if not value.endswith("USDT") or len(value) <= 4:
        raise ValueError("unsupported Binance USDT perpetual symbol")
    return f"{value[:-4]}-USDT-SWAP"


def build_universe(
    exchange_info,
    tickers,
    books,
    *,
    snapshot_ms,
    min_quote_volume_usd=5_000_000,
    max_spread_bps=20,
):
    symbols = exchange_info.get("symbols") if isinstance(exchange_info, dict) else None
    if not isinstance(symbols, list):
        raise ValueError("Binance exchangeInfo response was not successful")
    if not isinstance(tickers, list) or not isinstance(books, list):
        raise ValueError("Binance ticker/book response was not successful")

    ticker_map = {str(row.get("symbol") or "").upper(): row for row in tickers}
    book_map = {str(row.get("symbol") or "").upper(): row for row in books}
    selected = []
    excluded_counts = {}

    def exclude(reason):
        excluded_counts[reason] = excluded_counts.get(reason, 0) + 1

    for item in symbols:
        symbol = str(item.get("symbol") or "").upper()
        if (
            item.get("contractType") != "PERPETUAL"
            or item.get("status") != "TRADING"
            or item.get("quoteAsset") != "USDT"
            or item.get("marginAsset") != "USDT"
            or not symbol.endswith("USDT")
        ):
            exclude("NOT_LIVE_LINEAR_USDT_PERPETUAL")
            continue
        ticker = ticker_map.get(symbol)
        book = book_map.get(symbol)
        if not ticker or not book:
            exclude("MISSING_TICKER_OR_BOOK")
            continue
        bid = _float(book.get("bidPrice"))
        ask = _float(book.get("askPrice"))
        last = _float(ticker.get("lastPrice"))
        quote_volume = _float(ticker.get("quoteVolume"))
        open_24h = _float(ticker.get("openPrice"))
        if bid <= 0 or ask <= bid or last <= 0:
            exclude("INVALID_TOP_OF_BOOK")
            continue
        midpoint = (bid + ask) / 2.0
        spread_bps = (ask - bid) / midpoint * 10_000.0
        if quote_volume < float(min_quote_volume_usd):
            exclude("LOW_QUOTE_VOLUME")
            continue
        if spread_bps > float(max_spread_bps):
            exclude("WIDE_SPREAD")
            continue
        onboard_ms = int(item.get("onboardDate") or 0)
        listing_days = (
            (int(snapshot_ms) - onboard_ms) / 86_400_000.0
            if onboard_ms > 0 else None
        )
        selected.append({
            "instrument": _normalized_instrument(symbol),
            "symbol": symbol,
            "base_currency": item.get("baseAsset") or symbol[:-4],
            "contract_value": 1.0,
            "contract_value_currency": item.get("baseAsset") or symbol[:-4],
            "listing_timestamp_ms": onboard_ms,
            "listing_days": round(listing_days, 3) if listing_days is not None else None,
            "quote_volume_24h_estimate_usd": quote_volume,
            "spread_bps": spread_bps,
            "change_24h_pct": (
                100.0 * (last / open_24h - 1.0) if open_24h > 0 else 0.0
            ),
            "last_price": last,
        })

    selected.sort(
        key=lambda row: (-row["quote_volume_24h_estimate_usd"], row["instrument"])
    )
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_timestamp_ms": int(snapshot_ms),
        "selection_contract": {
            "exchange": "BINANCE",
            "instrument_type": "USD_M_LINEAR_PERPETUAL",
            "min_quote_volume_24h_estimate_usd": min_quote_volume_usd,
            "max_spread_bps": max_spread_bps,
            "minimum_listing_age_days": None,
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
