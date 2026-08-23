import csv
import sqlite3
from database.runtime_store import connect as runtime_connect
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from data.historical_market_data import get_spot_symbols


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


def get_close_at_or_after(connection, symbol, target_time):
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


def evaluate_signal(connection, news_id, news_time, coin, symbol, direction, impact, confidence):
    entry = get_entry_candle(connection, symbol, news_time)
    if not entry:
        return []

    entry_time = parse_time(entry[0])
    entry_price = float(entry[1])
    entry_delay_minutes = max(0.0, (entry_time - news_time).total_seconds() / 60.0)
    results = []

    for horizon_name, horizon in HORIZONS.items():
        target_time = entry_time + horizon
        future = get_close_at_or_after(connection, symbol, target_time)
        if not future:
            continue

        exit_price = float(future[1])
        return_pct = ((exit_price / entry_price) - 1.0) * 100.0
        directional_return_pct = return_pct if direction == "positive" else -return_pct
        hit = directional_return_pct > 0

        results.append({
            "news_id": news_id,
            "coin": coin,
            "symbol": symbol,
            "direction": direction,
            "impact_score": impact,
            "confidence_score": confidence,
            "news_time": news_time.isoformat(),
            "entry_time": entry_time.isoformat(),
            "entry_delay_minutes": round(entry_delay_minutes, 3),
            "entry_price": entry_price,
            "horizon": horizon_name,
            "target_time": target_time.isoformat(),
            "exit_time": parse_time(future[0]).isoformat(),
            "exit_price": exit_price,
            "return_pct": round(return_pct, 6),
            "directional_return_pct": round(directional_return_pct, 6),
            "hit": int(hit),
        })

    return results


def save_results(rows):
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "news_id", "coin", "symbol", "direction", "impact_score", "confidence_score",
        "news_time", "entry_time", "entry_delay_minutes", "entry_price", "horizon", "target_time",
        "exit_time", "exit_price", "return_pct", "directional_return_pct", "hit"
    ]
    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def print_group(label, rows):
    if not rows:
        return
    hit_rate = sum(row["hit"] for row in rows) / len(rows) * 100
    avg_return = sum(row["return_pct"] for row in rows) / len(rows)
    avg_directional = sum(row["directional_return_pct"] for row in rows) / len(rows)
    print(
        f"  {label:<8} n={len(rows)} | "
        f"hit_rate={hit_rate:.1f}% | "
        f"avg_return={avg_return:.3f}% | "
        f"directional_return={avg_directional:.3f}%"
    )


def print_score_breakdown(rows, field, label):
    bands = ((0, 49), (50, 69), (70, 84), (85, 100))
    print(f"{label} breakdown:")
    for horizon in HORIZONS:
        print(f"  {horizon}:")
        horizon_rows = [row for row in rows if row["horizon"] == horizon]
        for low, high in bands:
            subset = [row for row in horizon_rows if low <= row[field] <= high]
            if not subset:
                continue
            hit_rate = sum(row["hit"] for row in subset) / len(subset) * 100
            avg_return = sum(row["return_pct"] for row in subset) / len(subset)
            avg_directional = sum(row["directional_return_pct"] for row in subset) / len(subset)
            print(
                f"  {low}-{high:<5} n={len(subset)} | "
                f"hit_rate={hit_rate:.1f}% | "
                f"avg_return={avg_return:.3f}% | "
                f"directional_return={avg_directional:.3f}%"
            )


def print_baseline_comparison(rows):
    print("baseline comparison:")
    for horizon in HORIZONS:
        subset = [row for row in rows if row["horizon"] == horizon]
        if not subset:
            continue
        baseline = sum(row["return_pct"] for row in subset) / len(subset)
        signal = sum(row["directional_return_pct"] for row in subset) / len(subset)
        edge = signal - baseline
        print(
            f"  {horizon}: signal_directional={signal:.3f}% | "
            f"always_long={baseline:.3f}% | edge={edge:+.3f}%"
        )


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def print_entry_delay_summary(rows):
    signal_rows = [row for row in rows if row["horizon"] == "1h"]
    delays = [float(row["entry_delay_minutes"]) for row in signal_rows]
    if not delays:
        return
    mean_delay = sum(delays) / len(delays)
    median_delay = percentile(delays, 0.50)
    p90_delay = percentile(delays, 0.90)
    max_delay = max(delays)
    over_30 = sum(delay > 30.0 for delay in delays) / len(delays) * 100.0
    print("entry timing diagnostics:")
    print(
        f"  n={len(delays)} | mean_delay={mean_delay:.1f}m | "
        f"median_delay={median_delay:.1f}m | p90_delay={p90_delay:.1f}m | "
        f"max_delay={max_delay:.1f}m | >30m={over_30:.1f}%"
    )


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["horizon"], row["direction"])].append(row)

    for horizon in HORIZONS:
        subset = [row for row in rows if row["horizon"] == horizon]
        if not subset:
            print(f"{horizon}: veri yok")
            continue

        hit_rate = sum(row["hit"] for row in subset) / len(subset) * 100
        avg_return = sum(row["return_pct"] for row in subset) / len(subset)
        avg_directional = sum(row["directional_return_pct"] for row in subset) / len(subset)
        print(
            f"{horizon}: n={len(subset)} | "
            f"hit_rate={hit_rate:.1f}% | "
            f"avg_return={avg_return:.3f}% | "
            f"directional_return={avg_directional:.3f}%"
        )
        print_group("positive", grouped.get((horizon, "positive"), []))
        print_group("negative", grouped.get((horizon, "negative"), []))


def main():
    connection = runtime_connect(DB_NAME)
    try:
        try:
            spot_symbols = get_spot_symbols()
        except Exception as exc:
            print(f"HATA: Binance spot sembolleri alınamadı: {exc}")
            return

        signal_rows = load_signal_rows(connection)
        results = []
        skipped_coins = set()

        for news_id, published_at, coins, direction, impact, confidence in signal_rows:
            news_time = parse_time(published_at)
            for coin in coins.split(","):
                coin = coin.strip().upper()
                if not coin:
                    continue

                symbol = spot_symbols.get(coin)
                if not symbol:
                    skipped_coins.add(coin)
                    continue

                results.extend(
                    evaluate_signal(
                        connection,
                        news_id,
                        news_time,
                        coin,
                        symbol,
                        direction,
                        impact,
                        confidence,
                    )
                )

        save_results(results)
        print("AG Crypto Analyst - News Signal Backtest")
        print("Symbol rule: Binance active spot USDT symbols resolved dynamically")
        print("Entry rule: first 1h candle OPEN at/after the news timestamp")
        print("Exit rule: first completed 1h candle CLOSE at/after each horizon")
        print("Directional return: positive keeps return; negative reverses return")
        print()
        print_entry_delay_summary(results)
        print()
        summarize(results)
        print()
        print_score_breakdown(results, "impact_score", "impact_score")
        print()
        print_score_breakdown(results, "confidence_score", "confidence_score")
        print()
        print_baseline_comparison(results)
        if skipped_coins:
            print()
            print("Backtest dışında kalan coinler:")
            print(", ".join(sorted(skipped_coins)))
        print()
        print(f"Sonuç dosyası: {OUTPUT_FILE}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
