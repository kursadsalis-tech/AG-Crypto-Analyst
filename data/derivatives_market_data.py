"""Point-in-time USD-M derivatives data for Forecast Core research.

The module backfills a fixed development cohort from public Binance sources:

* REST: closed 1h perpetual klines and premium-index klines.
* Binance Vision: daily 5m positioning metrics and monthly funding events.
* REST: recent funding events, used only to extend the archive idempotently.

Every stored observation carries its real source time.  Consumers must use
``funding_time <= feature_cutoff`` and
``source_max_time < hour_close_time <= feature_cutoff``.  Nothing in this
module changes the live Decision Engine or emits a trading signal.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import tempfile
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

try:
    import requests
except ModuleNotFoundError:  # The offline self-test does not require requests.
    requests = None


DB_NAME = "crypto_data.db"
USER_AGENT = "AG-Crypto-Analyst/derivatives-market-data-v1"
FAPI_BASE = "https://fapi.binance.com"
VISION_BASE = "https://data.binance.vision/data/futures/um"
# Includes the 400-day experiment window, price-feature warmup and funding
# calibration lead-in without relying on the archive's leading edge.
DEFAULT_DAYS = 430
MAX_WORKERS = 8
REST_LIMIT = 1500
FUNDING_LIMIT = 1000
REQUEST_TIMEOUT = 30
ARCHIVE_REVALIDATE_DAYS = 30

SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "ARKMUSDT", "FIDAUSDT", "WLDUSDT",
    "TIAUSDT", "ETHFIUSDT", "BIOUSDT", "LQTYUSDT", "ILVUSDT",
    "SLPUSDT", "UMAUSDT", "IOUSDT",
)

METRIC_COLUMNS = (
    "create_time",
    "symbol",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "count_toptrader_long_short_ratio_position",
    "count_toptrader_long_short_ratio_long",
    "count_toptrader_long_short_ratio_short",
    "count_long_short_ratio",
    "count_long_short_ratio_long",
    "count_long_short_ratio_short",
    "taker_buy_sell_ratio",
    "taker_buy_vol",
    "taker_sell_vol",
)
FUNDING_COLUMNS = ("calc_time", "funding_rate", "symbol")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def parse_time(value) -> datetime:
    """Parse Binance millisecond/microsecond epochs or UTC text timestamps."""
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("empty timestamp")
        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
            number = float(text)
            absolute = abs(number)
            if absolute >= 1e14:
                number /= 1_000_000.0
            elif absolute >= 1e11:
                number /= 1_000.0
            result = datetime.fromtimestamp(number, tz=timezone.utc)
        else:
            normalized = text.replace("Z", "+00:00")
            try:
                result = datetime.fromisoformat(normalized)
            except ValueError:
                result = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def floor_hour(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )


def hour_close_for_source(value: datetime) -> datetime:
    """Assign a snapshot to the first Binance 1h close strictly after it."""
    value = value.astimezone(timezone.utc)
    # Binance candle close timestamps end in .999.  A 01:00:00 metric was not
    # available at the 00:59:59.999 decision and therefore belongs to the next
    # hour.  Always advancing one hour prevents a one-millisecond lookahead.
    return floor_hour(value) + timedelta(hours=1, milliseconds=-1)


def finite_float(value, field: str, *, optional: bool = False):
    if value in (None, ""):
        if optional:
            return None
        raise ValueError(f"{field} missing")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} is not finite")
    return result


def ensure_tables(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS futures_hourly_market (
            symbol TEXT NOT NULL,
            open_time TEXT NOT NULL,
            close_time TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            quote_volume REAL,
            trade_count INTEGER,
            taker_buy_quote_volume REAL,
            premium_open REAL,
            premium_high REAL,
            premium_low REAL,
            premium_close REAL,
            PRIMARY KEY(symbol, open_time)
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS ix_futures_market_symbol_close
        ON futures_hourly_market(symbol, close_time)
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS futures_positioning_hourly (
            symbol TEXT NOT NULL,
            hour_close_time TEXT NOT NULL,
            source_max_time TEXT NOT NULL,
            open_interest_contracts REAL,
            open_interest_value REAL,
            top_account_ratio REAL,
            top_position_ratio REAL,
            global_account_ratio REAL,
            taker_ratio REAL,
            source_url TEXT,
            PRIMARY KEY(symbol, hour_close_time)
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS ix_futures_positioning_symbol_source
        ON futures_positioning_hourly(symbol, source_max_time)
    """)
    positioning_columns = {
        row[1] for row in con.execute("PRAGMA table_info(futures_positioning_hourly)")
    }
    if "source_url" not in positioning_columns:
        con.execute(
            "ALTER TABLE futures_positioning_hourly ADD COLUMN source_url TEXT"
        )
    con.execute("""
        CREATE TABLE IF NOT EXISTS funding_events (
            symbol TEXT NOT NULL,
            funding_time TEXT NOT NULL,
            rate REAL NOT NULL,
            mark_price REAL,
            PRIMARY KEY(symbol, funding_time)
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS ix_funding_symbol_time
        ON funding_events(symbol, funding_time)
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS derivatives_archive_manifest (
            url TEXT PRIMARY KEY,
            checksum TEXT,
            status TEXT NOT NULL,
            rows INTEGER NOT NULL DEFAULT 0,
            fetched_at TEXT NOT NULL,
            error TEXT
        )
    """)
    con.commit()


def _request(url: str, *, params=None, stream: bool = False):
    if requests is None:
        raise RuntimeError(
            "requests package is required for Binance network collection"
        )
    last_error = None
    for attempt in range(3):
        try:
            response = requests.get(
                url,
                params=params,
                stream=stream,
                timeout=REQUEST_TIMEOUT,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json,text/csv,application/zip,*/*",
                },
            )
            if response.status_code == 404:
                return response
            if response.status_code == 429 or 500 <= response.status_code < 600:
                last_error = RuntimeError(
                    f"HTTP {response.status_code}: {response.text[:200]}"
                )
                if attempt < 2:
                    retry_after = response.headers.get("Retry-After")
                    delay = min(10.0, float(retry_after)) if retry_after else 2 ** attempt
                    time.sleep(delay)
                    continue
            response.raise_for_status()
            return response
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"request failed: {url}: {last_error}")


def _rest_json(path: str, params: dict):
    response = _request(f"{FAPI_BASE}{path}", params=params)
    if response.status_code == 404:
        return []
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected Binance response for {path}: {payload}")
    return payload


def save_market_klines(con: sqlite3.Connection, symbol: str, rows: Iterable) -> int:
    prepared = []
    for row in rows:
        if len(row) < 11:
            raise ValueError(f"{symbol}: incomplete futures kline")
        opened = parse_time(row[0])
        closed = parse_time(row[6])
        prepared.append((
            symbol, iso(opened), iso(closed),
            finite_float(row[1], "open"), finite_float(row[2], "high"),
            finite_float(row[3], "low"), finite_float(row[4], "close"),
            finite_float(row[5], "volume"), finite_float(row[7], "quote_volume"),
            int(row[8]), finite_float(row[10], "taker_buy_quote_volume"),
        ))
    if prepared:
        con.executemany("""
            INSERT INTO futures_hourly_market (
                symbol, open_time, close_time, open, high, low, close,
                volume, quote_volume, trade_count, taker_buy_quote_volume
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, open_time) DO UPDATE SET
                close_time=excluded.close_time,
                open=excluded.open, high=excluded.high, low=excluded.low,
                close=excluded.close, volume=excluded.volume,
                quote_volume=excluded.quote_volume,
                trade_count=excluded.trade_count,
                taker_buy_quote_volume=excluded.taker_buy_quote_volume
        """, prepared)
        con.commit()
    return len(prepared)


def save_premium_klines(con: sqlite3.Connection, symbol: str, rows: Iterable) -> int:
    prepared = []
    for row in rows:
        if len(row) < 7:
            raise ValueError(f"{symbol}: incomplete premium kline")
        opened = parse_time(row[0])
        closed = parse_time(row[6])
        prepared.append((
            symbol, iso(opened), iso(closed),
            finite_float(row[1], "premium_open"),
            finite_float(row[2], "premium_high"),
            finite_float(row[3], "premium_low"),
            finite_float(row[4], "premium_close"),
        ))
    if prepared:
        con.executemany("""
            INSERT INTO futures_hourly_market (
                symbol, open_time, close_time,
                premium_open, premium_high, premium_low, premium_close
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, open_time) DO UPDATE SET
                close_time=excluded.close_time,
                premium_open=excluded.premium_open,
                premium_high=excluded.premium_high,
                premium_low=excluded.premium_low,
                premium_close=excluded.premium_close
        """, prepared)
        con.commit()
    return len(prepared)


def save_funding_events(con: sqlite3.Connection, symbol: str, rows: Iterable) -> int:
    prepared = []
    for row in rows:
        if isinstance(row, dict):
            event_time = row.get("fundingTime", row.get("funding_time"))
            rate = row.get("fundingRate", row.get("funding_rate"))
            mark = row.get("markPrice", row.get("mark_price"))
        else:
            event_time, rate = row[0], row[1]
            mark = row[2] if len(row) > 2 else None
        prepared.append((
            symbol, iso(parse_time(event_time)), finite_float(rate, "funding_rate"),
            finite_float(mark, "mark_price", optional=True),
        ))
    if prepared:
        con.executemany("""
            INSERT INTO funding_events(symbol, funding_time, rate, mark_price)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol, funding_time) DO UPDATE SET
                rate=excluded.rate,
                mark_price=COALESCE(excluded.mark_price, funding_events.mark_price)
        """, prepared)
        con.commit()
    return len(prepared)


def missing_hour_ranges(
    con: sqlite3.Connection,
    symbol: str,
    start: datetime,
    end: datetime,
    value_column: str,
) -> list[tuple[datetime, datetime]]:
    allowed = {"close", "premium_close"}
    if value_column not in allowed:
        raise ValueError("unsupported market coverage column")
    rows = con.execute(f"""
        SELECT open_time FROM futures_hourly_market
        WHERE symbol=? AND open_time>=? AND open_time<?
          AND {value_column} IS NOT NULL
    """, (symbol, iso(start), iso(end))).fetchall()
    present = {floor_hour(parse_time(row[0])) for row in rows}
    missing = []
    # Once a contract's first real candle is known, a leading gap is a listing
    # boundary rather than missing data.  Do not redownload it on every run.
    cursor = max(floor_hour(start), min(present)) if present else floor_hour(start)
    end = floor_hour(end)
    while cursor < end:
        if cursor in present:
            cursor += timedelta(hours=1)
            continue
        range_start = cursor
        cursor += timedelta(hours=1)
        while cursor < end and cursor not in present:
            cursor += timedelta(hours=1)
        missing.append((range_start, cursor))
    return missing


def fetch_kline_range(
    symbol: str, path: str, start: datetime, end: datetime
) -> list:
    output = []
    cursor_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000) - 1
    while cursor_ms <= end_ms:
        payload = _rest_json(path, {
            "symbol": symbol,
            "interval": "1h",
            "startTime": cursor_ms,
            "endTime": end_ms,
            "limit": REST_LIMIT,
        })
        if not payload:
            break
        output.extend(payload)
        next_ms = int(payload[-1][0]) + 60 * 60 * 1000
        if next_ms <= cursor_ms:
            raise RuntimeError(f"{symbol}: non-advancing REST kline pagination")
        cursor_ms = next_ms
        if len(payload) < REST_LIMIT:
            break
        time.sleep(0.05)
    return output


def backfill_rest_market(
    con: sqlite3.Connection, symbols: Iterable[str], start: datetime, end: datetime
) -> dict:
    result = {"market": 0, "premium": 0, "errors": {}}
    sources = (
        ("market", "/fapi/v1/klines", "close", save_market_klines),
        ("premium", "/fapi/v1/premiumIndexKlines", "premium_close", save_premium_klines),
    )
    for position, symbol in enumerate(symbols, 1):
        for label, path, column, saver in sources:
            try:
                ranges = missing_hour_ranges(con, symbol, start, end, column)
                inserted = 0
                for range_start, range_end in ranges:
                    rows = fetch_kline_range(symbol, path, range_start, range_end)
                    inserted += saver(con, symbol, rows)
                result[label] += inserted
            except Exception as exc:
                result["errors"][f"{symbol}:{label}"] = str(exc)
        print(
            f"REST {position:>2}/{len(tuple(symbols))}: {symbol} | "
            f"market_total={result['market']} premium_total={result['premium']}"
        )
    return result


def backfill_recent_funding_rest(
    con: sqlite3.Connection, symbols: Iterable[str], start: datetime, end: datetime
) -> dict:
    inserted = 0
    errors = {}
    for symbol in symbols:
        try:
            cursor_ms = int(start.timestamp() * 1000)
            end_ms = int(end.timestamp() * 1000)
            while cursor_ms <= end_ms:
                rows = _rest_json("/fapi/v1/fundingRate", {
                    "symbol": symbol,
                    "startTime": cursor_ms,
                    "endTime": end_ms,
                    "limit": FUNDING_LIMIT,
                })
                if not rows:
                    break
                inserted += save_funding_events(con, symbol, rows)
                next_ms = int(rows[-1]["fundingTime"]) + 1
                if next_ms <= cursor_ms:
                    raise RuntimeError("non-advancing funding pagination")
                cursor_ms = next_ms
                if len(rows) < FUNDING_LIMIT:
                    break
                time.sleep(0.05)
        except Exception as exc:
            errors[symbol] = str(exc)
    return {"funding": inserted, "errors": errors}


def _manifest_status(con: sqlite3.Connection, url: str):
    row = con.execute(
        "SELECT status, fetched_at FROM derivatives_archive_manifest WHERE url=?",
        (url,),
    ).fetchone()
    if not row:
        return None
    if row[0] == "READY" and row[1]:
        fetched_at = parse_time(row[1])
        if fetched_at < utcnow() - timedelta(days=ARCHIVE_REVALIDATE_DAYS):
            return "STALE_READY"
    return row[0]


def save_manifest(con: sqlite3.Connection, result: dict) -> None:
    con.execute("""
        INSERT INTO derivatives_archive_manifest (
            url, checksum, status, rows, fetched_at, error
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            checksum=excluded.checksum,
            status=excluded.status,
            rows=excluded.rows,
            fetched_at=excluded.fetched_at,
            error=excluded.error
    """, (
        result["url"], result.get("checksum"), result["status"],
        int(result.get("rows", 0)), iso(utcnow()), result.get("error"),
    ))
    con.commit()


def _expected_checksum(text: str) -> str:
    match = re.search(r"\b([0-9a-fA-F]{64})\b", text)
    if not match:
        raise ValueError("checksum file does not contain a SHA-256 digest")
    return match.group(1).lower()


def _normalized_header(row: Iterable[str]) -> list[str]:
    return [str(value).strip().lstrip("\ufeff").lower() for value in row]


def _rows_with_columns(
    binary, fallback_columns: tuple[str, ...], *, require_header: bool = False
):
    wrapper = io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
    reader = csv.reader(wrapper)
    first = next(reader, None)
    if first is None:
        return
    normalized = _normalized_header(first)
    known = set(fallback_columns)
    has_header = bool(known.intersection(normalized))
    if require_header and not has_header:
        raise ValueError("archive CSV header is required for fail-closed parsing")
    columns = normalized if has_header else list(fallback_columns[:len(first)])
    if not has_header:
        yield dict(zip(columns, first))
    for values in reader:
        if values:
            yield dict(zip(columns, values))


def _pick(row: dict, *names):
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def parse_metrics_zip(path: Path) -> tuple[int, list[tuple]]:
    raw_rows = 0
    hourly = {}
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError("metrics ZIP must contain exactly one CSV")
        with archive.open(names[0]) as binary:
            for row in _rows_with_columns(binary, METRIC_COLUMNS):
                raw_rows += 1
                source_time = parse_time(_pick(row, "create_time", "timestamp"))
                hour_close = hour_close_for_source(source_time)
                candidate = (
                    source_time,
                    finite_float(_pick(row, "sum_open_interest"), "sum_open_interest"),
                    finite_float(_pick(row, "sum_open_interest_value"), "sum_open_interest_value"),
                    finite_float(_pick(
                        row, "count_toptrader_long_short_ratio",
                        "top_account_ratio",
                    ), "top_account_ratio", optional=True),
                    finite_float(_pick(
                        row, "count_toptrader_long_short_ratio_position",
                        "sum_toptrader_long_short_ratio", "top_position_ratio",
                    ), "top_position_ratio", optional=True),
                    finite_float(_pick(
                        row, "count_long_short_ratio", "global_account_ratio",
                    ), "global_account_ratio", optional=True),
                    # Vision metric schemas legitimately contain blank ratio
                    # cells. Taker ratio is not a V2 feature; retain NULL for
                    # audit instead of rejecting the complete daily archive.
                    finite_float(_pick(
                        row, "taker_buy_sell_ratio",
                        "sum_taker_long_short_vol_ratio", "taker_ratio",
                    ), "taker_ratio", optional=True),
                )
                current = hourly.get(hour_close)
                if current is None or candidate[0] > current[0]:
                    hourly[hour_close] = candidate
    prepared = [
        (
            iso(hour_close), iso(values[0]), values[1], values[2], values[3],
            values[4], values[5], values[6],
        )
        for hour_close, values in sorted(hourly.items())
    ]
    return raw_rows, prepared


def parse_funding_zip(path: Path) -> tuple[int, list[tuple]]:
    raw_rows = 0
    events = []
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError("funding ZIP must contain exactly one CSV")
        with archive.open(names[0]) as binary:
            for row in _rows_with_columns(
                binary, FUNDING_COLUMNS, require_header=True
            ):
                raw_rows += 1
                event_time = _pick(row, "calc_time", "funding_time", "fundingtime")
                rate = _pick(row, "funding_rate", "fundingrate", "last_funding_rate")
                events.append((
                    iso(parse_time(event_time)), finite_float(rate, "funding_rate"),
                    finite_float(_pick(row, "mark_price", "markprice"),
                                 "mark_price", optional=True),
                ))
    return raw_rows, events


def download_archive(task: dict, temp_root: Path) -> dict:
    url = task["url"]
    result = {
        "url": url,
        "symbol": task["symbol"],
        "kind": task["kind"],
        "status": "ERROR",
        "rows": 0,
    }
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", url.rsplit("/", 1)[-1])
    target = temp_root / f"{task['kind']}-{task['symbol']}-{safe_name}"
    try:
        response = _request(url, stream=True)
        if response.status_code == 404:
            result["status"] = "NOT_LISTED"
            return result
        digest = hashlib.sha256()
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    digest.update(chunk)
                    handle.write(chunk)
        checksum_response = _request(f"{url}.CHECKSUM")
        if checksum_response.status_code == 404:
            raise RuntimeError("archive exists but checksum is missing")
        expected = _expected_checksum(checksum_response.text)
        actual = digest.hexdigest()
        result["checksum"] = actual
        if actual != expected:
            raise RuntimeError(f"checksum mismatch: expected={expected} actual={actual}")
        if task["kind"] == "metrics":
            raw_rows, parsed = parse_metrics_zip(target)
            result["hourly_rows"] = parsed
        elif task["kind"] == "funding":
            raw_rows, parsed = parse_funding_zip(target)
            result["funding_rows"] = parsed
        else:
            raise ValueError(f"unsupported archive kind: {task['kind']}")
        result["rows"] = raw_rows
        result["status"] = "READY"
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result
    finally:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass


def save_positioning_rows(
    con: sqlite3.Connection, symbol: str, rows: Iterable[tuple], source_url: str
) -> int:
    prepared = []
    for row in rows:
        candidate = (symbol, *row, source_url)
        existing = con.execute("""
            SELECT source_max_time, open_interest_contracts,
                   open_interest_value, top_account_ratio,
                   top_position_ratio, global_account_ratio, taker_ratio,
                   source_url
            FROM futures_positioning_hourly
            WHERE symbol=? AND hour_close_time=?
        """, (symbol, row[0])).fetchone()
        if existing is None:
            prepared.append(candidate)
            continue
        existing_time = parse_time(existing[0])
        candidate_time = parse_time(row[1])
        if candidate_time < existing_time:
            continue
        candidate_payload = tuple(
            None if value is None else float(value) for value in row[2:]
        )
        existing_payload = tuple(
            None if value is None else float(value) for value in existing[1:7]
        )
        existing_url = str(existing[7] or "")
        if candidate_time == existing_time:
            if candidate_payload == existing_payload:
                continue
            if existing_url == source_url:
                # Same authoritative archive URL was revalidated and changed.
                con.execute("""
                    UPDATE futures_positioning_hourly SET
                        open_interest_contracts=?, open_interest_value=?,
                        top_account_ratio=?, top_position_ratio=?,
                        global_account_ratio=?, taker_ratio=?, source_url=?
                    WHERE symbol=? AND hour_close_time=?
                """, (*candidate_payload, source_url, symbol, row[0]))
                con.commit()
                continue
            # Cross-archive disagreement is a data-quality error. Persist the
            # lexicographically smaller payload so the DB is deterministic
            # regardless of download completion order, then fail closed.
            candidate_key = tuple(
                (value is not None, value if value is not None else 0.0)
                for value in candidate_payload
            )
            existing_key = tuple(
                (value is not None, value if value is not None else 0.0)
                for value in existing_payload
            )
            if candidate_key < existing_key:
                con.execute("""
                    UPDATE futures_positioning_hourly SET
                        open_interest_contracts=?, open_interest_value=?,
                        top_account_ratio=?, top_position_ratio=?,
                        global_account_ratio=?, taker_ratio=?, source_url=?
                    WHERE symbol=? AND hour_close_time=?
                """, (*candidate_payload, source_url, symbol, row[0]))
                con.commit()
            raise ValueError(
                f"{symbol}: conflicting positioning payload at {row[0]}"
            )
        prepared.append(candidate)
    if prepared:
        con.executemany("""
            INSERT INTO futures_positioning_hourly (
                symbol, hour_close_time, source_max_time,
                open_interest_contracts, open_interest_value,
                top_account_ratio, top_position_ratio,
                global_account_ratio, taker_ratio, source_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, hour_close_time) DO UPDATE SET
                source_max_time=CASE
                    WHEN excluded.source_max_time>futures_positioning_hourly.source_max_time
                    THEN excluded.source_max_time
                    ELSE futures_positioning_hourly.source_max_time END,
                open_interest_contracts=CASE
                    WHEN excluded.source_max_time>futures_positioning_hourly.source_max_time
                    THEN excluded.open_interest_contracts
                    ELSE futures_positioning_hourly.open_interest_contracts END,
                open_interest_value=CASE
                    WHEN excluded.source_max_time>futures_positioning_hourly.source_max_time
                    THEN excluded.open_interest_value
                    ELSE futures_positioning_hourly.open_interest_value END,
                top_account_ratio=CASE
                    WHEN excluded.source_max_time>futures_positioning_hourly.source_max_time
                    THEN excluded.top_account_ratio
                    ELSE futures_positioning_hourly.top_account_ratio END,
                top_position_ratio=CASE
                    WHEN excluded.source_max_time>futures_positioning_hourly.source_max_time
                    THEN excluded.top_position_ratio
                    ELSE futures_positioning_hourly.top_position_ratio END,
                global_account_ratio=CASE
                    WHEN excluded.source_max_time>futures_positioning_hourly.source_max_time
                    THEN excluded.global_account_ratio
                    ELSE futures_positioning_hourly.global_account_ratio END,
                taker_ratio=CASE
                    WHEN excluded.source_max_time>futures_positioning_hourly.source_max_time
                    THEN excluded.taker_ratio
                    ELSE futures_positioning_hourly.taker_ratio END,
                source_url=CASE
                    WHEN excluded.source_max_time>futures_positioning_hourly.source_max_time
                    THEN excluded.source_url
                    ELSE futures_positioning_hourly.source_url END
        """, prepared)
        con.commit()
    return len(prepared)


def save_archive_funding(
    con: sqlite3.Connection, symbol: str, rows: Iterable[tuple]
) -> int:
    prepared = [(symbol, *row) for row in rows]
    if prepared:
        con.executemany("""
            INSERT INTO funding_events(symbol, funding_time, rate, mark_price)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol, funding_time) DO UPDATE SET
                rate=excluded.rate,
                mark_price=COALESCE(excluded.mark_price, funding_events.mark_price)
        """, prepared)
        con.commit()
    return len(prepared)


def iter_days(start: date, end: date):
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def iter_months(start: date, end: date):
    cursor = start.replace(day=1)
    final = end.replace(day=1)
    while cursor <= final:
        yield cursor
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)


def _archive_due(status, release_date: date, today: date) -> bool:
    """Retry recent 404s until the archive's publication grace period ends."""
    if status == "READY":
        return False
    if status != "NOT_LISTED":
        return True
    return today <= release_date + timedelta(days=10)


def archive_tasks(
    con: sqlite3.Connection,
    symbols: Iterable[str],
    start: datetime,
    end: datetime,
) -> list[dict]:
    tasks = []
    archive_last_day = min(
        (utcnow() - timedelta(days=1)).date(),
        (end - timedelta(microseconds=1)).date(),
    )
    today = utcnow().date()
    for symbol in symbols:
        if start.date() <= archive_last_day:
            for day in iter_days(start.date(), archive_last_day):
                name = f"{symbol}-metrics-{day.isoformat()}.zip"
                url = f"{VISION_BASE}/daily/metrics/{symbol}/{name}"
                status = _manifest_status(con, url)
                if _archive_due(status, day + timedelta(days=1), today):
                    tasks.append({"kind": "metrics", "symbol": symbol, "url": url})
        for month in iter_months(start.date(), end.date()):
            suffix = f"{month.year:04d}-{month.month:02d}"
            name = f"{symbol}-fundingRate-{suffix}.zip"
            url = f"{VISION_BASE}/monthly/fundingRate/{symbol}/{name}"
            next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
            status = _manifest_status(con, url)
            if _archive_due(status, next_month, today):
                tasks.append({"kind": "funding", "symbol": symbol, "url": url})
    return tasks


def backfill_archives(
    con: sqlite3.Connection,
    symbols: Iterable[str],
    start: datetime,
    end: datetime,
    workers: int,
) -> dict:
    tasks = archive_tasks(con, symbols, start, end)
    counts = Counter()
    inserted_positioning = 0
    inserted_funding = 0
    print(f"Binance Vision: işlenecek arşiv={len(tasks)} | worker={workers}")
    if not tasks:
        return {
            "tasks": 0, "statuses": {}, "positioning": 0, "funding": 0,
        }
    with tempfile.TemporaryDirectory(prefix="agca-derivatives-") as temp_name:
        temp_root = Path(temp_name).resolve()
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, workers))) as pool:
            futures = {
                pool.submit(download_archive, task, temp_root): task
                for task in tasks
            }
            for completed, future in enumerate(as_completed(futures), 1):
                # Drop completed Future references immediately; each result may
                # hold a full day of parsed 5-minute rows.
                task = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "url": task["url"], "symbol": task["symbol"],
                        "kind": task["kind"], "status": "ERROR", "rows": 0,
                        "error": str(exc),
                    }
                if result["status"] == "READY":
                    try:
                        if result["kind"] == "metrics":
                            inserted_positioning += save_positioning_rows(
                                con, result["symbol"],
                                result.get("hourly_rows", ()), result["url"],
                            )
                        else:
                            inserted_funding += save_archive_funding(
                                con, result["symbol"], result.get("funding_rows", ())
                            )
                    except Exception as exc:
                        result["status"] = "ERROR"
                        result["error"] = f"archive persistence failed: {exc}"
                save_manifest(con, result)
                counts[result["status"]] += 1
                if completed % 100 == 0 or completed == len(tasks):
                    print(
                        f"Vision {completed:>5}/{len(tasks)} | "
                        f"READY={counts['READY']} NOT_LISTED={counts['NOT_LISTED']} "
                        f"ERROR={counts['ERROR']} | hourly={inserted_positioning} "
                        f"funding={inserted_funding}"
                    )
    return {
        "tasks": len(tasks), "statuses": dict(counts),
        "positioning": inserted_positioning, "funding": inserted_funding,
    }


def funding_asof(
    con: sqlite3.Connection, symbol: str, feature_cutoff: datetime
):
    """Return the last settled funding event available at the cutoff."""
    row = con.execute("""
        SELECT funding_time, rate, mark_price
        FROM funding_events
        WHERE symbol=? AND funding_time<=?
        ORDER BY funding_time DESC
        LIMIT 1
    """, (symbol, iso(feature_cutoff))).fetchone()
    if not row:
        return None
    return {"funding_time": row[0], "rate": row[1], "mark_price": row[2]}


def audit_readiness(
    con: sqlite3.Connection,
    symbols: Iterable[str] = SYMBOLS,
    *,
    minimum_days: float = 300.0,
    minimum_coins: int = 10,
    minimum_coverage: float = 0.98,
) -> dict:
    details = []
    for symbol in symbols:
        row = con.execute("""
            SELECT MIN(m.close_time), MAX(m.close_time), COUNT(*)
            FROM futures_hourly_market m
            JOIN futures_positioning_hourly p
              ON p.symbol=m.symbol AND p.hour_close_time=m.close_time
            WHERE m.symbol=?
              AND m.close IS NOT NULL
              AND m.premium_close IS NOT NULL
              AND m.quote_volume IS NOT NULL
              AND m.taker_buy_quote_volume IS NOT NULL
              AND p.open_interest_contracts IS NOT NULL
              AND p.open_interest_value IS NOT NULL
              AND p.top_account_ratio IS NOT NULL
              AND p.top_position_ratio IS NOT NULL
              AND p.global_account_ratio IS NOT NULL
              AND p.source_max_time<p.hour_close_time
        """, (symbol,)).fetchone()
        funding = con.execute("""
            SELECT MIN(funding_time), MAX(funding_time), COUNT(*)
            FROM funding_events WHERE symbol=?
        """, (symbol,)).fetchone()
        if not row or not row[0] or not row[1]:
            history_days = 0.0
            coverage = 0.0
            joined_hours = 0
        else:
            first = parse_time(row[0])
            last = parse_time(row[1])
            joined_hours = int(row[2])
            expected = max(1, int((last - first).total_seconds() // 3600) + 1)
            history_days = (last - first).total_seconds() / 86400.0
            coverage = joined_hours / expected
        funding_days = 0.0
        funding_events = int(funding[2] or 0) if funding else 0
        if funding and funding[0] and funding[1]:
            funding_days = (
                parse_time(funding[1]) - parse_time(funding[0])
            ).total_seconds() / 86400.0
        ready = bool(
            history_days >= minimum_days
            and funding_days >= minimum_days
            and coverage >= minimum_coverage
        )
        details.append({
            "symbol": symbol,
            "history_days": history_days,
            "funding_days": funding_days,
            "coverage": coverage,
            "joined_hours": joined_hours,
            "funding_events": funding_events,
            "ready": ready,
        })
    by_symbol = {row["symbol"]: row for row in details}
    ready_count = sum(row["ready"] for row in details)
    references_ready = all(
        by_symbol.get(symbol, {}).get("ready", False)
        for symbol in ("BTCUSDT", "ETHUSDT")
    )
    archive_errors = int(con.execute(
        "SELECT COUNT(*) FROM derivatives_archive_manifest WHERE status='ERROR'"
    ).fetchone()[0])
    passed = (
        references_ready and ready_count >= minimum_coins and archive_errors == 0
    )
    return {
        "status": "READY" if passed else "DATA_BLOCKED",
        "passed": passed,
        "ready_count": ready_count,
        "total_count": len(details),
        "references_ready": references_ready,
        "archive_errors": archive_errors,
        "minimum_days": minimum_days,
        "minimum_coverage": minimum_coverage,
        "minimum_coins": minimum_coins,
        "symbols": details,
    }


def render_audit(audit: dict) -> None:
    print("\nDERIVATIVES DATA READINESS")
    print("SYMBOL       MARKET_D  FUNDING_D  COVERAGE   HOURS   FUND  READY")
    for row in audit["symbols"]:
        print(
            f"{row['symbol']:<11} {row['history_days']:>8.1f} "
            f"{row['funding_days']:>10.1f} {row['coverage'] * 100:>8.2f}% "
            f"{row['joined_hours']:>7} {row['funding_events']:>6}  "
            f"{'YES' if row['ready'] else 'NO'}"
        )
    print(
        f"Readiness: {audit['status']} | ready={audit['ready_count']}/"
        f"{audit['total_count']} | BTC/ETH={'OK' if audit['references_ready'] else 'BLOCK'}"
        f" | archive_errors={audit['archive_errors']}"
    )


def _make_test_zip(path: Path, header: tuple[str, ...], rows: list[tuple]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("test.csv", buffer.getvalue())
    return hashlib.sha256(path.read_bytes()).hexdigest()


def self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="agca-derivatives-selftest-") as tmp:
        tmp_path = Path(tmp)
        metrics_zip = tmp_path / "metrics.zip"
        metric_rows = [
            ("2026-01-01 00:55:00", "BTCUSDT", 10, 1000, 1.1, 1.2,
             0.52, 0.48, 0.9, 0.47, 0.53, 1.3, 13, 10),
            ("2026-01-01 01:00:00", "BTCUSDT", 11, 1100, 1.15, 1.25,
             0.53, 0.47, 0.95, 0.49, 0.51, 1.4, 14, 10),
            ("2026-01-01 01:05:00", "BTCUSDT", 12, 1200, 1.2, 1.3,
             0.54, 0.46, 1.0, 0.50, 0.50, 1.5, 15, 10),
        ]
        checksum = _make_test_zip(metrics_zip, METRIC_COLUMNS, metric_rows)
        if not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise AssertionError("test checksum failed")
        raw, hourly = parse_metrics_zip(metrics_zip)
        if raw != 3 or len(hourly) != 2:
            raise AssertionError("metrics hourly reduction failed")
        if abs(hourly[0][3] - 1000.0) > 1e-12:
            raise AssertionError("last-known metric was not retained")

        optional_zip = tmp_path / "metrics-optional-ratios.zip"
        optional_row = list(metric_rows[0])
        optional_row[4] = ""
        optional_row[8] = ""
        optional_row[11] = ""
        _make_test_zip(optional_zip, METRIC_COLUMNS, [tuple(optional_row)])
        optional_raw, optional_hourly = parse_metrics_zip(optional_zip)
        if optional_raw != 1 or len(optional_hourly) != 1:
            raise AssertionError("optional metric ratio row was rejected")
        if not (
            optional_hourly[0][4] is None
            and optional_hourly[0][6] is None
            and optional_hourly[0][7] is None
        ):
            raise AssertionError("blank metric ratios were not retained as NULL")

        funding_zip = tmp_path / "funding.zip"
        _make_test_zip(
            funding_zip, FUNDING_COLUMNS,
            [(1767254400000, "0.0001", "BTCUSDT")],
        )
        funding_raw, funding_rows = parse_funding_zip(funding_zip)
        if funding_raw != 1 or len(funding_rows) != 1:
            raise AssertionError("funding archive parse failed")

        # Binance has used more than one funding archive schema.  A headerless
        # three-column file is ambiguous (the second column can be either the
        # interval or the rate), so it must fail closed instead of silently
        # assigning the wrong feature semantics.
        headerless_zip = tmp_path / "funding-headerless.zip"
        with zipfile.ZipFile(
            headerless_zip, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr("test.csv", "1767254400000,8,0.0001\n")
        try:
            parse_funding_zip(headerless_zip)
        except ValueError as exc:
            if "header is required" not in str(exc):
                raise
        else:
            raise AssertionError("ambiguous headerless funding archive accepted")

        duplicate_con = sqlite3.connect(":memory:")
        ensure_tables(duplicate_con)
        duplicate_close = datetime(2026, 1, 1, 0, 59, 59, 999000,
                                   tzinfo=timezone.utc)
        duplicate_row = (
            iso(duplicate_close), iso(duplicate_close - timedelta(minutes=5)),
            10.0, 1000.0, 1.1, 1.2, 0.9, 1.3,
        )
        if save_positioning_rows(
            duplicate_con, "BTCUSDT", [duplicate_row], "archive-a"
        ) != 1:
            raise AssertionError("initial positioning row was not stored")
        if save_positioning_rows(
            duplicate_con, "BTCUSDT", [duplicate_row], "archive-b"
        ) != 0:
            raise AssertionError("identical cross-archive row was not deduplicated")
        conflicting_row = (*duplicate_row[:2], 11.0, *duplicate_row[3:])
        try:
            save_positioning_rows(
                duplicate_con, "BTCUSDT", [conflicting_row], "archive-b"
            )
        except ValueError as exc:
            if "conflicting positioning payload" not in str(exc):
                raise
        else:
            raise AssertionError("conflicting cross-archive row was accepted")
        retained = duplicate_con.execute(
            "SELECT open_interest_contracts FROM futures_positioning_hourly"
        ).fetchone()[0]
        if retained != 10.0:
            raise AssertionError("cross-archive conflict was not deterministic")
        optional_close = duplicate_close + timedelta(hours=1)
        optional_positioning_row = (
            iso(optional_close), iso(optional_close - timedelta(minutes=5)),
            12.0, 1200.0, None, 1.2, None, None,
        )
        if save_positioning_rows(
            duplicate_con, "BTCUSDT", [optional_positioning_row], "archive-c"
        ) != 1:
            raise AssertionError("optional positioning row was not stored")
        stored_optional = duplicate_con.execute(
            "SELECT top_account_ratio, global_account_ratio, taker_ratio "
            "FROM futures_positioning_hourly WHERE hour_close_time=?",
            (iso(optional_close),),
        ).fetchone()
        if stored_optional != (None, None, None):
            raise AssertionError("optional positioning NULL values were mutated")
        duplicate_con.close()

        con = sqlite3.connect(":memory:")
        ensure_tables(con)
        symbols = SYMBOLS[:10]
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        market_rows = []
        position_rows = []
        for symbol in symbols:
            for offset in range(49):
                opened = start + timedelta(hours=offset)
                closed = opened + timedelta(hours=1, milliseconds=-1)
                market_rows.append((
                    symbol, iso(opened), iso(closed), 100, 101, 99, 100,
                    10, 1000, 5, 550, 0.0, 0.1, -0.1, 0.01,
                ))
                position_rows.append((
                    symbol, iso(closed), iso(closed - timedelta(minutes=5)),
                    10, 1000,
                    1.1, 1.2, 0.9, 1.3, "self-test",
                ))
            con.execute(
                "INSERT INTO funding_events VALUES (?, ?, ?, ?)",
                (symbol, iso(start), 0.0001, 100.0),
            )
            con.execute(
                "INSERT INTO funding_events VALUES (?, ?, ?, ?)",
                (symbol, iso(start + timedelta(days=2)), 0.0002, 101.0),
            )
        con.executemany("""
            INSERT INTO futures_hourly_market VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, market_rows)
        con.executemany("""
            INSERT INTO futures_positioning_hourly VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, position_rows)
        con.commit()
        before = funding_asof(con, symbols[0], start - timedelta(seconds=1))
        at_event = funding_asof(con, symbols[0], start)
        if before is not None or at_event is None or at_event["rate"] != 0.0001:
            raise AssertionError("funding as-of semantics failed")
        audit = audit_readiness(
            con, SYMBOLS, minimum_days=2.0, minimum_coins=10,
            minimum_coverage=0.98,
        )
        if not audit["passed"]:
            raise AssertionError(json.dumps(audit, ensure_ascii=False))
        con.close()
    print("Derivatives market data self-test: PASS")


def parse_symbols(value: str) -> tuple[str, ...]:
    if not value:
        return SYMBOLS
    parsed = tuple(dict.fromkeys(
        item.strip().upper() for item in value.split(",") if item.strip()
    ))
    unknown = sorted(set(parsed) - set(SYMBOLS))
    if unknown:
        raise ValueError("unsupported development symbols: " + ", ".join(unknown))
    return parsed


def run(days: int, workers: int, symbols: tuple[str, ...], db_name: str) -> dict:
    if days < 1:
        raise ValueError("days must be positive")
    if not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"workers must be between 1 and {MAX_WORKERS}")
    end = floor_hour(utcnow())
    start = end - timedelta(days=days)
    con = sqlite3.connect(db_name)
    try:
        ensure_tables(con)
        print("AG Crypto Analyst - USD-M Derivatives Market Data v1.0.0")
        print(
            f"Range: {iso(start)} -> {iso(end)} | symbols={len(symbols)} | "
            f"days={days}"
        )
        rest = backfill_rest_market(con, symbols, start, end)
        archives = backfill_archives(con, symbols, start, end, workers)
        recent_funding_start = max(start, end - timedelta(days=30))
        funding = backfill_recent_funding_rest(
            con, symbols, recent_funding_start, end
        )
        audit = audit_readiness(con, symbols)
        render_audit(audit)
        errors = {
            **rest["errors"],
            **{f"funding:{key}": value for key, value in funding["errors"].items()},
        }
        archive_errors = int(archives["statuses"].get("ERROR", 0))
        print(
            "Summary: "
            f"market_rows={rest['market']} premium_rows={rest['premium']} | "
            f"positioning_rows={archives['positioning']} | "
            f"funding_archive_rows={archives['funding']} "
            f"funding_rest_rows={funding['funding']} | "
            f"source_errors={len(errors) + archive_errors}"
        )
        if errors:
            for source, message in sorted(errors.items()):
                print(f"ERROR {source}: {message}")
        return {
            "range": {"start": iso(start), "end": iso(end)},
            "rest": rest,
            "archives": archives,
            "funding": funding,
            "readiness": audit,
        }
    finally:
        con.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Backfill point-in-time Binance USD-M derivatives data."
    )
    parser.add_argument("--days", type=int, default=int(
        os.getenv("AGCA_DERIVATIVES_DAYS", str(DEFAULT_DAYS))
    ))
    parser.add_argument("--workers", type=int, default=int(
        os.getenv("AGCA_DERIVATIVES_WORKERS", str(MAX_WORKERS))
    ))
    parser.add_argument("--symbols", default="")
    parser.add_argument("--db", default=DB_NAME)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        self_test()
        return {"self_test": "PASS"}
    symbols = parse_symbols(args.symbols)
    return run(args.days, args.workers, symbols, args.db)


if __name__ == "__main__":
    main()

