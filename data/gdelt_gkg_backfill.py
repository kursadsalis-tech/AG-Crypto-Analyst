"""Historical GDELT GKG backfill for AG Crypto Analyst.

This version avoids the DOC API and the currently broken TLS hostname on
`data.gdeltproject.org` by downloading the public read-only GKG archive over
HTTP. It scans backwards for the latest *available* daily files, so missing
recent archive days do not fail the whole run. Parsing is schema-tolerant and
extracts source/date/url/title heuristically across GKG variants.
"""
from __future__ import annotations

import csv
import html
import io
import os
import re
import sqlite3
import time
import zipfile
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

BASE_URL = "http://data.gdeltproject.org/gkg/"
TARGET_DAYS = max(1, int(os.getenv("GDELT_BACKFILL_DAYS", "7")))
# Scan beyond the requested successful-day count so occasional missing archive
# days do not prevent a 90+ day backfill from completing. The env override can
# always raise this further for unusually sparse periods.
DEFAULT_LOOKBACK_DAYS = TARGET_DAYS + max(30, TARGET_DAYS // 3)
MAX_LOOKBACK_DAYS = max(
    TARGET_DAYS,
    int(os.getenv("GDELT_MAX_LOOKBACK_DAYS", str(DEFAULT_LOOKBACK_DAYS))),
)
SOURCES = {"coindesk.com", "cointelegraph.com", "theblock.co"}
CRYPTO_TERMS = {
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "ripple",
    "crypto", "cryptocurrency", "token", "defi", "stablecoin", "blockchain",
    "binance", "coinbase", "altcoin", "web3", "etf", "cardano", "ada",
    "dogecoin", "doge", "tron", "trx", "chainlink", "link", "avalanche",
    "avax", "sui", "hyperliquid"
}
DB_PATH = os.getenv("AGCA_DB", "crypto_data.db")


def gkg_url(day: datetime) -> str:
    return f"{BASE_URL}{day.strftime('%Y%m%d')}.gkg.csv.zip"


def fetch(url: str, attempts: int = 3) -> bytes:
    last = None
    for attempt in range(1, attempts + 1):
        try:
            req = Request(url, headers={"User-Agent": "AG-Crypto-Analyst/1.0"})
            with urlopen(req, timeout=60) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code == 404:
                raise
            last = exc
        except Exception as exc:
            last = exc
        if attempt < attempts:
            time.sleep(min(10, 2 ** attempt))
    raise last


def normalize_domain(value: str) -> str:
    value = (value or "").strip().lower()
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        value = urlparse(value).netloc.lower()
    value = value.split(":")[0]
    if value.startswith("www."):
        value = value[4:]
    return value


def find_source(row: list[str]) -> str:
    for field in row:
        domain = normalize_domain(field)
        if domain in SOURCES:
            return domain
    return ""


def find_url(row: list[str], source: str) -> str:
    for field in row:
        field = (field or "").strip()
        if not field.startswith(("http://", "https://")):
            continue
        domain = normalize_domain(field)
        if domain == source:
            return field
    return ""


def find_date(row: list[str], fallback_day: datetime) -> str:
    for field in row[:8]:
        value = (field or "").strip()
        if re.fullmatch(r"\d{14}", value):
            try:
                dt = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                return dt.isoformat()
            except ValueError:
                pass
    return fallback_day.replace(hour=12, minute=0, second=0, microsecond=0).isoformat()


def title_from_extras(row: list[str]) -> str:
    joined = "\t".join(row)
    for pattern in (
        r"<PAGE_TITLE>(.*?)</PAGE_TITLE>",
        r"PAGE_TITLE=([^;\t]+)",
    ):
        match = re.search(pattern, joined, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return html.unescape(match.group(1).strip())
    return ""


def title_from_url(url: str) -> str:
    if not url:
        return ""
    path = unquote(urlparse(url).path).strip("/")
    if not path:
        return ""
    slug = path.split("/")[-1]
    slug = re.sub(r"\.(html?|amp)$", "", slug, flags=re.IGNORECASE)
    slug = re.sub(r"[-_]+", " ", slug)
    slug = re.sub(r"\s+", " ", slug).strip()
    if len(slug) < 12:
        return ""
    return slug[:500]


def is_crypto(title: str, row: list[str]) -> bool:
    text = (title + " " + " ".join(row)).lower()
    return any(re.search(rf"\b{re.escape(term)}\b", text) for term in CRYPTO_TERMS)


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


def insert_row(conn, published_at, source, title, url, source_file) -> bool:
    if not title or not url:
        return False
    cur = conn.execute(
        "INSERT OR IGNORE INTO historical_news_raw(published_at,source,title,url,source_file,created_at) VALUES(?,?,?,?,?,?)",
        (published_at, source, title, url, source_file, datetime.now(timezone.utc).isoformat()),
    )
    return cur.rowcount > 0


def process_payload(day: datetime, payload: bytes, conn: sqlite3.Connection) -> tuple[int, int]:
    matched = 0
    added = 0
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as raw:
            reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", errors="replace"), delimiter="\t")
            for row in reader:
                if len(row) < 4:
                    continue
                source = find_source(row)
                if not source:
                    continue
                url = find_url(row, source)
                if not url:
                    continue
                title = title_from_extras(row) or title_from_url(url)
                if not title or not is_crypto(title, row):
                    continue
                matched += 1
                published = find_date(row, day)
                if insert_row(conn, published, source, title, url, name):
                    added += 1
    conn.commit()
    return matched, added


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    print("AG Crypto Analyst - GDELT GKG Historical Backfill")
    print(f"Hedef başarılı arşiv günü: {TARGET_DAYS}")
    print(f"Maksimum geriye tarama: {MAX_LOOKBACK_DAYS} gün")
    print("Kaynak: GDELT günlük GKG arşivi (DOC API yok, HTTP read-only archive)")
    print()

    successful_days = 0
    scanned_days = 0
    total_added = 0
    day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    while successful_days < TARGET_DAYS and scanned_days < MAX_LOOKBACK_DAYS:
        scanned_days += 1
        url = gkg_url(day)
        try:
            payload = fetch(url)
        except HTTPError as exc:
            if exc.code == 404:
                print(f"{day.date()} -> arşiv dosyası yok, geriye geçiliyor")
                day -= timedelta(days=1)
                continue
            print(f"{day.date()} -> HATA: HTTP {exc.code}")
            day -= timedelta(days=1)
            continue
        except Exception as exc:
            print(f"{day.date()} -> HATA: {exc}")
            day -= timedelta(days=1)
            continue

        try:
            matched, added = process_payload(day, payload, conn)
            successful_days += 1
            total_added += added
            print(f"{day.date()} -> arşiv bulundu | eşleşen={matched}, yeni={added}")
        except Exception as exc:
            print(f"{day.date()} -> DOSYA İŞLEME HATASI: {exc}")

        day -= timedelta(days=1)

    conn.close()
    print()
    print(f"Başarılı arşiv günü: {successful_days}/{TARGET_DAYS}")
    print(f"Taranan takvim günü: {scanned_days}")
    print(f"Toplam yeni tarihsel haber: {total_added}")


if __name__ == "__main__":
    main()
