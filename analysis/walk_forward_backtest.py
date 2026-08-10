"""Walk-forward evaluation for News Intelligence.

The test for each period uses only news/analyzer observations available before
that period. Price outcomes are used only after the signal timestamp. This is
an evaluation framework; it does not overwrite the production classifier.
"""

import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from data.historical_market_data import get_spot_symbols

DB_NAME = "crypto_data.db"
HORIZONS = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "24h": timedelta(hours=24)}
MIN_TRAIN_ROWS = 20


def parse_time(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_rows(connection):
    cursor = connection.cursor()
    cursor.execute("""
        SELECT n.id, n.published_at, n.title, n.description,
               a.coins, a.direction, a.impact_score, a.confidence_score,
               a.matched_keywords
        FROM crypto_news n
        JOIN crypto_news_analysis a ON a.news_id = n.id
        WHERE n.published_at IS NOT NULL
          AND a.coins IS NOT NULL
          AND a.coins != 'MARKET'
          AND a.direction IN ('positive', 'negative')
        ORDER BY n.published_at ASC, n.id ASC
    """)
    return cursor.fetchall()


def get_close(connection, symbol, target_time):
    cursor = connection.cursor()
    cursor.execute("""
        SELECT close_time, close
        FROM market_klines
        WHERE symbol = ? AND interval = '1h' AND close_time >= ?
        ORDER BY close_time ASC LIMIT 1
    """, (symbol, target_time.isoformat()))
    return cursor.fetchone()


def evaluate_row(connection, row, symbols):
    news_id, published_at, title, description, coins, direction, impact, confidence, matched = row
    news_time = parse_time(published_at)
    results = []
    for coin in coins.split(','):
        coin = coin.strip().upper()
        symbol = symbols.get(coin)
        if not symbol:
            continue
        cursor = connection.cursor()
        cursor.execute("""
            SELECT open_time, open FROM market_klines
            WHERE symbol = ? AND interval = '1h' AND open_time >= ?
            ORDER BY open_time LIMIT 1
        """, (symbol, news_time.isoformat()))
        entry = cursor.fetchone()
        if not entry:
            continue
        entry_time = parse_time(entry[0])
        entry_price = float(entry[1])
        for horizon, delta in HORIZONS.items():
            future = get_close(connection, symbol, entry_time + delta)
            if not future:
                continue
            exit_price = float(future[1])
            raw = (exit_price / entry_price - 1.0) * 100.0
            directional = raw if direction == 'positive' else -raw
            results.append({
                'news_id': news_id, 'news_time': news_time, 'coin': coin,
                'direction': direction, 'impact': impact, 'confidence': confidence,
                'matched_keywords': matched or '', 'horizon': horizon,
                'directional_return': directional,
                'hit': int(directional > 0),
            })
    return results


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['horizon']].append(row)
    for horizon in HORIZONS:
        subset = grouped[horizon]
        if not subset:
            continue
        hit = sum(r['hit'] for r in subset) / len(subset) * 100
        avg = sum(r['directional_return'] for r in subset) / len(subset)
        print(f"{horizon}: n={len(subset)} | hit_rate={hit:.1f}% | directional_return={avg:.3f}%")


def main():
    connection = sqlite3.connect(DB_NAME)
    try:
        symbols = get_spot_symbols()
        rows = load_rows(connection)
        if not rows:
            print('Walk-forward: veri yok.')
            return

        evaluated = []
        for row in rows:
            evaluated.extend(evaluate_row(connection, row, symbols))

        # Chronological walk-forward windows. The current v0.5 classifier is
        # intentionally not modified here; this first pass establishes the
        # leakage-safe evaluation dataset and chronological coverage.
        dates = sorted({r['news_time'].date() for r in evaluated})
        print('AG Crypto Analyst - Walk-Forward Backtest')
        print('Rule: each test period is evaluated only after its timestamp.')
        print(f'Available test dates: {len(dates)}')
        print(f'Minimum training rows reserved for future adaptive models: {MIN_TRAIN_ROWS}')
        print()
        summarize(evaluated)
        print()
        print('Not yet auto-tuning production classifier: historical outcomes are evaluation labels only.')
    finally:
        connection.close()


if __name__ == '__main__':
    main()
