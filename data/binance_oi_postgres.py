"""Bounded Binance USD-M OI via AGCA's existing private Supabase DB egress.

The execution VM cannot directly reach fapi.binance.com (HTTP 451).
This adapter uses the already-provisioned and independently verified
extensions.http_get transport, ONLY for exact exchangeInfo-authorised symbols.
It performs read-only fixed-endpoint GETs, no public proxy, and no DB writes.
It does not claim a complete market-wide OI feed or a free-rate-limit budget.
"""
from __future__ import annotations

import math
import re
import time
from urllib.parse import urlencode


OI_URL = "https://fapi.binance.com/fapi/v1/openInterest"
NATIVE = re.compile(r"^[A-Z0-9]{5,30}$")
MAX_BATCH = 12
MAX_OI_AGE_MS = 15_000
MAX_NETWORK_RTT_MS = 12_000


class BinanceOIError(RuntimeError):
    pass


def validate_response(response, *, symbol, received_ms, rtt_ms):
    """Verify actual Binance contract, price-independent OI quantity and clock."""
    if not isinstance(response, dict) or response.get("symbol") != symbol:
        raise BinanceOIError("SYMBOL_OR_PAYLOAD_MISMATCH")
    try:
        quantity = float(response["openInterest"])
        stamp = int(response["time"])
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        raise BinanceOIError("INVALID_OI_FIELDS") from exc
    if not math.isfinite(quantity) or quantity < 0:
        raise BinanceOIError("INVALID_OI_QUANTITY")
    if not 0 <= int(received_ms) - stamp <= MAX_OI_AGE_MS:
        raise BinanceOIError("STALE_OR_FUTURE_OI")
    if not 0 <= int(rtt_ms) <= MAX_NETWORK_RTT_MS:
        raise BinanceOIError("EXCESSIVE_OI_LATENCY")
    return {"symbol": symbol, "open_interest_native": quantity,
            "exchange_timestamp_ms": stamp, "received_timestamp_ms": int(received_ms),
            "age_ms": int(received_ms) - stamp, "round_trip_ms": int(rtt_ms),
            "source_exchange": "BINANCE_USD_M", "source": "FAPI_OPEN_INTEREST_VIA_EXISTING_SUPABASE_EGRESS"}


def fetch_batch(database_url, symbols, allowed_symbols, *, timeout_seconds=15,
                now_ms=None):
    """One bounded DB connection, one fixed unauthenticated GET per exact symbol.

    Callers should select a *small* fresh route and budget rate/latency; no
    fallback, retries, concurrent DB fanout, aliasing, or stale-cache reuse.
    """
    ordered = tuple(symbols)
    allowed = frozenset(allowed_symbols)
    if not ordered or len(ordered) > MAX_BATCH or len(set(ordered)) != len(ordered):
        raise BinanceOIError("UNBOUNDED_OR_DUPLICATED_OI_BATCH")
    if any(not isinstance(sym, str) or not NATIVE.fullmatch(sym) or
           not sym.endswith("USDT") or sym not in allowed for sym in ordered):
        raise BinanceOIError("NON_AUTHORITATIVE_OI_SYMBOL")
    if not database_url:
        raise BinanceOIError("SUPABASE_DATABASE_URL_UNAVAILABLE")
    import psycopg
    timeout_ms = max(1000, int(timeout_seconds*1000))
    results = {}
    # Do not reveal remote DSN, headers, response bodies, or connection errors.
    try:
        with psycopg.connect(database_url, connect_timeout=max(1, int(timeout_seconds)),
                            tcp_user_timeout=timeout_ms) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = %s", (str(timeout_ms*MAX_BATCH),))
                cursor.execute("SELECT extensions.http_set_curlopt('CURLOPT_CONNECTTIMEOUT_MS', %s)",
                               (str(timeout_ms),))
                cursor.execute("SELECT extensions.http_set_curlopt('CURLOPT_TIMEOUT_MS', %s)",
                               (str(timeout_ms),))
                for symbol in ordered:
                    url = OI_URL + "?" + urlencode({"symbol": symbol})
                    start = time.monotonic()
                    cursor.execute("SELECT status, content_type, content FROM extensions.http_get(%s)",
                                   (url,))
                    received = int(time.time()*1000) if now_ms is None else int(now_ms)
                    rtt = int((time.monotonic()-start)*1000)
                    result = cursor.fetchone()
                    if not result or result[0] != 200 or str(result[1] or "").split(";", 1)[0].strip().lower() != "application/json":
                        raise BinanceOIError("BINANCE_OI_HTTP_UNAVAILABLE")
                    import json
                    try:
                        payload = json.loads(result[2])
                    except (ValueError, TypeError) as exc:
                        raise BinanceOIError("INVALID_OI_JSON") from exc
                    results[symbol] = validate_response(
                        payload, symbol=symbol, received_ms=received, rtt_ms=rtt)
    except BinanceOIError:
        raise
    except Exception as exc:
        raise BinanceOIError("SUPABASE_OI_TRANSPORT_UNAVAILABLE") from exc
    return results
