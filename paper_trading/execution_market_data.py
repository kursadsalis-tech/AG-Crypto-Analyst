"""Causal public market observations for dynamic PAPER execution.

The behavior universe is dynamic, so PAPER execution must not depend on the
fixed research/forecast candle cohort.  Each position captures its own public
Binance USD-M reference once; later runs may read but never rewrite it.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests


OKX_CANDLES_URL = "https://www.okx.com/api/v5/market/candles"
OKX_TICKER_URL = "https://www.okx.com/api/v5/market/ticker"
BINANCE_SPOT_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
BINANCE_SPOT_TICKER_URL = "https://data-api.binance.vision/api/v3/ticker/price"
BINANCE_SPOT_24H_URL = "https://data-api.binance.vision/api/v3/ticker/24hr"
USER_AGENT = "AG-Crypto-Analyst/paper-execution-observation-v1"
REQUEST_TIMEOUT_SECONDS = 10
MAX_ENTRY_OBSERVATION_DELAY = timedelta(minutes=10)
MAX_CROSS_SOURCE_DEVIATION_PCT = 2.0


class SpotManagementVenueUnavailable(ValueError):
    """Raised when a Binance Spot PAPER fill cannot be managed on OKX Spot."""


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def ensure_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS paper_execution_market_observations (
            position_id INTEGER NOT NULL,
            purpose TEXT NOT NULL CHECK(purpose IN ('ENTRY','EXIT')),
            symbol TEXT NOT NULL,
            expected_time TEXT NOT NULL,
            market_time TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            reference_price REAL NOT NULL,
            quote_volume REAL NOT NULL,
            source TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            real_order_allowed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(position_id,purpose)
        )
    """)


def _parse_binance_row(symbol: str, expected: datetime, purpose: str, row):
    if not isinstance(row, list) or len(row) < 8:
        raise ValueError(f"{symbol}: incomplete USD-M kline")
    opened = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc)
    closed = datetime.fromtimestamp(int(row[6]) / 1000, tz=timezone.utc)
    if purpose == "ENTRY":
        market_time = opened
        price = float(row[1])
        if abs((opened - expected).total_seconds()) > 1:
            raise ValueError(f"{symbol}: exact USD-M entry open unavailable")
    else:
        market_time = closed
        price = float(row[4])
        if abs((closed - expected).total_seconds()) > 1:
            raise ValueError(f"{symbol}: exact USD-M exit close unavailable")
    quote_volume = float(row[7])
    if price <= 0 or quote_volume <= 0:
        raise ValueError(f"{symbol}: non-executable USD-M reference")
    return market_time, price, quote_volume


def _okx_instrument(symbol: str) -> str:
    value = str(symbol).upper()
    if not value.endswith("USDT") or len(value) <= 4:
        raise ValueError(f"{symbol}: unsupported PAPER symbol")
    return f"{value[:-4]}-USDT-SWAP"


def _parse_okx_rows(symbol: str, expected: datetime, purpose: str, rows):
    if not isinstance(rows, list):
        raise ValueError(f"{symbol}: invalid OKX candle response")
    for row in rows:
        if not isinstance(row, list) or len(row) < 9:
            continue
        opened = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc)
        closed = opened + timedelta(hours=1) - timedelta(milliseconds=1)
        market_time = opened if purpose == "ENTRY" else closed
        if abs((market_time - expected).total_seconds()) > 1:
            continue
        if purpose == "EXIT" and str(row[8]) != "1":
            raise ValueError(f"{symbol}: OKX exit candle is not closed")
        price = float(row[1] if purpose == "ENTRY" else row[4])
        quote_volume = float(row[7])
        if price <= 0 or quote_volume <= 0:
            raise ValueError(f"{symbol}: non-executable OKX reference")
        return market_time, price, quote_volume, row
    raise ValueError(f"{symbol}: exact OKX {purpose.lower()} unavailable")


def fetch_reference(symbol: str, expected: datetime, purpose: str):
    if purpose == "ENTRY":
        return fetch_entry_snapshot(symbol, expected)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    okx_response = requests.get(
        OKX_CANDLES_URL,
        params={"instId": _okx_instrument(symbol), "bar": "1H", "limit": 3},
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers=headers,
    )
    okx_response.raise_for_status()
    okx_payload = okx_response.json()
    if not isinstance(okx_payload, dict) or str(okx_payload.get("code")) != "0":
        raise ValueError(f"{symbol}: OKX candle response failed")
    market_time, price, quote_volume, okx_row = _parse_okx_rows(
        symbol, expected, purpose, okx_payload.get("data")
    )

    start = expected if purpose == "ENTRY" else expected - timedelta(hours=1)
    binance_response = requests.get(
        BINANCE_SPOT_KLINES_URL,
        params={
            "symbol": symbol, "interval": "1h",
            "startTime": int(start.timestamp() * 1000), "limit": 1,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers=headers,
    )
    binance_response.raise_for_status()
    binance_payload = binance_response.json()
    if not isinstance(binance_payload, list) or len(binance_payload) != 1:
        raise ValueError(f"{symbol}: Binance cross-check candle missing")
    _, cross_price, _ = _parse_binance_row(
        symbol, expected, purpose, binance_payload[0]
    )
    deviation = abs(price - cross_price) / ((price + cross_price) / 2.0) * 100.0
    if deviation > MAX_CROSS_SOURCE_DEVIATION_PCT:
        raise ValueError(f"{symbol}: execution cross-source deviation too high")
    digest = hashlib.sha256(json.dumps(
        {"okx": okx_row, "binance_spot": binance_payload[0]},
        separators=(",", ":"), ensure_ascii=True, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    return market_time, price, quote_volume, digest


def fetch_entry_snapshot(symbol: str, observed_at: datetime):
    """Capture an immutable, causal market snapshot without hourly waiting."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    okx_response = requests.get(
        OKX_TICKER_URL, params={"instId": _okx_instrument(symbol)},
        timeout=REQUEST_TIMEOUT_SECONDS, headers=headers,
    )
    okx_response.raise_for_status()
    okx_payload = okx_response.json()
    rows = okx_payload.get("data") if isinstance(okx_payload, dict) else None
    if str(okx_payload.get("code")) != "0" or not isinstance(rows, list) or len(rows) != 1:
        raise ValueError(f"{symbol}: OKX ticker response failed")
    row = rows[0]
    price = float(row.get("last", 0))
    base_volume_24h = float(row.get("volCcy24h", 0))
    market_ms = int(row.get("ts", 0))
    market_time = datetime.fromtimestamp(market_ms / 1000, tz=timezone.utc)
    if price <= 0 or base_volume_24h <= 0 or market_ms <= 0:
        raise ValueError(f"{symbol}: non-executable OKX ticker")
    if abs((observed_at - market_time).total_seconds()) > 30:
        raise ValueError(f"{symbol}: stale OKX ticker")

    cross_response = requests.get(
        BINANCE_SPOT_TICKER_URL, params={"symbol": symbol},
        timeout=REQUEST_TIMEOUT_SECONDS, headers=headers,
    )
    cross_response.raise_for_status()
    cross_payload = cross_response.json()
    cross_price = float(cross_payload.get("price", 0)) \
        if isinstance(cross_payload, dict) else 0.0
    if cross_price <= 0:
        raise ValueError(f"{symbol}: Binance ticker cross-check missing")
    deviation = abs(price - cross_price) / ((price + cross_price) / 2.0) * 100.0
    if deviation > MAX_CROSS_SOURCE_DEVIATION_PCT:
        raise ValueError(f"{symbol}: execution cross-source deviation too high")

    # Conservative one-minute share of reported 24h base volume. It is used
    # only by the PAPER slippage model and never grants real-order authority.
    quote_volume = base_volume_24h * price / (24.0 * 60.0)
    digest = hashlib.sha256(json.dumps(
        {"okx": row, "binance_spot": cross_payload},
        separators=(",", ":"), ensure_ascii=True, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    return market_time, price, quote_volume, digest


def _postgres_json_get(database_url: str, url: str):
    import psycopg

    timeout_ms = REQUEST_TIMEOUT_SECONDS * 1000
    with psycopg.connect(
        database_url, connect_timeout=REQUEST_TIMEOUT_SECONDS,
        tcp_user_timeout=timeout_ms,
    ) as con:
        with con.cursor() as cursor:
            cursor.execute(f"SET LOCAL statement_timeout = '{timeout_ms}ms'")
            cursor.execute(
                "SELECT extensions.http_set_curlopt('CURLOPT_CONNECTTIMEOUT_MS', %s)",
                (str(timeout_ms),),
            )
            cursor.execute(
                "SELECT extensions.http_set_curlopt('CURLOPT_TIMEOUT_MS', %s)",
                (str(timeout_ms),),
            )
            cursor.execute(
                "SELECT status,content_type,content FROM extensions.http_get(%s)",
                (url,),
            )
            row = cursor.fetchone()
    content_type = str(row[1] or "").split(";", 1)[0].strip().lower() if row else ""
    if not row or row[0] != 200 or content_type != "application/json":
        raise ValueError("Binance Spot source returned an invalid response")
    return json.loads(row[2])


def fetch_spot_snapshot(symbol: str, observed_at: datetime):
    """Capture Binance Spot PAPER fill evidence with an OKX management check."""
    database_url = os.getenv("AGCA_REMOTE_DATABASE_URL", "")
    if database_url:
        payload = _postgres_json_get(
            database_url, f"{BINANCE_SPOT_24H_URL}?{urlencode({'symbol': symbol})}",
        )
    else:
        response = requests.get(
            BINANCE_SPOT_24H_URL,
            params={"symbol": symbol},
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"{symbol}: Binance spot ticker response failed")
    price = float(payload.get("lastPrice", 0))
    quote_volume_24h = float(payload.get("quoteVolume", 0))
    market_ms = int(payload.get("closeTime", 0))
    market_time = datetime.fromtimestamp(market_ms / 1000, tz=timezone.utc)
    if price <= 0 or quote_volume_24h <= 0 or market_ms <= 0:
        raise ValueError(f"{symbol}: non-executable Binance spot ticker")
    if abs((observed_at - market_time).total_seconds()) > 30:
        raise ValueError(f"{symbol}: stale Binance spot ticker")

    okx_response = requests.get(
        OKX_TICKER_URL,
        params={"instId": f"{symbol[:-4]}-USDT"},
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    okx_response.raise_for_status()
    okx_payload = okx_response.json()
    okx_rows = okx_payload.get("data") if isinstance(okx_payload, dict) else None
    if str(okx_payload.get("code")) != "0" or not isinstance(okx_rows, list) or len(okx_rows) != 1:
        raise SpotManagementVenueUnavailable(
            f"{symbol}: OKX Spot management instrument unavailable"
        )
    okx_row = okx_rows[0]
    okx_price = float(okx_row.get("last", 0))
    okx_base_volume = float(okx_row.get("volCcy24h", 0))
    okx_ms = int(okx_row.get("ts", 0))
    okx_time = datetime.fromtimestamp(okx_ms / 1000, tz=timezone.utc)
    if okx_price <= 0 or okx_base_volume <= 0 or okx_ms <= 0:
        raise SpotManagementVenueUnavailable(f"{symbol}: non-executable OKX Spot ticker")
    if abs((observed_at - okx_time).total_seconds()) > 30:
        raise SpotManagementVenueUnavailable(f"{symbol}: stale OKX Spot ticker")
    deviation = abs(price - okx_price) / ((price + okx_price) / 2.0) * 100.0
    if deviation > MAX_CROSS_SOURCE_DEVIATION_PCT:
        raise SpotManagementVenueUnavailable(
            f"{symbol}: Binance/OKX Spot price deviation too high"
        )
    okx_quote_volume = okx_base_volume * okx_price / (24.0 * 60.0)
    digest = hashlib.sha256(json.dumps(
        {"binance_spot": payload, "okx_spot": okx_row}, separators=(",", ":"),
        ensure_ascii=True, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    return market_time, price, min(quote_volume_24h / (24.0 * 60.0), okx_quote_volume), digest


def capture_reference(con, *, position_id: int, symbol: str, expected: datetime,
                      purpose: str, observed_at: datetime,
                      market_type: str = "USD_M_PERPETUAL"):
    ensure_table(con)
    row = con.execute("""
        SELECT market_time,reference_price,quote_volume
        FROM paper_execution_market_observations
        WHERE position_id=? AND purpose=?
    """, (position_id, purpose)).fetchone()
    if row:
        market_time = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
        return market_time, float(row[1]), float(row[2])
    if observed_at < expected:
        return None
    if purpose == "ENTRY" and observed_at - expected > MAX_ENTRY_OBSERVATION_DELAY:
        return None
    if market_type == "SPOT":
        market_time, price, quote_volume, digest = fetch_spot_snapshot(
            symbol, observed_at
        )
    elif purpose == "ENTRY":
        market_time, price, quote_volume, digest = fetch_entry_snapshot(
            symbol, observed_at
        )
    else:
        market_time, price, quote_volume, digest = fetch_reference(
            symbol, expected, purpose
        )
    con.execute("""
        INSERT INTO paper_execution_market_observations(
            position_id,purpose,symbol,expected_time,market_time,observed_at,
            reference_price,quote_volume,source,payload_sha256,real_order_allowed
        ) VALUES(?,?,?,?,?,?,?,?,?,?,0)
        ON CONFLICT(position_id,purpose) DO NOTHING
    """, (
        position_id, purpose, symbol, _iso(expected), _iso(market_time),
        _iso(observed_at), price, quote_volume,
        ("BINANCE_SPOT_24H_TICKER_WITH_OKX_SPOT_MANAGEMENT_CHECK"
         if market_type == "SPOT" else
         ("OKX_USDT_SWAP_TICKER_WITH_BINANCE_SPOT_CROSSCHECK"
          if purpose == "ENTRY" else
          "OKX_USDT_SWAP_WITH_BINANCE_SPOT_CROSSCHECK")), digest,
    ))
    return market_time, price, quote_volume
