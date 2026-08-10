"""Historical GDELT GKG backfill for AG Crypto Analyst.

Downloads daily GKG archive files instead of using the rate-limited DOC API.
The parser keeps only configured crypto-news sources and writes through the
existing news repository when available.
"""
from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import time
import zipfile
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

BASE_URL = "https://data.gdeltproject.org/gkg/"
DEFAULT_DAYS = int(os.getenv("GDELT_BACKFILL_DAYS", "7"))
SOURCES = {"coindesk.com", "cointelegraph.com", "theblock.co"}
CRYPTO_TERMS = {
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "crypto",
    "cryptocurrency", "token", "defi", "stablecoin", "blockchain", "binance",
    "coinbase", "altcoin", "web3", "etf", "btc", "eth"
}
DB_PATH = os.getenv("AGCA_DB", "crypto_data.db")


def gkg_url(day: datetime) -> str:
    stamp = day.strftime("%Y%m%d")
    return f"{BASE_URL}{stamp}.gkg.csv.zip"


def fetch(url: str, attempts: int = 4) -> bytes:
    last = None
    for attempt in range(1, attempts + 1):
        try:
            req = Request(url, headers={"User-Agent": "AG-Crypto-Analyst/1.0"})
            with urlopen(req, timeout=60) as response:
                return response.read()
        except Exception as exc:
            last = exc
            if attempt < attempts:
                time.sleep(min(30, 2 ** attempt))
    raise last


def clean_source(value: str) -> str:
    return value.lower().strip().split("/")[0]


def title_from_row(row: list[str]) -> str:
    # GKG 2.1: Extras often contains PAGE_TITLE=...
    extras = row[55] if len(row) > 55 else ""
    match = re.search(r"PAGE_TITLE=([^;]+)", extras)
    if match:
        return match.group(1).strip()
    # Fallback: first quoted-looking text in themes/entities is intentionally avoided.
    return ""


def is_crypto(title: str, row: list[str]) -> bool:
    text = title.lower() + " " + " ".join(row[8:35]).lower()
    return any(term in text for term in CRYPTO_TERMS)


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS historical_news_raw (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        published_at TEXT NOT NULL,
        source TEXT NOT NULL,
        title TEXT NOT NULL,
        url TEXT NOT NULL UNIQUE,
        source_file TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    conn.commit()


def insert_row(conn: sqlite3.Connection, published_at: str, source: str, title: str, url: str, source_file: str) -> bool:
    if not title or not url:
        return False
    cur = conn.execute(
        "INSERT OR IGNORE INTO historical_news_raw(published_at,source,title,url,source_file,created_at) VALUES(?,?,?,?,?,?)",
        (published_at, source, title, url, source_file, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return cur.rowcount > 0


def process_day(day: datetime, conn: sqlite3.Connection) -> tuple[int, int]:
    url = gkg_url(day)
    payload = fetch(url)
    added = 0
    matched = 0
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
            reader = csv.reader(text, delimiter="\t")
            for row in reader:
                if len(row) < 7:
                    continue
                source = clean_source(row[4])
                if source not in SOURCES:
                    continue
                title = title_from_row(row)
                if not is_crypto(title, row):
                    continue
                matched += 1
                url_value = row[40].strip() if len(row) > 40 else ""
                published = row[1].strip()
                if insert_row(conn, published, source, title, url_value, name):
                    added += 1
    return matched, added


def main() -> None:
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=DEFAULT_DAYS)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    print("AG Crypto Analyst - GDELT GKG Historical Backfill")
    print(f"Pencere: {start.isoformat()} -> {end.isoformat()}")
    print("Kaynak: GDELT günlük GKG arşivi (DOC API kullanılmıyor)")
    day = start.replace(hour=0)
    total = 0
    while day.date() <= end.date():
        try:
            matched, added = process_day(day, conn)
            total += added
            print(f"{day.date()} -> eşleşen={matched}, yeni={added}")
        except Exception as exc:
            print(f"{day.date()} -> HATA: {exc}")
        day += timedelta(days=1)
    conn.close()
    print(f"Toplam yeni tarihsel haber: {total}")


if __name__ == "__main__":
    main()
