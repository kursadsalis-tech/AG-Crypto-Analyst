"""Causal public market observations for dynamic PAPER execution.

The behavior universe is dynamic, so PAPER execution must not depend on the
fixed research/forecast candle cohort.  Each position captures its own public
Binance USD-M reference once; later runs may read but never rewrite it.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import requests


OKX_CANDLES_URL = "https://www.okx.com/api/v5/market/candles"
BINANCE_SPOT_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
USER_AGENT = "AG-Crypto-Analyst/paper-execution-observation-v1"
REQUEST_TIMEOUT_SECONDS = 10
MAX_ENTRY_OBSERVATION_DELAY = timedelta(minutes=10)
MAX_CROSS_SOURCE_DEVIATION_PCT = 2.0


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


def capture_reference(con, *, position_id: int, symbol: str, expected: datetime,
                      purpose: str, observed_at: datetime):
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
        "OKX_USDT_SWAP_WITH_BINANCE_SPOT_CROSSCHECK", digest,
    ))
    return market_time, price, quote_volume
