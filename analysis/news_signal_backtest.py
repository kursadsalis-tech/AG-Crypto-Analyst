import csv
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


DB_NAME = "crypto_data.db"
OUTPUT_FILE = Path("reports/news_signal_backtest.csv")
HORIZONS = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "24h": timedelta(hours=24),
}


def parse_time(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_signal_rows(connection):
    cursor = connection.cursor()
    cursor.execute("""
        SELECT
            n.id,
            n.published_at,
            a.coins,
            a.direction,
            a.impact_score,
            a.confidence_score
        FROM crypto_news n
        JOIN crypto_news_analysis a ON a.news_id = n.id
        WHERE n.published_at IS NOT NULL
          AND a.coins IS NOT NULL
          AND a.coins != 'MARKET'
          AND a.direction IN ('positive', 'negative')
        ORDER BY n.published_at ASC, n.id ASC
    """)
    return cursor.fetchall()


def get_entry_candle(connection, symbol, news_time):
    cursor = connection.cursor()
    cursor.execute("""
        SELECT open_time, open
        FROM market_klines
        WHERE symbol = ?
          AND interval = '1h'
          AND open_time >= ?
        ORDER BY open_time ASC
        LIMIT 1
    """, (symbol, news_time.isoformat()))
    return cursor.fetchone()


def get_future_close(connection, symbol, target_time):
    cursor = connection.cursor()
    cursor.execute("""
        SELECT close_time, close
        FROM market_klines
        WHERE symbol = ?
          AND interval = '1h'
          AND close_time >= ?
        ORDER BY close_time ASC
        LIMIT 1
    """, (symbol, target_time.isoformat()))
    return cursor.fetchone()


def evaluate_signal(connection, news_id, news_time, coin, direction, impact, confidence):
    symbol = f"{coin}USDT"
    entry = get_entry_candle(connection, symbol, news_time)
    if not entry:
        return []

    entry_time = parse_time(entry[0])
    entry_price = float(entry[1])
    results = []

    for horizon_name, horizon in HORIZONS.items():
        target_time = entry_time + horizon
        future = get_future_close(connection, symbol, target_time)
        if not future:
            continue

        exit_price = float(future[1])
        return_pct = ((exit_price / entry_price) - 1.0) * 100.0
        hit = (
            return_pct > 0 if direction == "positive"
            else return_pct < 0
        )

        results.append({
            "news_id": news_id,
            "coin": coin,
            "direction": direction,
            "impact_score": impact,
            "confidence_score": confidence,
            "news_time": news_time.isoformat(),
            "entry_time": entry_time.isoformat(),
            "entry_price": entry_price,
            "horizon": horizon_name,
            "target_time": parse_time(future[0]).isoformat(),
            "exit_price": exit_price,
            "return_pct": round(return_pct, 6),
            "hit": int(hit),
        })

    return results


def save_results(rows):
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "news_id", "coin", "direction", "impact_score", "confidence_score",
        "news_time", "entry_time", "entry_price", "horizon", "target_time",
        "exit_price", "return_pct", "hit"
    ]
    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def print_summary(rows):
    print("AG Crypto Analyst - News Signal Backtest")
    print("Entry rule: first 1h candle OPEN at/after the news timestamp")
    print()

    for horizon in HORIZONS:
        subset = [row for row in rows if row["horizon"] == horizon]
        if not subset:
            print(f"{horizon}: veri yok")
            continue

        hit_rate = sum(row["hit"] for row in subset) / len(subset) * 100
        avg_return = sum(row["return_pct"] for row in subset) / len(subset)
        print(
            f"{horizon}: n={len(subset)} | "
            f"hit_rate={hit_rate:.1f}% | "
            f"avg_return={avg_return:.3f}%"
        )


def main():
    connection = sqlite3.connect(DB_NAME)
    try:
        signal_rows = load_signal_rows(connection)
        results = []

        for news_id, published_at, coins, direction, impact, confidence in signal_rows:
            news_time = parse_time(published_at)
            for coin in coins.split(","):
                coin = coin.strip()
                if coin:
                    results.extend(
                        evaluate_signal(
                            connection,
                            news_id,
                            news_time,
                            coin,
                            direction,
                            impact,
                            confidence,
                        )
                    )

        save_results(results)
        print_summary(results)
        print()
        print(f"Sonuç dosyası: {OUTPUT_FILE}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
