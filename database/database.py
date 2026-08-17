import sqlite3
from database.runtime_store import connect as runtime_connect

DB_NAME = "crypto_data.db"

connection = runtime_connect(DB_NAME)

cursor = connection.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS crypto_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    coin TEXT,
    price REAL,
    change_24h REAL,
    volume REAL,
    high_24h REAL,
    low_24h REAL
)
""")

connection.commit()

connection.close()

print("✅ Veritabanı ve crypto_prices tablosu hazır.")