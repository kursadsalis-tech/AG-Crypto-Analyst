"""Canonical Binance USD-M technical bar ingestion and archive bootstrap."""
from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests


VISION_BASE = "https://data.binance.vision/data/futures/um"
USER_AGENT = "AG-Crypto-Analyst/technical-bars-v1"
REQUEST_TIMEOUT_SECONDS = 12
SOURCE_WS = "BINANCE_USD_M_WS_KLINE_1M"
SOURCE_ARCHIVE = "BINANCE_VISION_USD_M_DAILY_KLINE_1M"


def _finite(value, field):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} is not finite")
    return result


def normalize_closed_1m_kline(message):
    """Normalize one exact closed USD-M 1m kline WebSocket event."""
    if not isinstance(message, dict) or message.get("e") != "kline":
        return None
    if message.get("st") is not None and int(message["st"]) != 1:
        return None
    payload = message.get("k")
    if not isinstance(payload, dict):
        return None
    if payload.get("x") is not True or str(payload.get("i")) != "1m":
        return None
    symbol = str(payload.get("s") or message.get("s") or "").upper()
    if not symbol.endswith("USDT") or "_" in symbol:
        return None
    opened = int(payload.get("t") or 0)
    closed = int(payload.get("T") or 0)
    if opened <= 0 or closed - opened != 59_999:
        return None
    try:
        row = {
            "symbol": symbol,
            "open_time_ms": opened,
            "close_time_ms": closed,
            "open": _finite(payload["o"], "open"),
            "high": _finite(payload["h"], "high"),
            "low": _finite(payload["l"], "low"),
            "close": _finite(payload["c"], "close"),
            "volume": _finite(payload.get("v", 0), "volume"),
            "quote_volume": _finite(payload.get("q", 0), "quote_volume"),
            "trade_count": int(payload.get("n") or 0),
            "taker_buy_quote_volume": _finite(
                payload.get("Q", 0), "taker_buy_quote_volume"
            ),
            "source": SOURCE_WS,
        }
    except (KeyError, TypeError, ValueError):
        return None
    if (
        min(row["open"], row["high"], row["low"], row["close"]) <= 0
        or min(row["volume"], row["quote_volume"], row["taker_buy_quote_volume"]) < 0
        or row["trade_count"] < 0
    ):
        return None
    return row


def archive_url(symbol, day):
    symbol = str(symbol).upper()
    day_text = day.isoformat()
    name = f"{symbol}-1m-{day_text}.zip"
    return f"{VISION_BASE}/daily/klines/{symbol}/1m/{name}"


def _expected_checksum(text):
    match = re.search(r"\b([0-9a-fA-F]{64})\b", str(text))
    if not match:
        raise ValueError("archive checksum missing")
    return match.group(1).lower()


def _parse_archive_bytes(symbol, payload):
    rows = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError("technical kline archive must contain one CSV")
        with archive.open(names[0]) as binary:
            wrapper = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
            reader = csv.reader(wrapper)
            first = next(reader, None)
            if first is None:
                return []
            pending = [first]
            if not str(first[0]).strip().isdigit():
                pending = []
            pending.extend(reader)
            for raw in pending:
                if len(raw) < 11:
                    continue
                try:
                    opened = int(raw[0])
                    closed = int(raw[6])
                    if opened >= 100_000_000_000_000:
                        opened //= 1000
                    if closed >= 100_000_000_000_000:
                        closed //= 1000
                    bar = {
                        "symbol": str(symbol).upper(),
                        "open_time_ms": opened,
                        "close_time_ms": closed,
                        "open": _finite(raw[1], "open"),
                        "high": _finite(raw[2], "high"),
                        "low": _finite(raw[3], "low"),
                        "close": _finite(raw[4], "close"),
                        "volume": _finite(raw[5], "volume"),
                        "quote_volume": _finite(raw[7], "quote_volume"),
                        "trade_count": int(raw[8]),
                        "taker_buy_quote_volume": _finite(
                            raw[10], "taker_buy_quote_volume"
                        ),
                        "source": SOURCE_ARCHIVE,
                    }
                except (TypeError, ValueError):
                    continue
                if closed - opened == 59_999:
                    rows.append(bar)
    rows.sort(key=lambda row: row["open_time_ms"])
    return rows


def fetch_daily_archive(symbol, day, session=requests):
    """Fetch one checksum-verified Binance Vision USD-M 1m archive."""
    url = archive_url(symbol, day)
    response = session.get(
        url, timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT, "Accept": "application/zip"},
    )
    if response.status_code == 404:
        return []
    response.raise_for_status()
    checksum = session.get(
        f"{url}.CHECKSUM", timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT, "Accept": "text/plain"},
    )
    checksum.raise_for_status()
    actual = hashlib.sha256(response.content).hexdigest()
    expected = _expected_checksum(checksum.text)
    if actual != expected:
        raise ValueError("technical archive checksum mismatch")
    return _parse_archive_bytes(symbol, response.content)


def bootstrap_day(now=None):
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).date() - timedelta(days=1)



def bootstrap_missing(database_path, symbols, *, now=None, max_workers=4):
    """Backfill one exact prior-day USD-M 1m archive for underfilled symbols.

    This runs only from the off-hot-path support cycle. A failed or not-yet-
    published archive never changes trading authority; that symbol simply
    remains without an authoritative technical plan.
    """
    from database.runtime_store import connect
    from database.technical_bar_store import (
        ensure_table, persist_bars, symbols_needing_bootstrap,
    )

    now = now or datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    with connect(database_path, timeout=30) as con:
        con.execute("PRAGMA busy_timeout=30000")
        ensure_table(con)
        needed = symbols_needing_bootstrap(
            con, symbols, now_ms=now_ms, minimum_bars=360, lookback_hours=36
        )
    if not needed:
        return {"needed": 0, "ready": 0, "bars": 0, "errors": {}}

    day = bootstrap_day(now)
    fetched = {}
    errors = {}
    worker_count = max(1, min(int(max_workers), len(needed)))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {
            pool.submit(fetch_daily_archive, item["symbol"], day): item["symbol"]
            for item in needed
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                bars = future.result()
            except Exception as exc:
                errors[symbol] = type(exc).__name__
                continue
            if not bars:
                errors[symbol] = "ARCHIVE_NOT_AVAILABLE"
                continue
            fetched[symbol] = bars

    inserted = 0
    if fetched:
        prepared = []
        for symbol in sorted(fetched):
            prepared.extend(fetched[symbol])
        persisted = persist_bars(
            database_path, prepared, now_ms=now_ms, retention_days=3
        )
        inserted = int(persisted["upserted"])
    return {
        "needed": len(needed),
        "ready": len(fetched),
        "bars": inserted,
        "errors": errors,
        "archive_day": day.isoformat(),
    }
