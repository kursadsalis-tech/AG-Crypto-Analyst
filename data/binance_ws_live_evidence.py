"""Pure adapter: official Binance USD-M WebSocket evidence + strict private OI.

No REST, no I/O, no model promotion, no orders. The acquisition layer must
supply actual subscribed frames and private OI samples for the same native ID.
Missing source fields remain None and make the minute INELIGIBLE; neither
funding nor OI is inferred from price or imported from another venue.
"""
from __future__ import annotations

import math

from data.binance_live_evidence import _valid_levels


VERSION = "BINANCE_USDM_WS_LIVE_EVIDENCE_V1"
MAX_DEPTH_AGE_MS = 5000
MAX_TRADE_AGE_MS = 15000
MAX_MARK_AGE_MS = 30000
MAX_OI_AGE_MS = 15000


def _value(x):
    try:
        value = float(x)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _fresh(frame, symbol, event, at_ms, max_age):
    if not isinstance(frame, dict) or frame.get("s") != symbol or frame.get("e") != event:
        return False
    if frame.get("st", 1) != 1:
        return False
    try:
        stamp = int(frame["E"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    return 0 <= at_ms - stamp <= max_age


def summarize_ws_instrument(row, *, depth, trades, mark, oi, received_ms):
    """Require matching exact native USD-M source frames and fresh OI sample."""
    if row.get("exchange") != "BINANCE_USD_M" or row.get("market_type") != "USD_M_PERPETUAL":
        raise ValueError("NON_BINANCE_USDM_ROUTE")
    symbol = row["instrument"]
    if not isinstance(symbol, str) or not symbol.endswith("USDT") or "-" in symbol:
        raise ValueError("NON_NATIVE_BINANCE_SYMBOL")
    at_ms = int(received_ms)
    sources = {k: "MISSING" for k in ("order_book", "recent_trades", "open_interest", "funding")}
    book = {
        "best_bid": None, "best_ask": None, "spread_bps": None,
        "exchange_timestamp_ms": None,
        "depths": {"5": {"imbalance": None}, "20": {"imbalance": None}},
        "coverage": "ACTUAL_20_LEVEL_PUBLIC_WS",
    }
    trade = {
        "count": 0, "buy_notional_usd_estimate": None,
        "sell_notional_usd_estimate": None, "taker_imbalance": None,
        "coverage": "OBSERVED_RECENT_AGG_TRADES_NOT_COMPLETE_VENUE_TAPE",
    }
    open_interest = {"usd": None, "contracts": None, "exchange_timestamp_ms": None}
    funding = {"rate": None, "next_funding_timestamp_ms": None, "exchange_timestamp_ms": None}
    if _fresh(depth, symbol, "depthUpdate", at_ms, MAX_DEPTH_AGE_MS):
        bids = _valid_levels(depth.get("b"), reverse=True)
        asks = _valid_levels(depth.get("a"), reverse=False)
        try:
            transaction_ms = int(depth["T"])
            has_update_id = int(depth["u"]) > 0
        except (KeyError, ValueError, TypeError, OverflowError):
            transaction_ms, has_update_id = 0, False
        if (bids and asks and has_update_id
                and 0 <= at_ms-transaction_ms <= MAX_DEPTH_AGE_MS
                and bids[0][0] < asks[0][0]):
            midpoint = (bids[0][0] + asks[0][0])/2
            for size in (5, 20):
                bid_total = sum(price*qty for price, qty in bids[:size])
                ask_total = sum(price*qty for price, qty in asks[:size])
                total = bid_total + ask_total
                book["depths"][str(size)] = {
                    "imbalance": (bid_total-ask_total)/total if total else 0.,
                    "bid_visible_notional_usd_estimate": bid_total,
                    "ask_visible_notional_usd_estimate": ask_total,
                }
            book.update({
                "best_bid": bids[0][0], "best_ask": asks[0][0],
                "spread_bps": (asks[0][0]-bids[0][0])/midpoint*10000,
                "exchange_timestamp_ms": int(depth["E"]),
            })
            sources["order_book"] = "AVAILABLE"

    # Aggregated trades only: m=true means buyer was maker (aggressive SELL).
    # Each entry must be a distinct observed message, never a fabricated fill.
    if isinstance(trades, list) and trades and len(trades) <= 1000:
        buy, sell, timestamps, identities = 0., 0., [], set()
        valid = True
        for event in trades:
            if not _fresh(event, symbol, "aggTrade", at_ms, 60000):
                valid = False
                break
            if not isinstance(event.get("m"), bool):
                valid = False
                break
            price, size = _value(event.get("p")), _value(event.get("q"))
            try:
                stamp = int(event["T"])
                identity = int(event["a"])
            except (KeyError, TypeError, ValueError, OverflowError):
                valid = False
                break
            if (price is None or size is None or price <= 0 or size <= 0
                    or stamp > at_ms or stamp <= 0 or identity in identities):
                valid = False
                break
            identities.add(identity)
            timestamps.append(stamp)
            if event["m"]:
                sell += price*size
            else:
                buy += price*size
        if valid and timestamps and 0 <= at_ms-max(timestamps) <= MAX_TRADE_AGE_MS:
            total = buy + sell
            trade.update({
                "count": len(timestamps), "buy_notional_usd_estimate": buy,
                "sell_notional_usd_estimate": sell,
                "taker_imbalance": (buy-sell)/total if total else 0.,
            })
            sources["recent_trades"] = "AVAILABLE"

    if _fresh(mark, symbol, "markPriceUpdate", at_ms, MAX_MARK_AGE_MS):
        rate = _value(mark.get("r"))
        try:
            next_funding = int(mark["T"])
        except (KeyError, ValueError, TypeError, OverflowError):
            next_funding = 0
        if rate is not None and next_funding > 0:
            funding = {
                "rate": rate, "exchange_timestamp_ms": int(mark["E"]),
                "next_funding_timestamp_ms": next_funding,
            }
            sources["funding"] = "AVAILABLE"

    if isinstance(oi, dict) and oi.get("symbol") == symbol and oi.get("source_exchange") == "BINANCE_USD_M":
        qty = _value(oi.get("open_interest_native"))
        try:
            stamp = int(oi["exchange_timestamp_ms"])
        except (KeyError, TypeError, ValueError, OverflowError):
            stamp = 0
        if (qty is not None and qty >= 0
                and 0 <= at_ms-stamp <= MAX_OI_AGE_MS
                and sources["order_book"] == "AVAILABLE"):
            midpoint = (book["best_bid"]+book["best_ask"])/2
            open_interest = {
                "usd": qty*midpoint, "contracts": qty,
                "exchange_timestamp_ms": stamp,
            }
            sources["open_interest"] = "AVAILABLE"

    return {
        "instrument": symbol, "source_exchange": "BINANCE_USD_M",
        "collected_timestamp_ms": at_ms,
        "order_book": book, "recent_trades": trade,
        "open_interest": open_interest, "funding": funding,
        "source_states": sources,
        "required_sources_available": all(status == "AVAILABLE" for status in sources.values()),
        "trade_signal_generated": False,
    }
