"""Monthly stress test for frozen robust News Intelligence policies.

This module does not optimize parameters. It evaluates three already selected
robust candidate policies month by month over the historical dataset so we can
measure regime stability, weak months, sample scarcity and drawdown-like drops
in hit rate without contaminating policy selection.

Frozen candidates come from policy_robustness_analyzer output:
- 1h: impact>=0, confidence>=70, event=ALL, direction=negative
- 4h: impact>=0, confidence>=0,  event=ALL, direction=negative
- 24h: impact>=0, confidence>=0, event=ALL, direction=negative
"""
from __future__ import annotations

import csv
import sqlite3
from database.runtime_store import connect as runtime_connect
from collections import defaultdict
from pathlib import Path

from analysis.walk_forward_backtest import DATASET, evaluate_row, load_rows, policy_subset
from data.historical_market_data import get_spot_symbols

DB_NAME = "crypto_data.db"
REPORT_PATH = Path("reports/frozen_policy_monthly_stress_test.csv")

POLICIES = {
    "1h": {"impact_min": 0, "confidence_min": 70, "event_type": "ALL", "direction_filter": "negative"},
    "4h": {"impact_min": 0, "confidence_min": 0, "event_type": "ALL", "direction_filter": "negative"},
    "24h": {"impact_min": 0, "confidence_min": 0, "event_type": "ALL", "direction_filter": "negative"},
}


def month_key(dt):
    return dt.strftime("%Y-%m")


def metrics(rows):
    if not rows:
        return {"n": 0, "hit": 0.0, "avg": 0.0}
    n = len(rows)
    return {
        "n": n,
        "hit": sum(r["hit"] for r in rows) / n,
        "avg": sum(r["directional_return"] for r in rows) / n,
    }


def write_report(rows):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "month", "horizon", "n", "hit_rate", "directional_return",
        "impact_min", "confidence_min", "event_type", "direction_filter",
    ]
    with REPORT_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


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

        if not evaluated:
            print("Monthly stress test: değerlendirilebilir veri yok.")
            return

        print("AG Crypto Analyst - Frozen Policy Monthly Stress Test")
        print("Kural: Parametreler sabittir; aylık sonuçlara göre değiştirilmez.")
        print("Amaç: dayanıklılık, zayıf rejimler ve örneklem darlığını görmek.")
        print()

        report = []
        all_months = sorted({month_key(r["entry_time"]) for r in evaluated})

        for horizon, policy in POLICIES.items():
            base = [r for r in evaluated if r["horizon"] == horizon]
            selected = policy_subset(
                base,
                policy["impact_min"],
                policy["confidence_min"],
                policy["event_type"],
                policy["direction_filter"],
            )
            by_month = defaultdict(list)
            for row in selected:
                by_month[month_key(row["entry_time"])].append(row)

            print(
                f"{horizon} frozen policy: impact>={policy['impact_min']} | "
                f"confidence>={policy['confidence_min']} | event={policy['event_type']} | "
                f"direction={policy['direction_filter']}"
            )

            month_metrics = []
            for month in all_months:
                m = metrics(by_month.get(month, []))
                if m["n"] == 0:
                    continue
                month_metrics.append((month, m))
                print(
                    f"  {month}: n={m['n']:3d} | hit_rate={m['hit']*100:5.1f}% | "
                    f"directional_return={m['avg']:+.3f}%"
                )
                report.append({
                    "month": month,
                    "horizon": horizon,
                    "n": m["n"],
                    "hit_rate": m["hit"],
                    "directional_return": m["avg"],
                    "impact_min": policy["impact_min"],
                    "confidence_min": policy["confidence_min"],
                    "event_type": policy["event_type"],
                    "direction_filter": policy["direction_filter"],
                })

            total = metrics(selected)
            profitable_months = sum(1 for _, m in month_metrics if m["avg"] > 0)
            winning_months = sum(1 for _, m in month_metrics if m["hit"] > 0.5)
            if month_metrics:
                worst_hit_month, worst_hit = min(month_metrics, key=lambda x: x[1]["hit"])
                best_hit_month, best_hit = max(month_metrics, key=lambda x: x[1]["hit"])
                print(
                    f"  TOPLAM: n={total['n']} | hit_rate={total['hit']*100:.1f}% | "
                    f"directional_return={total['avg']:+.3f}% | "
                    f">50% ay={winning_months}/{len(month_metrics)} | pozitif getiri ayı={profitable_months}/{len(month_metrics)}"
                )
                print(
                    f"  En zayıf hit ayı: {worst_hit_month} {worst_hit['hit']*100:.1f}% (n={worst_hit['n']}) | "
                    f"En güçlü: {best_hit_month} {best_hit['hit']*100:.1f}% (n={best_hit['n']})"
                )
            print()

        write_report(report)
        print("Sonuç dosyası:", REPORT_PATH)
        print("Not: Bu test parametre seçmez; frozen politikaların zaman içi stabilitesini ölçer.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
