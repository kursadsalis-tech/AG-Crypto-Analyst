"""Locked out-of-sample validation for AG Crypto Analyst News Intelligence.

Purpose:
- Keep the previously used 2026-05-12 onward development benchmark untouched.
- Use the older historical period as a truly separate experiment.
- Train policy only on outcomes completed before VALIDATION_START.
- Freeze the selected policy for the entire validation window.
- Never learn from validation outcomes while evaluating validation.

Default split for the current ~270-day archive:
Training   : before 2026-02-11
Validation : 2026-02-11 through 2026-05-11
Development benchmark remains 2026-05-12 onward and is not used here.
"""

from __future__ import annotations

import csv
import os
import sqlite3
from database.runtime_store import connect as runtime_connect
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from analysis.walk_forward_backtest import (
    DATASET,
    HORIZONS,
    apply_policy,
    evaluate_row,
    load_rows,
    parse_time,
    select_policy,
)
from data.historical_market_data import get_spot_symbols

DB_NAME = "crypto_data.db"
VALIDATION_START = parse_time(os.getenv("AGCA_VALIDATION_START", "2026-02-11T00:00:00+00:00"))
VALIDATION_END = parse_time(os.getenv("AGCA_VALIDATION_END", "2026-05-12T00:00:00+00:00"))
REPORT_PATH = Path("reports/locked_validation_backtest.csv")


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["horizon"]].append(row)

    for horizon in HORIZONS:
        subset = grouped[horizon]
        if not subset:
            print(f"{horizon}: n=0")
            continue
        hit_rate = sum(r["hit"] for r in subset) / len(subset) * 100.0
        avg_return = sum(r["directional_return"] for r in subset) / len(subset)
        print(
            f"{horizon}: n={len(subset)} | "
            f"hit_rate={hit_rate:.1f}% | directional_return={avg_return:.3f}%"
        )


def write_report(rows):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "news_id", "news_time", "entry_time", "exit_time", "coin", "direction",
        "event_type", "impact", "confidence", "matched_keywords", "horizon",
        "directional_return", "hit", "policy_impact_min",
        "policy_confidence_min", "policy_event_type", "policy_direction",
        "train_n", "train_avg", "train_hit", "policy_score",
    ]
    with REPORT_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    if DATASET != "historical":
        print("HATA: Bu doğrulama yalnızca AGCA_NEWS_DATASET=historical ile çalıştırılmalı.")
        return

    connection = runtime_connect(DB_NAME)
    try:
        symbols = get_spot_symbols()
        raw_rows = load_rows(connection)
        evaluated = []
        for row in raw_rows:
            evaluated.extend(evaluate_row(connection, row, symbols))

        # Training may contain only outcomes fully known before validation begins.
        training_rows = [r for r in evaluated if r["exit_time"] < VALIDATION_START]
        validation_rows = [
            r for r in evaluated
            if VALIDATION_START <= r["entry_time"] < VALIDATION_END
        ]

        print("AG Crypto Analyst - LOCKED Out-of-Sample Validation")
        print(f"Veri seti: {DATASET}")
        print(f"Validation başlangıcı: {VALIDATION_START.isoformat()}")
        print(f"Validation bitişi     : {VALIDATION_END.isoformat()}")
        print("Kural: Politikalar validation başlamadan önce seçilir ve tüm validation boyunca dondurulur.")
        print("Validation sonuçları politika seçiminde ASLA kullanılmaz.")
        print(f"Training sonucu: {len(training_rows)}")
        print(f"Validation sonucu: {len(validation_rows)}")
        print()

        policies = {}
        for horizon in HORIZONS:
            policies[horizon] = select_policy(training_rows, horizon)

        output = []
        for horizon in HORIZONS:
            policy = policies[horizon]
            candidates = apply_policy(
                [r for r in validation_rows if r["horizon"] == horizon],
                policy,
            )
            for result in candidates:
                enriched = dict(result)
                enriched.update({
                    "policy_impact_min": policy["impact_min"],
                    "policy_confidence_min": policy["confidence_min"],
                    "policy_event_type": policy["event_type"],
                    "policy_direction": policy["direction_filter"],
                    "train_n": policy["train_n"],
                    "train_avg": policy["train_avg"],
                    "train_hit": policy["train_hit"],
                    "policy_score": policy["score"],
                })
                output.append(enriched)

        print("Kilitli politikalar:")
        for horizon in HORIZONS:
            p = policies[horizon]
            print(
                f"  {horizon}: impact>={p['impact_min']} | confidence>={p['confidence_min']} | "
                f"event={p['event_type']} | direction={p['direction_filter']} | "
                f"train_n={p['train_n']} | train_hit={p['train_hit']*100:.1f}% | "
                f"train_avg={p['train_avg']:.3f}% | score={p['score']:.3f}"
            )

        print()
        summarize(output)
        write_report(output)
        print()
        print("Sonuç dosyası:", REPORT_PATH)
        print("VALIDATION LOCK: test sonuçları seçim veya yeniden eğitim için kullanılmadı.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
