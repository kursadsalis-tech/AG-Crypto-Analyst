"""Historical crypto-news backfill using the public GDELT DOC 2.0 API.

This module deliberately does not require an API key.  It reconstructs a
historical, time-ordered news set from public GDELT article metadata and keeps
the first-seen timestamp as the decision timestamp.  Using first-seen time is
conservative for leakage-safe backtests: the model is never allowed to act
before GDELT had observed the article.

The backfill covers the recent historical window supported by the public DOC
API and is designed to be rerun safely because the news repository de-duplicates
by URL.
"""

import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import requests

from news_intelligence.news_repository import create_news_table, save_news_batch, get_news_count

DB_NAME = "crypto_data.db"
API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
PAGE_SIZE = 250
REQUEST_DELAY = 0.25
DEFAULT_DAYS = 90

# Keep the source set aligned with the live AGCA collector while allowing the
# historical layer to remain independent from any one publisher's API.
DOMAINS = {
    "coindesk.com": "CoinDesk",
    "cointelegraph.com": "Cointelegraph",
    "theblock.co": "The Block",
}

CRYPTO_QUERY = (
    '(bitcoin OR btc OR ethereum OR eth OR xrp OR ripple OR solana OR bnb '
    'OR cardano OR dogecoin OR tron OR chainlink OR avalanche OR sui '
    'OR hyperliquid OR crypto OR cryptocurrency OR stablecoin OR bitcoin)'
)


def iso_from_gdelt_date(value):
    """Convert GDELT YYYYMMDDHHMMSS timestamps to UTC ISO-8601."""
    dt = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return dt.isoformat()


def get_oldest_news_timestamp():
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()
    cursor.execute("SELECT MIN(published_at) FROM crypto_news WHERE published_at IS NOT NULL")
    row = cursor.fetchone()
    connection.close()
    if not row or not row[0]:
        return None
    value = row[0].replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fetch_window(domain, start_dt, end_dt):
    """Fetch up to PAGE_SIZE articles for one source and one UTC day."""
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
    payload = response.json()
    return payload.get("articles", [])


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
    days = max(1, min(days, 90))

    now = datetime.now(timezone.utc)
    oldest_existing = get_oldest_news_timestamp()
    requested_start = now - timedelta(days=days)

    # If the DB already contains older data, only request the missing recent
    # window. This makes repeated runs incremental rather than wasteful.
    start_dt = requested_start
    if oldest_existing and oldest_existing > requested_start:
        start_dt = requested_start

    print("AG Crypto Analyst - Historical News Backfill")
    print("Kaynak: GDELT DOC 2.0 (API anahtarı gerektirmez)")
    print(f"Pencere : {start_dt.isoformat()} -> {now.isoformat()}")
    print(f"Kaynaklar: {', '.join(DOMAINS.values())}")
    print()

    total_seen = 0
    total_saved_candidates = 0

    current = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    while current < now:
        day_end = min(current + timedelta(days=1), now)

        for domain, source_name in DOMAINS.items():
            try:
                articles = fetch_window(domain, current, day_end)
                normalized = []
                for article in articles:
                    item = normalize_article(article, source_name)
                    if item:
                        normalized.append(item)

                if normalized:
                    save_news_batch(normalized)
                    total_saved_candidates += len(normalized)
                total_seen += len(articles)
                print(
                    f"{current.date()} | {source_name:<14} -> "
                    f"{len(articles):3d} haber"
                )
            except Exception as exc:
                print(f"{current.date()} | {source_name:<14} -> HATA: {exc}")

            time.sleep(REQUEST_DELAY)

        current = day_end

    print()
    print(f"GDELT'den alınan kayıt: {total_seen}")
    print(f"Veritabanına gönderilen aday: {total_saved_candidates}")
    print(f"Veritabanındaki toplam haber: {get_news_count()}")


if __name__ == "__main__":
    main()
