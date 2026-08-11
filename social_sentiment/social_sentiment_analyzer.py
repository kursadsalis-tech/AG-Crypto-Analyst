"""Aggregate raw social observations into decision-ready coin signals."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone


DB_NAME = "crypto_data.db"
WINDOW_HOURS = 24


def parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def load_recent(con):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    rows = con.execute("""
        SELECT coins, sentiment, sentiment_score, published_at
        FROM social_sentiment_observations
        WHERE coins != 'MARKET'
    """).fetchall()
    return [row for row in rows if (parse_time(row[3]) or cutoff) >= cutoff]


def confidence_for_mentions(mentions):
    # Conservative until social coverage grows: 10 posts = 50, 20+ = 100.
    return min(100, mentions * 5)


def signal_for_score(score):
    if score >= 0.25:
        return "POSITIVE"
    if score <= -0.25:
        return "NEGATIVE"
    return "NEUTRAL"


def aggregate(rows):
    grouped = defaultdict(list)
    for coins, sentiment, score, _ in rows:
        for coin in coins.split(","):
            coin = coin.strip().upper()
            if coin and coin != "MARKET" and coin != "GMT":
                grouped[coin].append((sentiment, float(score)))

    results = []
    for coin, values in grouped.items():
        mentions = len(values)
        avg_score = sum(score for _, score in values) / mentions
        results.append({
            "coin": coin,
            "mentions": mentions,
            "positive": sum(sentiment == "positive" for sentiment, _ in values),
            "negative": sum(sentiment == "negative" for sentiment, _ in values),
            "neutral": sum(sentiment == "neutral" for sentiment, _ in values),
            "score": avg_score,
            "signal": signal_for_score(avg_score),
            "confidence": confidence_for_mentions(mentions),
        })
    return sorted(results, key=lambda row: (-row["mentions"], row["coin"]))


def ensure_snapshot_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS social_sentiment_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            window_hours INTEGER NOT NULL,
            coin TEXT NOT NULL,
            mentions INTEGER NOT NULL,
            positive INTEGER NOT NULL,
            negative INTEGER NOT NULL,
            neutral INTEGER NOT NULL,
            sentiment_score REAL NOT NULL,
            signal TEXT NOT NULL,
            confidence INTEGER NOT NULL
        )
    """)
    con.commit()


def save_snapshot(con, results):
    created_at = datetime.now(timezone.utc).isoformat()
    con.executemany("""
        INSERT INTO social_sentiment_snapshots (
            created_at, window_hours, coin, mentions, positive, negative,
            neutral, sentiment_score, signal, confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [(
        created_at, WINDOW_HOURS, row["coin"], row["mentions"], row["positive"],
        row["negative"], row["neutral"], row["score"], row["signal"],
        row["confidence"],
    ) for row in results])
    con.commit()


def main():
    con = sqlite3.connect(DB_NAME)
    try:
        ensure_snapshot_table(con)
        results = aggregate(load_recent(con))
        save_snapshot(con, results)
    finally:
        con.close()

    print(f"AG Crypto Analyst - Social Sentiment Analyzer | son {WINDOW_HOURS} saat")
    if not results:
        print("Analize uygun coin bazlı sosyal gözlem yok.")
        return
    print("COIN     MENTION  POS NEG NEU   SCORE  SIGNAL    CONF")
    for row in results:
        print(
            f"{row['coin']:<8} {row['mentions']:>7}  {row['positive']:>3} {row['negative']:>3} "
            f"{row['neutral']:>3}  {row['score']:>+6.3f}  {row['signal']:<8} {row['confidence']:>3}/100"
        )


if __name__ == "__main__":
    main()
