"""Leakage-safe expanding walk-forward backtest for News Intelligence.

Set AGCA_NEWS_DATASET=historical to use the isolated historical GDELT tables.
At every decision timestamp, policy selection may use only outcomes whose
exit_time is already known. Policies are selected from impact/confidence plus
optional event-type and direction filters. Selection is penalized for tiny
samples to reduce overfitting.
"""

import csv
import math
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from data.historical_market_data import get_spot_symbols

DB_NAME = "crypto_data.db"
DATASET = os.getenv("AGCA_NEWS_DATASET", "live").strip().lower()
REPORT_PATH = Path("reports/walk_forward_backtest_historical.csv" if DATASET == "historical" else "reports/walk_forward_backtest.csv")
HORIZONS = {"1h": timedelta(hours=1), "4h": timedelta(hours=4), "24h": timedelta(hours=24)}
MIN_TRAIN_ROWS = 20
MIN_POLICY_ROWS = 12
IMPACT_THRESHOLDS = (0, 35, 50, 65, 85)
CONFIDENCE_THRESHOLDS = (0, 55, 70, 85)


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
    table_sql = """
        SELECT n.id, n.published_at, n.title, {description},
               a.coins, a.direction, a.event_type, a.impact_score,
               a.confidence_score, a.matched_keywords
        FROM {news_table} n
        JOIN {analysis_table} a ON {join_key} = n.id
        WHERE n.published_at IS NOT NULL AND a.coins IS NOT NULL
          AND a.coins != 'MARKET' AND a.direction IN ('positive', 'negative')
        ORDER BY n.published_at ASC, n.id ASC
    """
    if DATASET == "historical":
        cursor.execute(table_sql.format(description="'' AS description", news_table="historical_news_raw", analysis_table="historical_news_analysis", join_key="a.historical_news_id"))
    else:
        cursor.execute(table_sql.format(description="n.description", news_table="crypto_news", analysis_table="crypto_news_analysis", join_key="a.news_id"))
    return cursor.fetchall()


def get_entry(connection, symbol, news_time):
    return connection.execute("SELECT open_time, open FROM market_klines WHERE symbol=? AND interval='1h' AND open_time>=? ORDER BY open_time ASC LIMIT 1", (symbol, news_time.isoformat())).fetchone()


def get_close(connection, symbol, target_time):
    return connection.execute("SELECT close_time, close FROM market_klines WHERE symbol=? AND interval='1h' AND close_time>=? ORDER BY close_time ASC LIMIT 1", (symbol, target_time.isoformat())).fetchone()


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
        entry_time, entry_price = parse_time(entry[0]), float(entry[1])
        for horizon, delta in HORIZONS.items():
            future = get_close(connection, symbol, entry_time + delta)
            if not future:
                continue
            exit_time, exit_price = parse_time(future[0]), float(future[1])
            raw = (exit_price / entry_price - 1.0) * 100.0
            directional = raw if direction == 'positive' else -raw
            results.append({'news_id': news_id, 'news_time': news_time, 'entry_time': entry_time, 'exit_time': exit_time, 'coin': coin, 'direction': direction, 'event_type': event_type, 'impact': impact, 'confidence': confidence, 'matched_keywords': matched or '', 'horizon': horizon, 'directional_return': directional, 'hit': int(directional > 0)})
    return results


def policy_subset(rows, impact_min, confidence_min, event_type, direction):
    return [r for r in rows if r['impact'] >= impact_min and r['confidence'] >= confidence_min and (event_type == 'ALL' or r['event_type'] == event_type) and (direction == 'ALL' or r['direction'] == direction)]


def select_policy(training_rows, horizon):
    horizon_rows = [r for r in training_rows if r['horizon'] == horizon]
    if len(horizon_rows) < MIN_TRAIN_ROWS:
        return {'impact_min': 0, 'confidence_min': 0, 'event_type': 'ALL', 'direction_filter': 'ALL', 'train_n': len(horizon_rows), 'train_avg': 0.0, 'train_hit': 0.0, 'score': 0.0}

    event_options = ['ALL'] + sorted({r['event_type'] for r in horizon_rows})
    direction_options = ['ALL', 'positive', 'negative']
    candidates = []
    for impact_min in IMPACT_THRESHOLDS:
        for confidence_min in CONFIDENCE_THRESHOLDS:
            for event_type in event_options:
                for direction in direction_options:
                    subset = policy_subset(horizon_rows, impact_min, confidence_min, event_type, direction)
                    if len(subset) < MIN_POLICY_ROWS:
                        continue
                    avg = sum(r['directional_return'] for r in subset) / len(subset)
                    hit = sum(r['hit'] for r in subset) / len(subset)
                    # Shrink small-sample returns toward zero. This keeps the search
                    # adaptive while making a lucky 12-row niche less attractive.
                    reliability = len(subset) / (len(subset) + 30.0)
                    score = avg * reliability
                    candidates.append((score, hit, len(subset), avg, impact_min, confidence_min, event_type, direction))

    if not candidates:
        return {'impact_min': 0, 'confidence_min': 0, 'event_type': 'ALL', 'direction_filter': 'ALL', 'train_n': len(horizon_rows), 'train_avg': 0.0, 'train_hit': 0.0, 'score': 0.0}
    best = max(candidates, key=lambda x: (x[0], x[1], x[2]))
    return {'impact_min': best[4], 'confidence_min': best[5], 'event_type': best[6], 'direction_filter': best[7], 'train_n': best[2], 'train_avg': best[3], 'train_hit': best[1], 'score': best[0]}


def apply_policy(rows, policy):
    return policy_subset(rows, policy['impact_min'], policy['confidence_min'], policy['event_type'], policy['direction_filter'])


def write_report(rows):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields = ['news_id','news_time','entry_time','exit_time','coin','direction','event_type','impact','confidence','matched_keywords','horizon','directional_return','hit','policy_impact_min','policy_confidence_min','policy_event_type','policy_direction','train_n','train_avg','train_hit','policy_score']
    with REPORT_PATH.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows: grouped[row['horizon']].append(row)
    for horizon in HORIZONS:
        subset = grouped[horizon]
        if not subset:
            print(f"{horizon}: n=0"); continue
        hit = sum(r['hit'] for r in subset) / len(subset) * 100
        avg = sum(r['directional_return'] for r in subset) / len(subset)
        print(f"{horizon}: n={len(subset)} | hit_rate={hit:.1f}% | directional_return={avg:.3f}%")


def main():
    connection = sqlite3.connect(DB_NAME)
    try:
        symbols = get_spot_symbols()
        raw_rows = load_rows(connection)
        evaluated = []
        for row in raw_rows: evaluated.extend(evaluate_row(connection, row, symbols))
        if not evaluated:
            print('Walk-forward: fiyat verisiyle eşleşen sonuç yok.'); return

        decision_times = sorted({r['entry_time'] for r in evaluated})
        test_rows, policy_log = [], []
        print('AG Crypto Analyst - Leakage-Safe Adaptive Walk-Forward Backtest')
        print(f'Veri seti: {DATASET}')
        print('Kural: Her karar anında yalnızca exit_time <= karar zamanı olan sonuçlardan öğrenilir.')
        print('Politika araması: impact + confidence + event_type + direction; küçük örneklem cezası aktif.')
        print(f'Yönlü haber/fiyat sonucu: {len(evaluated)}')
        print(f'Karar zamanı: {len(decision_times)}')
        print(f'Minimum eğitim örneği: {MIN_TRAIN_ROWS} | minimum politika örneği: {MIN_POLICY_ROWS}')
        print()

        for decision_time in decision_times:
            decision_rows = [r for r in evaluated if r['entry_time'] == decision_time]
            completed_history = [r for r in evaluated if r['exit_time'] <= decision_time]
            for horizon in HORIZONS:
                policy = select_policy(completed_history, horizon)
                candidates = apply_policy([r for r in decision_rows if r['horizon'] == horizon], policy)
                for result in candidates:
                    enriched = dict(result)
                    enriched.update({'policy_impact_min': policy['impact_min'], 'policy_confidence_min': policy['confidence_min'], 'policy_event_type': policy['event_type'], 'policy_direction': policy['direction_filter'], 'train_n': policy['train_n'], 'train_avg': policy['train_avg'], 'train_hit': policy['train_hit'], 'policy_score': policy['score']})
                    test_rows.append(enriched)
                policy_log.append((decision_time, horizon, policy))

        write_report(test_rows); summarize(test_rows)
        print(); print('Sonuç dosyası:', REPORT_PATH)
        print('Leakage kontrolü: eğitimde yalnızca karar anında sonucu tamamlanmış gözlemler kullanıldı.')
        if policy_log:
            print(); print('Son politika örnekleri:')
            for decision_time, horizon, p in policy_log[-6:]:
                print(f"  {decision_time.isoformat()} {horizon}: impact>={p['impact_min']} | confidence>={p['confidence_min']} | event={p['event_type']} | direction={p['direction_filter']} | train_n={p['train_n']} | train_hit={p['train_hit']*100:.1f}% | train_avg={p['train_avg']:.3f}% | score={p['score']:.3f}")
    finally:
        connection.close()


if __name__ == '__main__':
    main()
