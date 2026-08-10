import sqlite3
import time
from datetime import datetime, timezone

import requests


DB_NAME = "crypto_data.db"
KLINES_URL = "https://api.binance.com/api/v3/klines"
EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
INTERVAL = "1h"
LIMIT = 1000


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


def get_spot_symbols():
    response = requests.get(EXCHANGE_INFO_URL, timeout=15)
    response.raise_for_status()
    data = response.json()

    symbols = {}
    for item in data.get("symbols", []):
        if (
            item.get("status") == "TRADING"
            and item.get("isSpotTradingAllowed")
            and item.get("quoteAsset") == "USDT"
        ):
            symbols[item.get("baseAsset", "").upper()] = item.get("symbol")
    return symbols


def resolve_symbols(coins, spot_symbols):
    resolved = {}
    unresolved = []
    for coin in sorted(coins):
        symbol = spot_symbols.get(coin.upper())
        if symbol:
            resolved[coin.upper()] = symbol
        else:
            unresolved.append(coin.upper())
    return resolved, unresolved


def get_analyzed_coins():
    connection = sqlite3.connect(DB_NAME)
    cursor = connection.cursor()
    cursor.execute("""
        SELECT DISTINCT a.coins
        FROM crypto_news_analysis a
        WHERE a.coins IS NOT NULL
          AND a.coins != 'MARKET'
    """)
    rows = cursor.fetchall()
    connection.close()

    coins = set()
    for (value,) in rows:
        for coin in value.split(","):
            coin = coin.strip().upper()
            if coin and coin != "MARKET":
                coins.add(coin)
    return coins


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
            KLINES_URL,
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

    analyzed_coins = get_analyzed_coins()
    try:
        spot_symbols = get_spot_symbols()
    except Exception as exc:
        print(f"HATA: Binance spot sembolleri alınamadı: {exc}")
        return

    resolved, unresolved = resolve_symbols(analyzed_coins, spot_symbols)

    start_ms = to_milliseconds(min_news_time)
    end_ms = to_milliseconds(max_news_time) + 24 * 60 * 60 * 1000

    print("AG Crypto Analyst - Historical Market Data")
    print(f"Haber başlangıcı : {min_news_time}")
    print(f"Backtest sonu    : {max_news_time}")
    print("Ufuk             : +24 saat")
    print(f"Analiz edilen coin : {len(analyzed_coins)}")
    print(f"Eşleşen Binance spot : {len(resolved)}")
    print()

    for coin, symbol in sorted(resolved.items()):
        try:
            count = fetch_symbol_history(symbol, start_ms, end_ms)
            print(f"{coin:<8} {symbol:<12} -> {count} mum")
        except Exception as exc:
            print(f"{coin:<8} {symbol:<12} -> HATA: {exc}")

    if unresolved:
        print()
        print("Binance spot USDT eşleşmesi bulunamayan coinler:")
        print(", ".join(unresolved))


if __name__ == "__main__":
    main()
