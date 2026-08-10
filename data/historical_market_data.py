import sqlite3
import time
from datetime import datetime, timezone

import requests


DB_NAME = "crypto_data.db"
BASE_URL = "https://api.binance.com/api/v3/klines"
INTERVAL = "1h"
LIMIT = 1000

COIN_TO_SYMBOL = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "XRP": "XRPUSDT",
    "HYPE": "HYPEUSDT",
    "CRO": "CROUSDT",
    "ABTC": "ABTCUSDT",
    "SOL": "SOLUSDT",
    "BNB": "BNBUSDT",
    "ADA": "ADAUSDT",
    "DOGE": "DOGEUSDT",
    "TRX": "TRXUSDT",
    "LINK": "LINKUSDT",
    "AVAX": "AVAXUSDT",
    "SUI": "SUIUSDT",
}


def create_market_klines_table():
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_klines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            open_time TEXT NOT NULL,
            close_time TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            UNIQUE(symbol, interval, open_time)
        )
    """)
    connection.commit()
    connection.close()


def get_news_time_range():
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()
    cursor.execute("""
        SELECT MIN(published_at), MAX(published_at)
        FROM crypto_news
        WHERE published_at IS NOT NULL
    """)
    row = cursor.fetchone()
    connection.close()
    return row


def to_milliseconds(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def save_klines(symbol, klines):
    if not klines:
        return
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()
    cursor.executemany("""
        INSERT OR IGNORE INTO market_klines (
            symbol, interval, open_time, close_time,
            open, high, low, close, volume
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (
            symbol,
            INTERVAL,
            datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc).isoformat(),
            datetime.fromtimestamp(row[6] / 1000, tz=timezone.utc).isoformat(),
            float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
        )
        for row in klines
    ])
    connection.commit()
    connection.close()


def fetch_symbol_history(symbol, start_ms, end_ms):
    total = 0
    current_start = start_ms
    while current_start < end_ms:
        response = requests.get(
            BASE_URL,
            params={
                "symbol": symbol,
                "interval": INTERVAL,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": LIMIT,
            },
            timeout=15,
        )
        response.raise_for_status()
        klines = response.json()
        if not klines:
            break
        save_klines(symbol, klines)
        total += len(klines)
        next_start = klines[-1][0] + 1
        if next_start <= current_start:
            break
        current_start = next_start
        if len(klines) < LIMIT:
            break
        time.sleep(0.15)
    return total


def main():
    create_market_klines_table()
    min_news_time, max_news_time = get_news_time_range()
    if not min_news_time or not max_news_time:
        print("HATA: crypto_news tablosunda tarihli haber bulunamadı.")
        return

    start_ms = to_milliseconds(min_news_time)
    end_ms = to_milliseconds(max_news_time) + 24 * 60 * 60 * 1000

    print("AG Crypto Analyst - Historical Market Data")
    print(f"Haber başlangıcı : {min_news_time}")
    print(f"Backtest sonu    : {max_news_time}")
    print("Ufuk             : +24 saat")
    print()

    for coin, symbol in COIN_TO_SYMBOL.items():
        try:
            count = fetch_symbol_history(symbol, start_ms, end_ms)
            print(f"{coin:<6} {symbol:<12} -> {count} mum")
        except Exception as exc:
            print(f"{coin:<6} {symbol:<12} -> HATA: {exc}")


if __name__ == "__main__":
    main()
