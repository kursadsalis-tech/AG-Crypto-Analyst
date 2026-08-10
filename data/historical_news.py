"""Resilient historical crypto-news backfill using the public GDELT DOC 2.0 API.

No API key is required. The backfill is intentionally conservative and
restartable: it stores a per-source checkpoint, retries rate limits/timeouts
with exponential backoff, and recursively splits busy windows so a GDELT
result cap cannot silently discard articles.
"""

import json
import os
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from news_intelligence.news_repository import create_news_table, get_news_count, save_news_batch

DB_NAME = "crypto_data.db"
API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
PAGE_SIZE = 250
DEFAULT_DAYS = 30
WINDOW_HOURS = 24
MIN_WINDOW_MINUTES = 60
BASE_DELAY = 2.0
MAX_RETRIES = 6
STATE_PATH = Path("reports/gdelt_backfill_state.json")

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


def load_state():
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp.replace(STATE_PATH)


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

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(API_URL, params=params, timeout=30)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else min(120.0, BASE_DELAY * (2 ** attempt))
                wait += random.uniform(0.5, 1.5)
                print(f"    GDELT rate-limit (429), {wait:.1f} sn bekleniyor...")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json().get("articles", [])
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt == MAX_RETRIES - 1:
                break
            wait = min(120.0, BASE_DELAY * (2 ** attempt)) + random.uniform(0.5, 1.5)
            print(f"    Geçici bağlantı hatası, {wait:.1f} sn sonra tekrar...")
            time.sleep(wait)

    raise RuntimeError(f"GDELT sorgusu başarısız: {last_error}")


def fetch_complete_window(domain, start_dt, end_dt):
    """Fetch a complete interval; split busy intervals at the GDELT cap."""
    articles = fetch_window(domain, start_dt, end_dt)

    duration_minutes = int((end_dt - start_dt).total_seconds() / 60)
    if len(articles) < PAGE_SIZE or duration_minutes <= MIN_WINDOW_MINUTES:
        return articles

    midpoint = start_dt + (end_dt - start_dt) / 2
    left = fetch_complete_window(domain, start_dt, midpoint)
    right = fetch_complete_window(domain, midpoint, end_dt)
    return left + right


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
    state = load_state()

    print("AG Crypto Analyst - Resilient Historical News Backfill")
    print("Kaynak: GDELT DOC 2.0 (API anahtarı gerektirmez)")
    print(f"Pencere : {requested_start.isoformat()} -> {now.isoformat()}")
    print(f"Kaynaklar: {', '.join(DOMAINS.values())}")
    print("Özellikler: retry + rate-limit backoff + checkpoint + yoğun pencere bölme")
    print()

    total_seen = 0
    total_candidates = 0

    for domain, source_name in DOMAINS.items():
        key = domain
        if key in state and state[key].get("next_start"):
            current = parse_iso(state[key]["next_start"])
            if current < requested_start:
                current = requested_start
        else:
            current = requested_start

        if current >= now:
            print(f"{source_name}: tamamlanmış görünüyor, atlanıyor.")
            continue

        print(f"\n{source_name}: {current.isoformat()} -> {now.isoformat()}")

        while current < now:
            window_end = min(current + timedelta(hours=WINDOW_HOURS), now)
            try:
                articles = fetch_complete_window(domain, current, window_end)
                normalized = [
                    item for article in articles
                    if (item := normalize_article(article, source_name)) is not None
                ]
                if normalized:
                    save_news_batch(normalized)

                total_seen += len(articles)
                total_candidates += len(normalized)
                print(
                    f"  {current.strftime('%Y-%m-%d %H:%M')} -> "
                    f"{window_end.strftime('%Y-%m-%d %H:%M')} | "
                    f"{len(articles):3d} haber"
                )

                # Checkpoint advances only after a successful complete window.
                state[key] = {"next_start": window_end.isoformat()}
                save_state(state)
                current = window_end
                time.sleep(BASE_DELAY + random.uniform(0.5, 1.0))

            except Exception as exc:
                print(f"  HATA: {exc}")
                print("  Bu pencere tamamlanamadı; checkpoint ilerletilmiyor.")
                print("  Programu yeniden çalıştırmak güvenlidir; aynı pencereden devam eder.")
                save_state(state)
                break

    print()
    print(f"GDELT'den alınan kayıt: {total_seen}")
    print(f"Normalize edilen aday: {total_candidates}")
    print(f"Veritabanındaki toplam haber: {get_news_count()}")
    print(f"Checkpoint: {STATE_PATH}")


if __name__ == "__main__":
    main()
