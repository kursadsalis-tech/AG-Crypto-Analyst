import sqlite3

DB_NAME = "crypto_data.db"


def create_news_table():
    connection = sqlite3.connect(DB_NAME)
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


def save_news_batch(articles):
    connection = sqlite3.connect(DB_NAME)
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


def get_news_count():
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("SELECT COUNT(*) FROM crypto_news")
    count = cursor.fetchone()[0]

    connection.close()
    return count
