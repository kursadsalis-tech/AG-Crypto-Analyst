import sqlite3
from database.runtime_store import connect as runtime_connect

DB_NAME = "crypto_data.db"


def create_news_table():
    connection = runtime_connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS crypto_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            link TEXT NOT NULL UNIQUE,
            description TEXT,
            published_at TEXT,
            collected_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    connection.commit()
    connection.close()


def create_news_analysis_table():
    connection = runtime_connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS crypto_news_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id INTEGER NOT NULL UNIQUE,
            coins TEXT NOT NULL,
            event_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            impact_score INTEGER NOT NULL,
            confidence_score INTEGER NOT NULL,
            matched_keywords TEXT,
            analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (news_id) REFERENCES crypto_news(id)
        )
    """)

    connection.commit()
    connection.close()


def save_news_batch(articles):
    connection = runtime_connect(DB_NAME)
    cursor = connection.cursor()

    cursor.executemany("""
        INSERT OR IGNORE INTO crypto_news (
            source,
            title,
            link,
            description,
            published_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, [
        (
            article["source"],
            article["title"],
            article["link"],
            article["description"],
            article["published_at"]
        )
        for article in articles
    ])

    connection.commit()
    connection.close()


def get_all_news():
    connection = runtime_connect(DB_NAME)
    connection.row_factory = sqlite3.Row
    cursor = connection.cursor()

    cursor.execute("""
        SELECT id, source, title, link, description, published_at
        FROM crypto_news
        ORDER BY published_at DESC, id DESC
    """)

    rows = [dict(row) for row in cursor.fetchall()]
    connection.close()
    return rows


def save_news_analysis(analysis):
    connection = runtime_connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO crypto_news_analysis (
            news_id,
            coins,
            event_type,
            direction,
            impact_score,
            confidence_score,
            matched_keywords
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(news_id) DO UPDATE SET
            coins=excluded.coins,event_type=excluded.event_type,
            direction=excluded.direction,impact_score=excluded.impact_score,
            confidence_score=excluded.confidence_score,
            matched_keywords=excluded.matched_keywords,
            analyzed_at=CURRENT_TIMESTAMP
        WHERE crypto_news_analysis.coins IS NOT excluded.coins
           OR crypto_news_analysis.event_type IS NOT excluded.event_type
           OR crypto_news_analysis.direction IS NOT excluded.direction
           OR crypto_news_analysis.impact_score IS NOT excluded.impact_score
           OR crypto_news_analysis.confidence_score IS NOT excluded.confidence_score
           OR crypto_news_analysis.matched_keywords IS NOT excluded.matched_keywords
    """, (
        analysis["news_id"],
        analysis["coins"],
        analysis["event_type"],
        analysis["direction"],
        analysis["impact_score"],
        analysis["confidence_score"],
        analysis["matched_keywords"]
    ))

    connection.commit()
    connection.close()


def save_news_analysis_batch(analyses):
    connection = runtime_connect(DB_NAME)
    cursor = connection.cursor()

    cursor.executemany("""
        INSERT INTO crypto_news_analysis (
            news_id,
            coins,
            event_type,
            direction,
            impact_score,
            confidence_score,
            matched_keywords
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(news_id) DO UPDATE SET
            coins=excluded.coins,event_type=excluded.event_type,
            direction=excluded.direction,impact_score=excluded.impact_score,
            confidence_score=excluded.confidence_score,
            matched_keywords=excluded.matched_keywords,
            analyzed_at=CURRENT_TIMESTAMP
        WHERE crypto_news_analysis.coins IS NOT excluded.coins
           OR crypto_news_analysis.event_type IS NOT excluded.event_type
           OR crypto_news_analysis.direction IS NOT excluded.direction
           OR crypto_news_analysis.impact_score IS NOT excluded.impact_score
           OR crypto_news_analysis.confidence_score IS NOT excluded.confidence_score
           OR crypto_news_analysis.matched_keywords IS NOT excluded.matched_keywords
    """, [
        (
            analysis["news_id"],
            analysis["coins"],
            analysis["event_type"],
            analysis["direction"],
            analysis["impact_score"],
            analysis["confidence_score"],
            analysis["matched_keywords"]
        )
        for analysis in analyses
    ])

    connection.commit()
    connection.close()


def get_news_analysis_count():
    connection = runtime_connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("SELECT COUNT(*) FROM crypto_news_analysis")
    count = cursor.fetchone()[0]

    connection.close()
    return count


def get_news_count():
    connection = runtime_connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("SELECT COUNT(*) FROM crypto_news")
    count = cursor.fetchone()[0]

    connection.close()
    return count
