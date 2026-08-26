"""AG Crypto Analyst v0.8.0 - first working macro data collector.

Downloads recent official FRED CSV observations without requiring an API key.
This module stores observations only and intentionally makes no regime/trade call.
"""
from __future__ import annotations

import csv
import io
import sqlite3
from database.runtime_store import connect as runtime_connect
from datetime import datetime, timedelta, timezone

import requests
from data.truth_engine import validate_macro_series


DB_NAME = "crypto_data.db"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
USER_AGENT = "AG-Crypto-Analyst/0.8.0"
LOOKBACK_DAYS = 800
SERIES = {
    "DFF": {
        "name": "Effective Federal Funds Rate",
        "unit": "percent",
        "frequency": "daily",
    },
    "DGS10": {
        "name": "US 10-Year Treasury Yield",
        "unit": "percent",
        "frequency": "daily",
    },
    "DTWEXBGS": {
        "name": "Nominal Broad US Dollar Index",
        "unit": "index",
        "frequency": "daily",
    },
    "VIXCLS": {
        "name": "CBOE Volatility Index",
        "unit": "index",
        "frequency": "daily",
    },
    "CPIAUCSL": {
        "name": "US Consumer Price Index",
        "unit": "index",
        "frequency": "monthly",
    },
}


def utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


def fetch_series(series_id, start_date):
    response = requests.get(
        FRED_CSV_URL,
        params={"id": series_id, "cosd": start_date},
        timeout=30,
        headers={"User-Agent": USER_AGENT, "Accept": "text/csv"},
    )
    response.raise_for_status()
    reader = csv.DictReader(io.StringIO(response.text))
    if not reader.fieldnames or series_id not in reader.fieldnames:
        raise ValueError(f"beklenmeyen FRED CSV kolonları: {reader.fieldnames}")
    rows = []
    for row in reader:
        raw_value = row.get(series_id)
        if raw_value in (None, "", "."):
            continue
        date_value = row.get("observation_date") or row.get("DATE")
        if not date_value:
            continue
        rows.append((date_value, float(raw_value)))
    if not rows:
        raise ValueError("geçerli gözlem bulunamadı")
    return rows


def ensure_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS macro_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id TEXT NOT NULL,
            series_name TEXT NOT NULL,
            observation_date TEXT NOT NULL,
            value REAL NOT NULL,
            unit TEXT NOT NULL,
            frequency TEXT NOT NULL,
            source TEXT NOT NULL,
            ingested_at TEXT NOT NULL,
            UNIQUE(series_id, observation_date)
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS ix_macro_series_date
        ON macro_observations(series_id, observation_date)
    """)
    con.commit()


def save_series(con, series_id, observations, ingested_at):
    meta = SERIES[series_id]
    before = con.total_changes
    con.executemany("""
        INSERT OR IGNORE INTO macro_observations (
            series_id, series_name, observation_date, value, unit,
            frequency, source, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [(
        series_id,
        meta["name"],
        date_value,
        value,
        meta["unit"],
        meta["frequency"],
        "FRED",
        ingested_at,
    ) for date_value, value in observations])
    con.commit()
    return con.total_changes - before


def main():
    start_date = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).date().isoformat()
    ingested_at = utcnow_iso()
    fetched = {}
    errors = []

    for series_id, meta in SERIES.items():
        try:
            rows = fetch_series(series_id, start_date)
            validate_macro_series(rows, source=f"FRED:{series_id}")
            fetched[series_id] = rows
            latest_date, latest_value = rows[-1]
            print(
                f"{series_id}: {len(rows)} gözlem | "
                f"son={latest_date} {latest_value:g} {meta['unit']}"
            )
        except Exception as exc:
            errors.append(f"{series_id}: {exc}")
            print(f"{series_id}: HATA -> {exc}")

    con = runtime_connect(DB_NAME)
    try:
        ensure_table(con)
        inserted = 0
        for series_id, rows in fetched.items():
            inserted += save_series(con, series_id, rows, ingested_at)
    finally:
        con.close()

    total = sum(len(rows) for rows in fetched.values())
    print("\nAG Crypto Analyst - Macro Collector v0.8.0")
    print(f"Başlangıç tarihi: {start_date}")
    print(f"Alınan gözlem: {total} | Yeni kaydedilen: {inserted} | Seri hatası: {len(errors)}")
    return {"observations": total, "inserted": inserted, "errors": errors}


if __name__ == "__main__":
    main()
