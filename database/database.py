import sqlite3

DB_NAME = "crypto_data.db"

connection = sqlite3.connect(DB_NAME)

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