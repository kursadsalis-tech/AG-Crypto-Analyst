import sqlite3
from datetime import datetime

DB_NAME = "crypto_data.db"


def save_price(
    coin,
    price,
    change_24h,
    volume,
    high_24h,
    low_24h,
    open_price,
    weighted_avg_price,
    trade_count
):
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()

    cursor.execute("""
        INSERT INTO crypto_prices
        (
            timestamp,
            coin,
            price,
            change_24h,
            volume,
            high_24h,
            low_24h,
            open_price,
            weighted_avg_price,
            trade_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        coin,
        float(price),
        float(change_24h),
        float(volume),
        float(high_24h),
        float(low_24h),
        float(open_price),
        float(weighted_avg_price),
        int(trade_count)
    ))

    connection.commit()
    connection.close()

    print(f"✅ {coin} veritabanına kaydedildi.")