"""Build the canonical Binance USD-M perpetual market universe from WebSocket tickers.

The active runtime never requires the restricted USD-M REST market-data API.
The all-market 24h ticker stream provides the broad discovery snapshot.
"""
from __future__ import annotations

from datetime import datetime, timezone


VERSION = "BINANCE_WS_MARKET_UNIVERSE_V2"


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _instrument(symbol):
    value = str(symbol).upper()
    if not value.endswith("USDT") or "_" in value or len(value) <= 4:
        raise ValueError("unsupported Binance USD-M perpetual symbol")
    return f"{value[:-4]}-USDT-SWAP"


def build_universe(
    tickers,
    *,
    snapshot_ms,
    min_quote_volume_usd=5_000_000,
    max_spread_bps=20,
):
    if not isinstance(tickers, list):
        raise ValueError("Binance all-market ticker snapshot must be a list")

    selected = []
    excluded_counts = {}

    def exclude(reason):
        excluded_counts[reason] = excluded_counts.get(reason, 0) + 1

    for row in tickers:
        if not isinstance(row, dict):
            exclude("INVALID_TICKER_ROW")
            continue
        symbol = str(row.get("s") or row.get("symbol") or "").upper()
        # USD-M delivery contracts carry suffixes like BTCUSDT_YYMMDD.  The
        # perpetual symbol itself ends exactly in USDT.
        if not symbol.endswith("USDT") or "_" in symbol or len(symbol) <= 4:
            exclude("NOT_USDT_PERPETUAL_SYMBOL")
            continue

        last = _float(row.get("c", row.get("lastPrice")))
        open_24h = _float(row.get("o", row.get("openPrice")))
        quote_volume = _float(row.get("q", row.get("quoteVolume")))
        bid = _float(row.get("b", row.get("bidPrice")))
        ask = _float(row.get("a", row.get("askPrice")))
        if last <= 0 or bid <= 0 or ask <= bid:
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

        selected.append({
            "instrument": _instrument(symbol),
            "symbol": symbol,
            "base_currency": symbol[:-4],
            "contract_value": 1.0,
            "contract_value_currency": symbol[:-4],
            "listing_timestamp_ms": None,
            "listing_days": None,
            "quote_volume_24h_estimate_usd": quote_volume,
            "spread_bps": spread_bps,
            "change_24h_pct": (
                100.0 * (last / open_24h - 1.0) if open_24h > 0 else 0.0
            ),
            "last_price": last,
        })

    selected.sort(
        key=lambda item: (-item["quote_volume_24h_estimate_usd"], item["instrument"])
    )
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_timestamp_ms": int(snapshot_ms),
        "selection_contract": {
            "exchange": "BINANCE",
            "transport": "USD_M_WEBSOCKET_ALL_MARKET_TICKER",
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
