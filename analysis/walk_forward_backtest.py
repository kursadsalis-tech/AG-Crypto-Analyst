"""Leakage-safe expanding walk-forward backtest for News Intelligence.

Set AGCA_NEWS_DATASET=historical to use the isolated historical GDELT tables.
At every decision timestamp, policy selection may use only outcomes whose
exit_time is already known. This prevents 4h/24h future results from leaking
into earlier decisions on the same or following calendar day.
"""

import csv
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from data.historical_market_data import get_spot_symbols

DB_NAME = "crypto_data.db"
DATASET = os.getenv("AGCA_NEWS_DATASET", "live").strip().lower()
REPORT_PATH = Path(
    "reports/walk_forward_backtest_historical.csv"
    if DATASET == "historical"
    else "reports/walk_forward_backtest.csv"
)
HORIZONS = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "24h": timedelta(hours=24)}
MIN_TRAIN_ROWS = 20
IMPACT_THRESHOLDS = (0, 50, 65, 85)
CONFIDENCE_THRESHOLDS = (0, 70, 85)


def parse_time(value):
    value = str(value).strip()
    if value.isdigit() and len(value) >= 14:
        return datetime.strptime(value[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_rows(connection):
    cursor = connection.cursor()
    if DATASET == "historical":
        cursor.execute("""
            SELECT n.id, n.published_at, n.title, '' AS description,
                   a.coins, a.direction, a.event_type, a.impact_score,
                   a.confidence_score, a.matched_keywords
            FROM historical_news_raw n
            JOIN historical_news_analysis a ON a.historical_news_id = n.id
            WHERE n.published_at IS NOT NULL
              AND a.coins IS NOT NULL
              AND a.coins != 'MARKET'
              AND a.direction IN ('positive', 'negative')
            ORDER BY n.published_at ASC, n.id ASC
        """)
    else:
        cursor.execute("""
            SELECT n.id, n.published_at, n.title, n.description,
                   a.coins, a.direction, a.event_type, a.impact_score,
                   a.confidence_score, a.matched_keywords
            FROM crypto_news n
            JOIN crypto_news_analysis a ON a.news_id = n.id
            WHERE n.published_at IS NOT NULL
              AND a.coins IS NOT NULL
              AND a.coins != 'MARKET'
              AND a.direction IN ('positive', 'negative')
            ORDER BY n.published_at ASC, n.id ASC
        """)
    return cursor.fetchall()


def get_entry(connection, symbol, news_time):
    cursor = connection.cursor()
    cursor.execute("""
        SELECT open_time, open
        FROM market_klines
        WHERE symbol = ? AND interval = '1h' AND open_time >= ?
        ORDER BY open_time ASC LIMIT 1
    """, (symbol, news_time.isoformat()))
    return cursor.fetchone()


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
    news_id, published_at, title, description, coins, direction, event_type, impact, confidence, matched = row
    news_time = parse_time(published_at)
    results = []

    for coin in coins.split(','):
        coin = coin.strip().upper()
        symbol = symbols.get(coin)
        if not symbol:
            continue

        entry = get_entry(connection, symbol, news_time)
        if not entry:
            continue
        entry_time = parse_time(entry[0])
        entry_price = float(entry[1])

        for horizon, delta in HORIZONS.items():
            future = get_close(connection, symbol, entry_time + delta)
            if not future:
                continue
            exit_time = parse_time(future[0])
            exit_price = float(future[1])
            raw = (exit_price / entry_price - 1.0) * 100.0
            directional = raw if direction == 'positive' else -raw
            results.append({
                'news_id': news_id,
                'news_time': news_time,
                'entry_time': entry_time,
                'exit_time': exit_time,
                'coin': coin,
                'direction': direction,
                'event_type': event_type,
                'impact': impact,
                'confidence': confidence,
                'matched_keywords': matched or '',
                'horizon': horizon,
                'directional_return': directional,
                'hit': int(directional > 0),
            })
    return results


def select_policy(training_rows, horizon):
    candidates = []
    horizon_rows = [r for r in training_rows if r['horizon'] == horizon]

    if len(horizon_rows) < MIN_TRAIN_ROWS:
        return {'impact_min': 0, 'confidence_min': 0, 'train_n': len(horizon_rows), 'train_avg': 0.0}

    for impact_min in IMPACT_THRESHOLDS:
        for confidence_min in CONFIDENCE_THRESHOLDS:
            subset = [
                r for r in horizon_rows
                if r['impact'] >= impact_min and r['confidence'] >= confidence_min
            ]
            if len(subset) < MIN_TRAIN_ROWS:
                continue
            avg = sum(r['directional_return'] for r in subset) / len(subset)
            hit = sum(r['hit'] for r in subset) / len(subset)
            candidates.append((avg, hit, len(subset), impact_min, confidence_min))

    if not candidates:
        return {'impact_min': 0, 'confidence_min': 0, 'train_n': len(horizon_rows), 'train_avg': 0.0}

    best = max(candidates, key=lambda x: (x[0], x[1], x[2]))
    return {
        'impact_min': best[3],
        'confidence_min': best[4],
        'train_n': best[2],
        'train_avg': best[0],
    }


def apply_policy(rows, policy):
    return [
        r for r in rows
        if r['impact'] >= policy['impact_min']
        and r['confidence'] >= policy['confidence_min']
    ]


def write_report(rows):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        'news_id', 'news_time', 'entry_time', 'exit_time', 'coin', 'direction', 'event_type',
        'impact', 'confidence', 'horizon', 'directional_return', 'hit',
        'policy_impact_min', 'policy_confidence_min', 'train_n', 'train_avg',
    ]
    with REPORT_PATH.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['horizon']].append(row)

    for horizon in HORIZONS:
        subset = grouped[horizon]
        if not subset:
            print(f"{horizon}: n=0")
            continue
        hit = sum(r['hit'] for r in subset) / len(subset) * 100
        avg = sum(r['directional_return'] for r in subset) / len(subset)
        print(f"{horizon}: n={len(subset)} | hit_rate={hit:.1f}% | directional_return={avg:.3f}%")


def main():
    connection = sqlite3.connect(DB_NAME)
    try:
        symbols = get_spot_symbols()
        raw_rows = load_rows(connection)
        if not raw_rows:
            print('Walk-forward: veri yok.')
            return

        evaluated = []
        for row in raw_rows:
            evaluated.extend(evaluate_row(connection, row, symbols))

        if not evaluated:
            print('Walk-forward: fiyat verisiyle eşleşen sonuç yok.')
            return

        # Replay decisions chronologically. Multiple horizons for the same
        # news/coin decision share the same entry timestamp, but each horizon
        # learns only from outcomes completed by that timestamp.
        decision_times = sorted({r['entry_time'] for r in evaluated})
        test_rows = []
        policy_log = []

        print('AG Crypto Analyst - Leakage-Safe Walk-Forward Backtest')
        print(f'Veri seti: {DATASET}')
        print('Kural: Her karar anında yalnızca exit_time <= karar zamanı olan sonuçlardan öğrenilir.')
        print(f'Yönlü haber/fiyat sonucu: {len(evaluated)}')
        print(f'Karar zamanı: {len(decision_times)}')
        print(f'Minimum eğitim örneği: {MIN_TRAIN_ROWS}')
        print()

        for decision_time in decision_times:
            decision_rows = [r for r in evaluated if r['entry_time'] == decision_time]
            completed_history = [r for r in evaluated if r['exit_time'] <= decision_time]

            for horizon in HORIZONS:
                policy = select_policy(completed_history, horizon)
                candidates = apply_policy(
                    [r for r in decision_rows if r['horizon'] == horizon],
                    policy,
                )
                for result in candidates:
                    enriched = dict(result)
                    enriched.update({
                        'policy_impact_min': policy['impact_min'],
                        'policy_confidence_min': policy['confidence_min'],
                        'train_n': policy['train_n'],
                        'train_avg': policy['train_avg'],
                    })
                    test_rows.append(enriched)
                policy_log.append((decision_time, horizon, policy))

        write_report(test_rows)
        summarize(test_rows)
        print()
        print('Sonuç dosyası:', REPORT_PATH)
        print('Leakage kontrolü: eğitimde yalnızca karar anında sonucu tamamlanmış gözlemler kullanıldı.')

        if policy_log:
            print()
            print('Son politika örnekleri:')
            for decision_time, horizon, policy in policy_log[-6:]:
                print(
                    f"  {decision_time.isoformat()} {horizon}: impact>={policy['impact_min']} | "
                    f"confidence>={policy['confidence_min']} | train_n={policy['train_n']} | "
                    f"train_avg={policy['train_avg']:.3f}%"
                )
    finally:
        connection.close()


if __name__ == '__main__':
    main()
