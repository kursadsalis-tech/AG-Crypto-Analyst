"""Build a Binance-native dynamic universe from VALIDATED cache + WebSocket data.

The all-market streams are incremental, not complete snapshots. Every retained
symbol must be an actual Binance USD-M contract, with separately fresh book and
24h mini ticker evidence. Missing updates are never silently imputed or aliased.
This module performs no I/O; the live observer must maintain its bounded caches.
"""
from __future__ import annotations

from datetime import datetime, timezone

from data.binance_market_universe import build_universe


VERSION = "BINANCE_USDM_WS_UNIVERSE_V1"
MAX_METADATA_AGE_MS = 7_200_000


def from_ws_caches(exchange_info, metadata_fetched_at, mini_by_symbol,
                   book_by_symbol, *, now_ms, **universe_policy):
    """Convert real WS event fields to the existing pure native-universe gate.

    The calling collector must independently validate cache payload digest
    using paper_trading.exchange_metadata.load_cache before providing it here.
    Event timestamps, market type and exact contract IDs are validated again.
    """
    if not isinstance(exchange_info, dict) or not isinstance(exchange_info.get("symbols"), list):
        raise ValueError("EXCHANGE_INFO_CACHE_UNAVAILABLE")
    if not isinstance(now_ms, int) or now_ms <= 0:
        raise ValueError("INVALID_SNAPSHOT_CLOCK")
    try:
        stamp = datetime.fromisoformat(str(metadata_fetched_at).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("NAIVE_METADATA_TIMESTAMP")
        meta_ms = int(stamp.astimezone(timezone.utc).timestamp() * 1000)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("INVALID_METADATA_TIMESTAMP") from exc
    if not 0 <= now_ms - meta_ms <= MAX_METADATA_AGE_MS:
        raise ValueError("STALE_EXCHANGE_INFO_CACHE")
    if not isinstance(mini_by_symbol, dict) or not isinstance(book_by_symbol, dict):
        raise ValueError("UNAVAILABLE_WS_CACHE")
    # Authoritative trading inventory, not names guessed from venue tickers.
    valid = {
        row["symbol"] for row in exchange_info["symbols"]
        if isinstance(row, dict) and isinstance(row.get("symbol"), str)
        and row.get("status") == "TRADING"
        and row.get("contractType") == "PERPETUAL"
        and row.get("quoteAsset") == "USDT"
        and row.get("marginAsset") == "USDT"
    }
    tickers, books = [], []
    for symbol in sorted(valid):
        mini, book = mini_by_symbol.get(symbol), book_by_symbol.get(symbol)
        if not isinstance(mini, dict) or not isinstance(book, dict):
            continue
        if (mini.get("e") != "24hrMiniTicker" or book.get("e") != "bookTicker"
                or mini.get("st") != 1 or book.get("st") != 1
                or mini.get("s") != symbol or book.get("s") != symbol):
            continue
        try:
            # The market mini ticker supplies O/C/Q and event clock E.
            # No source provides a complete all-market snapshot on connect.
            opened, close, volume = (
                float(mini["o"]), float(mini["c"]), float(mini["q"])
            )
            import math
            if not all(math.isfinite(x) and x > 0 for x in (opened, close, volume)):
                continue
            mini_stamp, book_stamp = int(mini["E"]), int(book["E"])
        except (KeyError, ValueError, TypeError, OverflowError):
            continue
        if not 0 <= now_ms - mini_stamp <= 60_000:
            continue
        if not 0 <= now_ms - book_stamp <= 5_000:
            continue
        tickers.append({
            "symbol": symbol, "lastPrice": mini["c"],
            "quoteVolume": mini["q"],
            "priceChangePercent": str((close/opened-1)*100),
            "closeTime": mini_stamp,
        })
        books.append({
            "symbol": symbol, "bidPrice": book.get("b"),
            "askPrice": book.get("a"),
            "bidQty": book.get("B"), "askQty": book.get("A"),
            "time": book_stamp, "st": 1,
        })
    report = build_universe(
        exchange_info, tickers, books, snapshot_ms=now_ms, **universe_policy,
    )
    report["version"] = VERSION
    report["selection_contract"]["discovery_transport"] = "BINANCE_USDM_WS_INCREMENTAL"
    report["selection_contract"]["metadata_transport"] = "VALIDATED_EXISTING_CACHE"
    report["selection_contract"]["all_market_coverage"] = "FRESH_UPDATES_ONLY_NOT_FULL_SNAPSHOT"
    report["received_fresh_ws_pair_count"] = len(tickers)
    return report
