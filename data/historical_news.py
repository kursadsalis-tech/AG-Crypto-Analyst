"""Historical news backfill using the CoinDesk Data API.

The live RSS collector remains unchanged. This module is intentionally separate
so historical research data cannot silently alter the live ingestion path.
Set COINDESK_API_KEY in the environment before running.
"""

import os
import sqlite3
import time
from datetime import datetime, timezone

import requests

from news_intelligence.news_repository import create_news_table, save_news_batch, get_news_count

DB_NAME = "crypto_data.db"
API_URL = "https://data-api.coindesk.com/news/v1/article/list"
PAGE_SIZE = 100
SOURCE_IDS = ["coindesk"]


def iso_from_timestamp(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


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
    return int(dt.timestamp())


def fetch_batch(api_key, to_ts):
    response = requests.get(
        API_URL,
        params={
            "lang": "EN",
            "source_ids": SOURCE_IDS,
            "limit": PAGE_SIZE,
            "to_ts": to_ts,
        },
        headers={"Authorization": f"Apikey {api_key}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("Data", [])


def normalize_article(article):
    title = article.get("TITLE") or article.get("title") or ""
    description = article.get("DESCRIPTION") or article.get("description") or ""
    link = article.get("URL") or article.get("url") or ""
    ts = article.get("PUBLISHED_ON") or article.get("published_on")
    if not ts:
        return None
    return {
        "source": "CoinDesk",
        "title": title,
        "link": link,
        "description": description,
        "published_at": iso_from_timestamp(int(ts)),
    }


def main():
    api_key = os.getenv("COINDESK_API_KEY")
    if not api_key:
        print("HATA: COINDESK_API_KEY tanımlı değil.")
        print("CoinDesk Data API geçmiş haber arşivini kullanmak için API anahtarı gerekiyor.")
        return

    create_news_table()
    oldest = get_oldest_news_timestamp()
    cursor_ts = oldest if oldest is not None else int(datetime.now(tz=timezone.utc).timestamp())
    total = 0
    seen_earliest = None

    print("AG Crypto Analyst - Historical News Backfill")
    print("Kaynak: CoinDesk Data API")
    print()

    while True:
        batch = fetch_batch(api_key, cursor_ts)
        if not batch:
            break

        articles = []
        timestamps = []
        for article in batch:
            normalized = normalize_article(article)
            if normalized:
                articles.append(normalized)
                timestamps.append(int(article.get("PUBLISHED_ON") or article.get("published_on")))

        if articles:
            save_news_batch(articles)
            total += len(articles)

        earliest = min(timestamps) if timestamps else None
        if earliest is None or seen_earliest == earliest:
            break
        seen_earliest = earliest
        cursor_ts = earliest - 1
        print(f"Toplam yeni/işlenen haber: {total} | geriye gidilen tarih: {iso_from_timestamp(cursor_ts)}")
        time.sleep(0.25)

        if len(batch) < PAGE_SIZE:
            break

    print()
    print(f"Bu çalışmada işlenen haber: {total}")
    print(f"Veritabanındaki toplam haber: {get_news_count()}")


if __name__ == "__main__":
    main()
