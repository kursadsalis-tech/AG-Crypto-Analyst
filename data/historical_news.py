"""Historical crypto-news backfill using the public GDELT DOC 2.0 API.

No API key is required. The backfill searches the configured crypto news
publishers in small time windows so the result can be replayed chronologically.
GDELT's first-seen timestamp is stored as the conservative decision timestamp.
"""

import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import requests

from news_intelligence.news_repository import create_news_table, get_news_count, save_news_batch

DB_NAME = "crypto_data.db"
API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
PAGE_SIZE = 250
REQUEST_DELAY = 0.25
DEFAULT_DAYS = 90
WINDOW_HOURS = 6

DOMAINS = {
    "coindesk.com": "CoinDesk",
    "cointelegraph.com": "Cointelegraph",
    "theblock.co": "The Block",
}

CRYPTO_QUERY = (
    '(bitcoin OR btc OR ethereum OR eth OR xrp OR ripple OR solana OR bnb '
    'OR cardano OR dogecoin OR tron OR chainlink OR avalanche OR sui '
    'OR hyperliquid OR crypto OR cryptocurrency OR stablecoin)'
)


def iso_from_gdelt_date(value):
    dt = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return dt.isoformat()


def parse_iso(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_oldest_news_timestamp():
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()
    cursor.execute("SELECT MIN(published_at) FROM crypto_news WHERE published_at IS NOT NULL")
    row = cursor.fetchone()
    connection.close()
    return parse_iso(row[0]) if row and row[0] else None


def fetch_window(domain, start_dt, end_dt):
    params = {
        "query": f"domain:{domain} {CRYPTO_QUERY}",
        "mode": "artlist",
        "maxrecords": PAGE_SIZE,
        "format": "json",
        "sort": "datedesc",
        "startdatetime": start_dt.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end_dt.strftime("%Y%m%d%H%M%S"),
    }
    response = requests.get(API_URL, params=params, timeout=45)
    response.raise_for_status()
    return response.json().get("articles", [])


def normalize_article(article, source_name):
    title = (article.get("title") or "").strip()
    link = (article.get("url") or "").strip()
    seen_date = (article.get("seendate") or "").strip()
    if not title or not link or not seen_date:
        return None
    try:
        published_at = iso_from_gdelt_date(seen_date)
    except ValueError:
        return None
    return {
        "source": f"GDELT/{source_name}",
        "title": title,
        "link": link,
        "description": "",
        "published_at": published_at,
    }


def main():
    create_news_table()
    try:
        days = int(os.getenv("GDELT_BACKFILL_DAYS", str(DEFAULT_DAYS)))
    except ValueError:
        days = DEFAULT_DAYS
    days = max(1, min(days, 3650))

    now = datetime.now(timezone.utc)
    requested_start = now - timedelta(days=days)
    oldest_existing = get_oldest_news_timestamp()
    if oldest_existing and oldest_existing <= requested_start:
        print("GDELT backfill: requested history already reaches or exceeds the requested start.")
        print(f"Veritabanındaki en eski haber: {oldest_existing.isoformat()}")
        return

    # Extend slightly beyond the current oldest record so the backfill fills
    # the missing interval instead of repeatedly walking the already-collected range.
    start_dt = requested_start
    if oldest_existing and oldest_existing > requested_start:
        start_dt = requested_start

    print("AG Crypto Analyst - Historical News Backfill")
    print("Kaynak: GDELT DOC 2.0 (API anahtarı gerektirmez)")
    print(f"Pencere : {start_dt.isoformat()} -> {now.isoformat()}")
    print(f"Kaynaklar: {', '.join(DOMAINS.values())}")
    print(f"Pencere boyutu: {WINDOW_HOURS} saat")
    print()

    total_seen = 0
    total_candidates = 0
    current = start_dt

    while current < now:
        window_end = min(current + timedelta(hours=WINDOW_HOURS), now)
        for domain, source_name in DOMAINS.items():
            try:
                articles = fetch_window(domain, current, window_end)
                normalized = [
                    item for article in articles
                    if (item := normalize_article(article, source_name)) is not None
                ]
                if normalized:
                    save_news_batch(normalized)
                total_seen += len(articles)
                total_candidates += len(normalized)
                print(
                    f"{current.strftime('%Y-%m-%d %H:%M')} -> {window_end.strftime('%H:%M')} | "
                    f"{source_name:<14} -> {len(articles):3d} haber"
                )
            except Exception as exc:
                print(
                    f"{current.strftime('%Y-%m-%d %H:%M')} -> {window_end.strftime('%H:%M')} | "
                    f"{source_name:<14} -> HATA: {exc}"
                )
            time.sleep(REQUEST_DELAY)
        current = window_end

    print()
    print(f"GDELT'den alınan kayıt: {total_seen}")
    print(f"Normalize edilen aday: {total_candidates}")
    print(f"Veritabanındaki toplam haber: {get_news_count()}")


if __name__ == "__main__":
    main()
