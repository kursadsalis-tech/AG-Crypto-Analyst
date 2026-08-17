import os
import sqlite3
from database.runtime_store import connect as runtime_connect
import time
from datetime import datetime, timezone

import requests


DB_NAME = "crypto_data.db"
KLINES_URL = "https://api.binance.com/api/v3/klines"
EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
INTERVAL = "1h"
LIMIT = 1000


def dataset_mode():
    value = os.getenv("AGCA_NEWS_DATASET", "live").strip().lower()
    return "historical" if value == "historical" else "live"


def create_market_klines_table():
    connection = runtime_connect(DB_NAME)
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
            quote_volume REAL,
            UNIQUE(symbol, interval, open_time)
        )
    """)
    columns = {
        row[1]
        for row in cursor.execute("PRAGMA table_info(market_klines)")
    }
    if "quote_volume" not in columns:
        cursor.execute(
            "ALTER TABLE market_klines ADD COLUMN quote_volume REAL"
        )
    connection.commit()
    connection.close()


def get_news_time_range(mode=None):
    mode = mode or dataset_mode()
    connection = runtime_connect(DB_NAME)
    cursor = connection.cursor()
    if mode == "historical":
        cursor.execute("""
            SELECT MIN(published_at), MAX(published_at)
            FROM historical_news_raw
            WHERE published_at IS NOT NULL
        """)
    else:
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


def get_analyzed_coins(mode=None):
    mode = mode or dataset_mode()
    connection = runtime_connect(DB_NAME)
    cursor = connection.cursor()
    if mode == "historical":
        cursor.execute("""
            SELECT DISTINCT coins
            FROM historical_news_analysis
            WHERE coins IS NOT NULL
              AND coins != 'MARKET'
        """)
    else:
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
    # GKG timestamps may be compact YYYYMMDDHHMMSS; live data is ISO-8601.
    if isinstance(value, str) and value.isdigit() and len(value) == 14:
        dt = datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def save_klines(symbol, klines):
    if not klines:
        return
    connection = runtime_connect(DB_NAME)
    cursor = connection.cursor()
    cursor.executemany("""
        INSERT INTO market_klines (
            symbol, interval, open_time, close_time,
            open, high, low, close, volume, quote_volume
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, interval, open_time) DO UPDATE SET
            close_time=excluded.close_time,
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            volume=excluded.volume,
            quote_volume=excluded.quote_volume
    """, [
        (
            symbol,
            INTERVAL,
            datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc).isoformat(),
            datetime.fromtimestamp(row[6] / 1000, tz=timezone.utc).isoformat(),
            float(row[1]), float(row[2]), float(row[3]), float(row[4]),
            float(row[5]), float(row[7]),
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
    mode = dataset_mode()
    min_news_time, max_news_time = get_news_time_range(mode)
    if not min_news_time or not max_news_time:
        table_name = "historical_news_raw" if mode == "historical" else "crypto_news"
        print(f"HATA: {table_name} tablosunda tarihli haber bulunamadı.")
        return

    analyzed_coins = get_analyzed_coins(mode)
    try:
        spot_symbols = get_spot_symbols()
    except Exception as exc:
        print(f"HATA: Binance spot sembolleri alınamadı: {exc}")
        return

    resolved, unresolved = resolve_symbols(analyzed_coins, spot_symbols)

    start_ms = to_milliseconds(min_news_time)
    end_ms = to_milliseconds(max_news_time) + 24 * 60 * 60 * 1000

    print("AG Crypto Analyst - Historical Market Data")
    print(f"Veri seti        : {mode}")
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
