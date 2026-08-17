"""AG Crypto Analyst v0.6.0 - first working Social Sentiment Agent.

Collects public Reddit crypto RSS posts, detects tracked/dynamic Binance assets,
scores simple transparent sentiment, and stores deduplicated observations.
"""
from __future__ import annotations

import html
import re
import sqlite3
from database.runtime_store import connect as runtime_connect
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from news_intelligence.news_analyzer import (
    COIN_KEYWORDS,
    find_dynamic_tickers,
    get_dynamic_binance_spot_assets,
    normalize,
)


DB_NAME = "crypto_data.db"
# Reddit rate-limits multiple rapid RSS requests. A combined subreddit feed keeps
# collection to one request while retaining coverage across the three communities.
SOURCE_URLS = {
    "Reddit Crypto (broad market feed)":
        "https://www.reddit.com/r/CryptoCurrency+Bitcoin+ethtrader+altcoin+CryptoMarkets/new/.rss?limit=100",
}
POSITIVE_WORDS = {
    "adoption", "approval", "bullish", "breakout", "buy", "growth", "partnership",
    "rally", "record", "recovery", "surge", "upgrade", "win",
}
NEGATIVE_WORDS = {
    "attack", "ban", "bearish", "collapse", "crash", "delay", "drop", "exploit",
    "fraud", "hack", "lawsuit", "loss", "risk", "scam", "sell",
}
ATOM = {"atom": "http://www.w3.org/2005/Atom"}
DYNAMIC_TICKER_BLACKLIST = {"GMT", "ALT"}
SOCIAL_ALIAS_OVERRIDES = {
    # These tickers are also common words/abbreviations in social posts.
    "LINK": ["chainlink"],
    "BIO": ["bio protocol"],
    "TIA": ["celestia"],
    "UMA": ["uma protocol"],
    "IO": ["io.net", "ionet", "io token"],
}


def ensure_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS social_sentiment_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            external_id TEXT NOT NULL,
            title TEXT NOT NULL,
            link TEXT,
            published_at TEXT,
            collected_at TEXT NOT NULL,
            coins TEXT NOT NULL,
            sentiment TEXT NOT NULL,
            sentiment_score REAL NOT NULL,
            positive_hits TEXT,
            negative_hits TEXT,
            UNIQUE(source, external_id)
        )
    """)
    columns = {row[1] for row in con.execute("PRAGMA table_info(social_sentiment_observations)")}
    if "content_text" not in columns:
        con.execute("ALTER TABLE social_sentiment_observations ADD COLUMN content_text TEXT")
    # Early v0.6.0 tests used changing source labels, so the same Reddit entry
    # could be stored twice. Preserve the earliest row and make external IDs
    # globally idempotent from now on.
    con.execute("""
        DELETE FROM social_sentiment_observations
        WHERE id NOT IN (
            SELECT MIN(id) FROM social_sentiment_observations GROUP BY external_id
        )
    """)
    con.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_social_sentiment_external_id
        ON social_sentiment_observations(external_id)
    """)
    con.commit()


def fetch_feed(source, url):
    response = requests.get(
        url,
        timeout=20,
        headers={"User-Agent": "AG-Crypto-Analyst/0.6.0 (public RSS research)"},
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    rows = []
    for entry in root.findall("atom:entry", ATOM):
        link_node = entry.find("atom:link", ATOM)
        raw_content = (
            entry.findtext("atom:content", default="", namespaces=ATOM)
            or entry.findtext("atom:summary", default="", namespaces=ATOM)
        )
        content_text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(raw_content))).strip()
        rows.append({
            "source": source,
            "external_id": entry.findtext("atom:id", default="", namespaces=ATOM),
            "title": entry.findtext("atom:title", default="", namespaces=ATOM),
            "link": link_node.get("href", "") if link_node is not None else "",
            "published_at": entry.findtext("atom:updated", default="", namespaces=ATOM),
            "content_text": content_text,
        })
    return rows


def explicit_coin_matches(title, content_text, dynamic_assets):
    raw_text = f"{title} {content_text}"
    lowered = normalize(raw_text)
    coins = set()
    for coin, default_aliases in COIN_KEYWORDS.items():
        aliases = SOCIAL_ALIAS_OVERRIDES.get(coin, default_aliases)
        alias_found = any(
            re.search(r"\b" + re.escape(alias) + r"\b", lowered)
            for alias in aliases
        )
        explicit_ticker = re.search(
            rf"(?<![A-Za-z0-9])\$?{re.escape(coin)}(?![A-Za-z0-9])",
            raw_text,
        )
        if alias_found or explicit_ticker:
            coins.add(coin)

    # Dynamic discovery is intentionally title-only. Body text contains many
    # unrelated uppercase abbreviations that would otherwise look like coins.
    dynamic = find_dynamic_tickers(title, dynamic_assets)
    coins.update(coin for coin in dynamic if coin not in DYNAMIC_TICKER_BLACKLIST)
    return sorted(coins)


def score_title(title):
    text = normalize(title)
    positive = sorted(word for word in POSITIVE_WORDS if re.search(r"\b" + re.escape(word) + r"\b", text))
    negative = sorted(word for word in NEGATIVE_WORDS if re.search(r"\b" + re.escape(word) + r"\b", text))
    raw = len(positive) - len(negative)
    score = max(-1.0, min(1.0, raw / 3.0))
    sentiment = "positive" if score > 0 else "negative" if score < 0 else "neutral"
    return sentiment, score, positive, negative


def save_rows(con, rows, dynamic_assets):
    saved = 0
    collected_at = datetime.now(timezone.utc).isoformat()
    known_ids = {
        row[0] for row in con.execute(
            "SELECT external_id FROM social_sentiment_observations"
        ).fetchall()
    }
    for row in rows:
        external_id = row["external_id"] or row["link"]
        analysis_text = f"{row['title']} {row.get('content_text', '')}"
        coins = explicit_coin_matches(
            row["title"], row.get("content_text", ""), dynamic_assets
        )
        sentiment, score, positive, negative = score_title(analysis_text)
        con.execute("""
            INSERT OR IGNORE INTO social_sentiment_observations (
                source, external_id, title, link, published_at, collected_at,
                coins, sentiment, sentiment_score, positive_hits, negative_hits,
                content_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(external_id) DO UPDATE SET
                source=excluded.source,
                title=excluded.title,
                link=excluded.link,
                published_at=excluded.published_at,
                collected_at=excluded.collected_at,
                coins=excluded.coins,
                sentiment=excluded.sentiment,
                sentiment_score=excluded.sentiment_score,
                positive_hits=excluded.positive_hits,
                negative_hits=excluded.negative_hits,
                content_text=excluded.content_text
        """, (
            row["source"], external_id, row["title"], row["link"],
            row["published_at"], collected_at, ",".join(coins) if coins else "MARKET",
            sentiment, score, ",".join(positive), ",".join(negative), row.get("content_text", ""),
        ))
        if external_id not in known_ids:
            saved += 1
            known_ids.add(external_id)
    con.commit()
    return saved


def summary(con):
    return con.execute("""
        SELECT coins, COUNT(*), ROUND(AVG(sentiment_score), 3)
        FROM social_sentiment_observations
        GROUP BY coins ORDER BY COUNT(*) DESC, coins LIMIT 15
    """).fetchall()


def main():
    try:
        dynamic_assets = get_dynamic_binance_spot_assets()
    except Exception:
        dynamic_assets = set()
    collected = []
    for source, url in SOURCE_URLS.items():
        try:
            rows = fetch_feed(source, url)
            collected.extend(rows)
            print(f"{source}: {len(rows)} paylaşım alındı")
        except Exception as exc:
            print(f"{source}: HATA -> {exc}")

    con = runtime_connect(DB_NAME)
    try:
        ensure_table(con)
        saved = save_rows(con, collected, dynamic_assets)
        print("\nAG Crypto Analyst - Social Sentiment Agent v0.6.0")
        print(f"Toplanan: {len(collected)} | Yeni kaydedilen: {saved}")
        print("Coin/market özeti (gözlem | ortalama skor):")
        for coins, count, avg_score in summary(con):
            print(f"{coins:<18} {count:>4} | {avg_score:+.3f}")
    finally:
        con.close()

    return {
        "collected": len(collected),
        "saved": saved,
    }


if __name__ == "__main__":
    main()
