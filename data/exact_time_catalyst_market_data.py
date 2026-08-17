"""Collect targeted Binance spot candles for exact-time catalyst events.

This module is a data-readiness stage, not a forecast or backtest.  It reads
only exact-time strict listing/delisting events, downloads checksum-verified
Binance Vision 1h spot archives around each event, and measures whether a
causal next-hour entry plus 1h/4h/24h exits exists.  It never computes returns.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import os
import re
import sqlite3
from database.runtime_store import connect as runtime_connect
import tempfile
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from data import derivatives_market_data as shared


VERSION = "EXACT_TIME_CATALYST_MARKET_DATA_V1_1"
DB_NAME = os.getenv("AGCA_DB", "crypto_data.db")
EVENT_PATH = Path("reports/exact_time_catalyst_feasibility.csv")
VISION_BASE = "https://data.binance.vision/data/spot/daily/klines"
LOOKBACK_DAYS = 1095
PRE_EVENT_DAYS = 2
POST_EVENT_DAYS = 4
MAX_WORKERS = 4
HORIZONS = (1, 4, 24)
UPPER_TICKER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{1,14})(?![A-Za-z0-9])"
)
TICKER_STOPWORDS = {
    "AI", "API", "APR", "BNB", "BTC", "BUSD", "CEO", "DEX", "ETF", "ETH",
    "EU", "EUR", "FDUSD", "GBP", "KYC", "NFT", "OTC", "P2P", "TRY",
    "UAE", "UK", "US", "USD", "USDC", "USDT", "UTC", "VIP",
}


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def parse_time(value):
    return shared.parse_time(value)


def ensure_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS catalyst_spot_hourly (
            symbol TEXT NOT NULL,
            open_time TEXT NOT NULL,
            close_time TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            quote_volume REAL NOT NULL,
            trade_count INTEGER NOT NULL,
            source_url TEXT NOT NULL,
            PRIMARY KEY(symbol, open_time)
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS ix_catalyst_spot_symbol_open
        ON catalyst_spot_hourly(symbol, open_time)
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS catalyst_spot_archive_manifest (
            url TEXT PRIMARY KEY,
            checksum TEXT,
            status TEXT NOT NULL,
            rows INTEGER NOT NULL DEFAULT 0,
            fetched_at TEXT NOT NULL,
            error TEXT
        )
    """)
    con.commit()


def load_events(path=None):
    path = Path(path) if path is not None else EVENT_PATH
    if not path.exists():
        raise RuntimeError(
            f"{path} bulunamadı; exact-time source feasibility önce çalışmalı"
        )
    events = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("event_type") not in {"STRICT_LISTING", "STRICT_DELISTING"}:
                continue
            event_time = parse_time(row.get("available_at"))
            tickers = {
                value.strip().upper() for value in row.get("tickers", "").split(",")
                if value.strip()
            }
            tickers.update(UPPER_TICKER_PATTERN.findall(row.get("title", "")))
            tickers = tuple(sorted(tickers - TICKER_STOPWORDS))
            events.append({
                "article_code": row["article_code"],
                "event_type": row["event_type"],
                "event_time": event_time,
                "tickers": tickers,
                "title": row.get("title", ""),
            })
    if not events:
        raise RuntimeError("kesin zamanlı strict listing/delisting olayı bulunamadı")
    latest = max(row["event_time"] for row in events)
    cutoff = latest - timedelta(days=LOOKBACK_DAYS)
    return [row for row in events if row["event_time"] >= cutoff], cutoff, latest


def event_coin_rows(events):
    output = []
    for event in events:
        for ticker in event["tickers"]:
            if not ticker.isalnum() or not 2 <= len(ticker) <= 15:
                continue
            output.append({**event, "ticker": ticker, "symbol": ticker + "USDT"})
    return output


def iter_dates(start, end):
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def archive_url(symbol, day):
    suffix = day.isoformat()
    name = f"{symbol}-1h-{suffix}.zip"
    return f"{VISION_BASE}/{symbol}/1h/{name}"


def manifest_status(con, url):
    row = con.execute(
        "SELECT status FROM catalyst_spot_archive_manifest WHERE url=?", (url,)
    ).fetchone()
    return row[0] if row else None


def build_tasks(con, coin_events):
    tasks = {}
    last_archive_day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    for row in coin_events:
        start = row["event_time"].date() - timedelta(days=PRE_EVENT_DAYS)
        end = min(
            row["event_time"].date() + timedelta(days=POST_EVENT_DAYS),
            last_archive_day,
        )
        for day in iter_dates(start, end):
            url = archive_url(row["symbol"], day)
            if manifest_status(con, url) in {"READY", "NOT_LISTED"}:
                continue
            tasks[url] = {"url": url, "symbol": row["symbol"]}
    return list(tasks.values())


def parse_spot_zip(path):
    rows = []
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError("spot kline ZIP must contain exactly one CSV")
        with archive.open(names[0]) as binary:
            wrapper = shared.io.TextIOWrapper(binary, encoding="utf-8-sig", newline="")
            reader = csv.reader(wrapper)
            for values in reader:
                if not values:
                    continue
                if str(values[0]).strip().lower() in {"open_time", "opentime"}:
                    continue
                if len(values) < 11:
                    raise ValueError("incomplete spot kline row")
                opened = parse_time(values[0])
                closed = parse_time(values[6])
                rows.append((
                    iso(opened), iso(closed),
                    shared.finite_float(values[1], "open"),
                    shared.finite_float(values[2], "high"),
                    shared.finite_float(values[3], "low"),
                    shared.finite_float(values[4], "close"),
                    shared.finite_float(values[5], "volume"),
                    shared.finite_float(values[7], "quote_volume"),
                    int(values[8]),
                ))
    return rows


def download_task(task, temp_root):
    result = {**task, "status": "ERROR", "rows": 0}
    target = temp_root / (task["symbol"] + "-" + task["url"].rsplit("/", 1)[-1])
    try:
        response = shared._request(task["url"], stream=True)
        if response.status_code == 404:
            result["status"] = "NOT_LISTED"
            return result
        digest = shared.hashlib.sha256()
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if chunk:
                    digest.update(chunk)
                    handle.write(chunk)
        checksum_response = shared._request(task["url"] + ".CHECKSUM")
        if checksum_response.status_code == 404:
            raise RuntimeError("archive exists but checksum is missing")
        expected = shared._expected_checksum(checksum_response.text)
        actual = digest.hexdigest()
        if actual != expected:
            raise RuntimeError("archive checksum mismatch")
        result["checksum"] = actual
        result["parsed"] = parse_spot_zip(target)
        result["rows"] = len(result["parsed"])
        result["status"] = "READY"
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        target.unlink(missing_ok=True)
    return result


def save_result(con, result):
    if result["status"] == "READY":
        con.executemany("""
            INSERT INTO catalyst_spot_hourly (
                symbol, open_time, close_time, open, high, low, close,
                volume, quote_volume, trade_count, source_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, open_time) DO UPDATE SET
                close_time=excluded.close_time, open=excluded.open,
                high=excluded.high, low=excluded.low, close=excluded.close,
                volume=excluded.volume, quote_volume=excluded.quote_volume,
                trade_count=excluded.trade_count, source_url=excluded.source_url
        """, [(result["symbol"], *row, result["url"])
               for row in result.get("parsed", ())])
    con.execute("""
        INSERT INTO catalyst_spot_archive_manifest (
            url, checksum, status, rows, fetched_at, error
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            checksum=excluded.checksum, status=excluded.status,
            rows=excluded.rows, fetched_at=excluded.fetched_at,
            error=excluded.error
    """, (
        result["url"], result.get("checksum"), result["status"],
        result.get("rows", 0), iso(datetime.now(timezone.utc)), result.get("error"),
    ))
    con.commit()


def collect(con, tasks, workers):
    counts = Counter()
    inserted = 0
    print(f"Binance Vision targeted archives: {len(tasks)} | workers={workers}")
    if not tasks:
        return counts, inserted
    with tempfile.TemporaryDirectory(prefix="agca-catalyst-spot-") as name:
        root = Path(name).resolve()
        with ThreadPoolExecutor(max_workers=max(1, min(MAX_WORKERS, workers))) as pool:
            futures = {pool.submit(download_task, task, root): task for task in tasks}
            for index, future in enumerate(as_completed(futures), 1):
                task = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = {**task, "status": "ERROR", "rows": 0, "error": str(exc)}
                save_result(con, result)
                counts[result["status"]] += 1
                inserted += result.get("rows", 0) if result["status"] == "READY" else 0
                if index % 100 == 0 or index == len(tasks):
                    print(
                        f"Archive {index:>4}/{len(tasks)} | READY={counts['READY']} "
                        f"NOT_LISTED={counts['NOT_LISTED']} ERROR={counts['ERROR']} | "
                        f"hourly={inserted}"
                    )
    return counts, inserted


def load_symbol_times(con, symbols):
    output = {}
    for symbol in symbols:
        rows = con.execute("""
            SELECT open_time FROM catalyst_spot_hourly
            WHERE symbol=? ORDER BY open_time
        """, (symbol,)).fetchall()
        output[symbol] = [parse_time(row[0]) for row in rows]
    return output


def readiness(con, events, coin_events):
    symbol_times = load_symbol_times(con, sorted({row["symbol"] for row in coin_events}))
    matched = []
    for row in coin_events:
        times = symbol_times.get(row["symbol"], ())
        index = bisect.bisect_left(times, row["event_time"])
        if index >= len(times):
            continue
        entry = times[index]
        delay_hours = (entry - row["event_time"]).total_seconds() / 3600.0
        if not 0.0 <= delay_hours <= 48.0:
            continue
        available = []
        for horizon in HORIZONS:
            target = entry + timedelta(hours=horizon - 1)
            position = index + horizon - 1
            if position < len(times) and abs((times[position] - target).total_seconds()) <= 1:
                available.append(horizon)
        matched.append({**row, "entry_delay_hours": delay_hours, "horizons": tuple(available)})

    strict_with_ticker = sum(bool(row["tickers"]) for row in events)
    full = [row for row in matched if 24 in row["horizons"]]
    event_codes = {row["article_code"] for row in full}
    coins = {row["ticker"] for row in full}
    types = Counter(row["event_type"] for row in full)
    denominator = len(coin_events)
    result = {
        "strict_events": len(events),
        "strict_events_with_ticker": strict_with_ticker,
        "coin_events": denominator,
        "matched_coin_events_24h": len(full),
        "matched_unique_events_24h": len(event_codes),
        "matched_coins_24h": len(coins),
        "match_ratio_24h": len(full) / denominator if denominator else 0.0,
        "matched_types_24h": dict(types),
        "median_entry_delay_hours": (
            sorted(row["entry_delay_hours"] for row in full)[len(full) // 2]
            if full else None
        ),
    }
    gates = {
        "strict_events_with_ticker_gte_75": strict_with_ticker >= 75,
        "matched_coin_events_24h_gte_100": len(full) >= 100,
        "matched_unique_events_24h_gte_75": len(event_codes) >= 75,
        "matched_coins_24h_gte_20": len(coins) >= 20,
        "match_ratio_24h_gte_50pct": result["match_ratio_24h"] >= 0.50,
        "listing_and_delisting_matched_gte_20_each": (
            types["STRICT_LISTING"] >= 20 and types["STRICT_DELISTING"] >= 20
        ),
    }
    result["gates"] = gates
    result["status"] = "PRICE_DATA_FEASIBLE" if all(gates.values()) else "PRICE_DATA_BLOCKED"
    return result


def render(result, cutoff, latest, tasks, statuses, inserted):
    print("AG Crypto Analyst - Exact-Time Catalyst Market Data v1.1.0")
    print(f"Window: {iso(cutoff)} -> {iso(latest)} | status={result['status']}")
    print(
        f"Strict events={result['strict_events']} | with_ticker={result['strict_events_with_ticker']} | "
        f"coin_events={result['coin_events']}"
    )
    print(
        f"24h matched: coin_events={result['matched_coin_events_24h']} | "
        f"unique_events={result['matched_unique_events_24h']} | "
        f"coins={result['matched_coins_24h']} | "
        f"ratio={100.0 * result['match_ratio_24h']:.1f}% | "
        f"types={result['matched_types_24h']}"
    )
    print(
        f"Archive run: tasks={tasks} | READY={statuses['READY']} | "
        f"NOT_LISTED={statuses['NOT_LISTED']} | ERROR={statuses['ERROR']} | rows={inserted}"
    )
    failed = [name for name, passed in result["gates"].items() if not passed]
    if failed:
        print("failed: " + ",".join(failed))
    print("No return, price target or model/backtest result was computed.")


def self_test():
    with tempfile.TemporaryDirectory() as name:
        event_path = Path(name) / "events.csv"
        with event_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "article_code", "event_type", "available_at", "tickers", "title"
            ])
            writer.writeheader()
            writer.writerow({
                "article_code": "a" * 32,
                "event_type": "STRICT_DELISTING",
                "available_at": "2025-01-01T00:30:00+00:00",
                "tickers": "",
                "title": "Binance Will Delist ABC",
            })
        events, _, _ = load_events(event_path)
        assert events[0]["tickers"] == ("ABC",)

        path = Path(name) / "sample.zip"
        csv_path = Path(name) / "SAMPLE.csv"
        csv_path.write_text(
            "1735689600000,1,2,0.5,1.5,10,1735693199999,15,3,5,7.5,0\n",
            encoding="utf-8",
        )
        with zipfile.ZipFile(path, "w") as archive:
            archive.write(csv_path, arcname="SAMPLE.csv")
        rows = parse_spot_zip(path)
        assert len(rows) == 1 and rows[0][2] == 1.0 and rows[0][8] == 3

        con = runtime_connect(":memory:")
        ensure_tables(con)
        opened = datetime(2025, 1, 1, 1, tzinfo=timezone.utc)
        for offset in range(24):
            current = opened + timedelta(hours=offset)
            con.execute("""
                INSERT INTO catalyst_spot_hourly VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "ABCUSDT", iso(current),
                iso(current + timedelta(hours=1, milliseconds=-1)),
                1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1, "TEST",
            ))
        con.commit()
        coin_rows = event_coin_rows(events)
        result = readiness(con, events, coin_rows)
        assert result["matched_coin_events_24h"] == 1
        con.close()
    print("Exact-time catalyst market data self-test: PASS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    events, cutoff, latest = load_events()
    coin_events = event_coin_rows(events)
    con = runtime_connect(DB_NAME)
    try:
        ensure_tables(con)
        tasks = build_tasks(con, coin_events)
        statuses, inserted = collect(con, tasks, args.workers)
        result = readiness(con, events, coin_events)
        render(result, cutoff, latest, len(tasks), statuses, inserted)
    finally:
        con.close()


if __name__ == "__main__":
    main()
