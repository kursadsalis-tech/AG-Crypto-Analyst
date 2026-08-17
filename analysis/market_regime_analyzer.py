"""Market-regime analysis for the frozen robust 1h News Intelligence policy.

This is a diagnostic module, not a policy optimizer. It keeps the previously
selected 1h policy frozen and asks a new question: in which BTC market regimes
has that policy historically worked or failed?

Frozen policy:
    impact>=0, confidence>=70, event=ALL, direction=negative

Regime features are computed only from BTC 1h candles available at or before
the signal entry time, so future prices cannot leak into regime classification.
"""
from __future__ import annotations

import csv
import sqlite3
from database.runtime_store import connect as runtime_connect
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from analysis.walk_forward_backtest import DATASET, evaluate_row, load_rows, policy_subset, parse_time
from data.historical_market_data import get_spot_symbols

DB_NAME = "crypto_data.db"
REPORT_PATH = Path("reports/market_regime_analysis.csv")
POLICY = {"impact_min": 0, "confidence_min": 70, "event_type": "ALL", "direction_filter": "negative"}
MIN_REGIME_N = 10


def btc_closes_before(connection, entry_time, hours=72):
    start = entry_time - timedelta(hours=hours + 2)
    rows = connection.execute(
        """SELECT open_time, close FROM market_klines
           WHERE symbol='BTCUSDT' AND interval='1h' AND open_time>=? AND open_time<=?
           ORDER BY open_time ASC""",
        (start.isoformat(), entry_time.isoformat()),
    ).fetchall()
    return [(parse_time(t), float(c)) for t, c in rows]


def pct_change(current, previous):
    return (current / previous - 1.0) * 100.0 if previous else 0.0


def classify_regime(connection, entry_time):
    series = btc_closes_before(connection, entry_time, 72)
    if len(series) < 25:
        return None
    closes = [c for _, c in series]
    last = closes[-1]
    ret24 = pct_change(last, closes[-25])
    ret72 = pct_change(last, closes[0]) if len(closes) >= 70 else ret24

    hourly_returns = [pct_change(closes[i], closes[i - 1]) for i in range(1, len(closes))]
    recent24 = hourly_returns[-24:]
    vol24 = (sum(x * x for x in recent24) / len(recent24)) ** 0.5 if recent24 else 0.0

    if ret24 >= 2.0:
        trend = "STRONG_UP"
    elif ret24 >= 0.5:
        trend = "UP"
    elif ret24 <= -2.0:
        trend = "STRONG_DOWN"
    elif ret24 <= -0.5:
        trend = "DOWN"
    else:
        trend = "SIDEWAYS"

    if vol24 >= 1.0:
        volatility = "HIGH_VOL"
    elif vol24 >= 0.5:
        volatility = "MID_VOL"
    else:
        volatility = "LOW_VOL"

    if ret24 > 0.5 and ret72 > 0:
        alignment = "BULL_ALIGNED"
    elif ret24 < -0.5 and ret72 < 0:
        alignment = "BEAR_ALIGNED"
    else:
        alignment = "MIXED"

    return {
        "trend": trend,
        "volatility": volatility,
        "alignment": alignment,
        "ret24": ret24,
        "ret72": ret72,
        "vol24": vol24,
    }


def metrics(rows):
    n = len(rows)
    if not n:
        return 0, 0.0, 0.0
    return n, sum(r["hit"] for r in rows) / n, sum(r["directional_return"] for r in rows) / n


def print_group(title, grouped):
    print(title)
    ranked = []
    for key, rows in grouped.items():
        n, hit, avg = metrics(rows)
        ranked.append((n, hit, avg, key))
    for n, hit, avg, key in sorted(ranked, key=lambda x: (-x[0], -x[1])):
        flag = "STABLE" if n >= MIN_REGIME_N else "SMALL_N"
        print(f"  {key:24s} | n={n:3d} | hit={hit*100:5.1f}% | avg={avg:+.3f}% | {flag}")
    print()


def main():
    if DATASET != "historical":
        print("HATA: AGCA_NEWS_DATASET=historical ile çalıştırılmalı.")
        return

    connection = runtime_connect(DB_NAME)
    try:
        symbols = get_spot_symbols()
        evaluated = []
        for row in load_rows(connection):
            evaluated.extend(evaluate_row(connection, row, symbols))

        base = [r for r in evaluated if r["horizon"] == "1h"]
        selected = policy_subset(base, POLICY["impact_min"], POLICY["confidence_min"], POLICY["event_type"], POLICY["direction_filter"])

        enriched = []
        cache = {}
        for row in selected:
            key = row["entry_time"]
            if key not in cache:
                cache[key] = classify_regime(connection, key)
            regime = cache[key]
            if not regime:
                continue
            item = dict(row)
            item.update(regime)
            enriched.append(item)

        print("AG Crypto Analyst - Market Regime Analyzer")
        print("Frozen 1h policy: impact>=0 | confidence>=70 | event=ALL | direction=negative")
        print("Kural: rejim yalnızca sinyal anına kadar bilinen BTC fiyatlarından hesaplanır.")
        print("Amaç: politika seçmek değil; 1h avantajının hangi piyasa koşullarında güçlenip zayıfladığını bulmak.")
        print()

        n, hit, avg = metrics(enriched)
        print(f"TOPLAM: n={n} | hit_rate={hit*100:.1f}% | directional_return={avg:+.3f}%")
        print()

        by_trend = defaultdict(list)
        by_vol = defaultdict(list)
        by_align = defaultdict(list)
        by_combo = defaultdict(list)
        for row in enriched:
            by_trend[row["trend"]].append(row)
            by_vol[row["volatility"]].append(row)
            by_align[row["alignment"]].append(row)
            by_combo[f"{row['trend']} + {row['volatility']}"] .append(row)

        print_group("BTC 24h trend rejimi:", by_trend)
        print_group("BTC 24h volatilite rejimi:", by_vol)
        print_group("BTC 24h/72h yön uyumu:", by_align)
        print_group("Trend + volatilite kombinasyonu:", by_combo)

        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        fields = ["news_id", "entry_time", "coin", "direction", "event_type", "impact", "confidence", "directional_return", "hit", "trend", "volatility", "alignment", "ret24", "ret72", "vol24"]
        with REPORT_PATH.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in enriched:
                writer.writerow({k: row[k] for k in fields})

        print("Sonuç dosyası:", REPORT_PATH)
        print("Not: Bu çıktı keşif/teşhis içindir. Rejim filtresi ancak ayrı unseen validation sonrası üretim kuralına dönüşebilir.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
