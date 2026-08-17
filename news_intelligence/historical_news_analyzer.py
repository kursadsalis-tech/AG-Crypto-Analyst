"""Leakage-safe analyzer for historical GDELT news.

Historical observations stay isolated from live crypto_news tables. The same
classification logic is reused, but results are written to a dedicated table
keyed by historical_news_raw.id.
"""
from __future__ import annotations

import os
import sqlite3
from database.runtime_store import connect as runtime_connect

from news_intelligence.news_analyzer import classify_news

DB_PATH = os.getenv("AGCA_DB", "crypto_data.db")


def create_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS historical_news_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            historical_news_id INTEGER NOT NULL UNIQUE,
            coins TEXT NOT NULL,
            event_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            impact_score INTEGER NOT NULL,
            confidence_score INTEGER NOT NULL,
            matched_keywords TEXT,
            analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (historical_news_id) REFERENCES historical_news_raw(id)
        )
    """)
    conn.commit()


def load_historical_news(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, source, title, url, published_at
        FROM historical_news_raw
        ORDER BY published_at ASC, id ASC
    """).fetchall()
    return [
        {
            "id": row["id"],
            "source": row["source"],
            "title": row["title"],
            "link": row["url"],
            "description": "",
            "published_at": row["published_at"],
        }
        for row in rows
    ]


def save_analysis(conn: sqlite3.Connection, analysis: dict) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO historical_news_analysis (
            historical_news_id, coins, event_type, direction,
            impact_score, confidence_score, matched_keywords
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        analysis["news_id"], analysis["coins"], analysis["event_type"],
        analysis["direction"], analysis["impact_score"],
        analysis["confidence_score"], analysis["matched_keywords"],
    ))


def main() -> None:
    conn = runtime_connect(DB_PATH)
    create_table(conn)
    articles = load_historical_news(conn)
    analyses = []
    for article in articles:
        analysis = classify_news(article)
        save_analysis(conn, analysis)
        analyses.append(analysis)
    conn.commit()

    print("AG Crypto Analyst - Historical News Analyzer")
    print(f"Tarihsel haber      : {len(articles)}")
    print(f"Tarihsel analiz     : {len(analyses)}")
    print("Canlı haber tabloları: değiştirilmedi")
    print()
    for analysis in analyses[:20]:
        print(
            f"#{analysis['news_id']} | {analysis['coins']} | "
            f"{analysis['event_type']} | {analysis['direction']} | "
            f"impact={analysis['impact_score']} | "
            f"confidence={analysis['confidence_score']}"
        )
    conn.close()


if __name__ == "__main__":
    main()
