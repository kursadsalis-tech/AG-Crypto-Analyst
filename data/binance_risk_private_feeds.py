"""Bounded read-only reference feeds over AGCA's existing private DB egress.

The GCE VM cannot rely on direct Binance REST. Keep two independently owned
BTC reference providers (Binance SPOT and CoinGecko), plus official Binance
SPOT closed hourly bars for the established context basket. No credentialed
API, arbitrary URL/proxy, extra venue/asset alias, or database-table write.
"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlencode

from data.truth_engine import validate_cross_source_prices, validate_closed_klines


SPOT_PRICE_URL = "https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT"
INDEPENDENT_PRICE_URL = (
    "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
)
SPOT_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
VALID_SYMBOL = re.compile(r"^[A-Z0-9]{1,26}USDT$")
MAX_BARS = 170


class PrivateRiskFeedError(RuntimeError):
    pass


def _private_get(database_url, url, *, timeout_seconds=15):
    """Exactly one already-allowlisted URL via existing extensions.http_get."""
    if not database_url:
        raise PrivateRiskFeedError("PRIVATE_REFERENCE_EGRESS_UNAVAILABLE")
    if not 1 <= timeout_seconds <= 20:
        raise PrivateRiskFeedError("INVALID_PRIVATE_REQUEST_TIMEOUT")
    if not (url in (SPOT_PRICE_URL, INDEPENDENT_PRICE_URL)
            or url.startswith(SPOT_KLINES_URL+"?symbol=")
            and url.endswith("&interval=1h&limit=170")
            and VALID_SYMBOL.fullmatch(url[len(SPOT_KLINES_URL+"?symbol="):
                                           -len("&interval=1h&limit=170")])):
        raise PrivateRiskFeedError("UNAPPROVED_PRIVATE_RISK_URL")
    try:
        import psycopg
        ms = int(timeout_seconds * 1000)
        with psycopg.connect(
            database_url, connect_timeout=5, tcp_user_timeout=ms,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"SET LOCAL statement_timeout = '{ms+3000}ms'")
                cursor.execute(
                    "SELECT extensions.http_set_curlopt('CURLOPT_CONNECTTIMEOUT_MS', %s)",
                    (str(ms),),
                )
                cursor.execute(
                    "SELECT extensions.http_set_curlopt('CURLOPT_TIMEOUT_MS', %s)",
                    (str(ms),),
                )
                started = time.monotonic()
                cursor.execute(
                    "SELECT status,content_type,content FROM extensions.http_get(%s)",
                    (url,),
                )
                row = cursor.fetchone()
                if (time.monotonic() - started) > timeout_seconds + 3:
                    raise PrivateRiskFeedError("PRIVATE_REFERENCE_REQUEST_TOO_SLOW")
    except PrivateRiskFeedError:
        raise
    except Exception as exc:
        raise PrivateRiskFeedError("PRIVATE_REFERENCE_REQUEST_FAILED") from exc
    if (not row or row[0] != 200 or
            str(row[1] or "").split(";", 1)[0].lower() != "application/json"):
        raise PrivateRiskFeedError("PRIVATE_REFERENCE_HTTP_UNAVAILABLE")
    try:
        return json.loads(row[2])
    except (ValueError, TypeError) as exc:
        raise PrivateRiskFeedError("INVALID_PRIVATE_REFERENCE_JSON") from exc


def btc_independent_validation(database_url):
    """Keep the existing <=2% independent BTC price discrepancy rule."""
    spot = _private_get(database_url, SPOT_PRICE_URL)
    independent = _private_get(database_url, INDEPENDENT_PRICE_URL)
    try:
        if spot.get("symbol") != "BTCUSDT":
            raise ValueError("Wrong SPOT symbol")
        first = float(spot["price"])
        second = float(independent["bitcoin"]["usd"])
    except (KeyError, ValueError, TypeError, OverflowError, AttributeError) as exc:
        raise PrivateRiskFeedError("PRIVATE_BTC_REFERENCE_SCHEMA") from exc
    deviation = validate_cross_source_prices(
        first, second, source="BINANCE_SPOT_VS_COINGECKO:BTCUSD"
    )
    return {"binance_price": first, "coingecko_price": second,
            "deviation_pct": deviation}


def spot_closed_hourly(database_url, symbol, *, now_ms=None):
    if not isinstance(symbol, str) or not VALID_SYMBOL.fullmatch(symbol):
        raise PrivateRiskFeedError("INVALID_SPOT_RISK_SYMBOL")
    url = SPOT_KLINES_URL+"?"+urlencode({
        "symbol": symbol, "interval": "1h", "limit": MAX_BARS,
    })
    payload = _private_get(database_url, url)
    if not isinstance(payload, list):
        raise PrivateRiskFeedError("INVALID_SPOT_KLINES_PAYLOAD")
    now_ms = int(time.time()*1000) if now_ms is None else int(now_ms)
    try:
        closed = [row for row in payload if isinstance(row, list)
                  and len(row) >= 8 and int(row[6]) < now_ms]
    except (TypeError, ValueError, OverflowError) as exc:
        raise PrivateRiskFeedError("INVALID_SPOT_KLINES_ROWS") from exc
    if len(closed) < 169:
        raise PrivateRiskFeedError("INSUFFICIENT_SPOT_CLOSED_KLINES")
    return validate_closed_klines(
        closed[-169:], source=f"BINANCE_SPOT_PRIVATE_EGRESS:{symbol}",
        minimum_rows=169,
    )
