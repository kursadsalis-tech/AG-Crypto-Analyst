"""Leakage-safe regime-aware rolling validation for the frozen 1h news policy.

The base policy is fixed from prior research:
    impact>=0, confidence>=70, event=ALL, direction=negative

Each rolling cycle:
1. LEARN: rank BTC regimes using only completed historical outcomes.
2. CHALLENGE: compare the learned regime filter with the current champion.
3. PROMOTE/REJECT: challenger must beat champion on unseen challenge data.
4. EXAM: evaluate the frozen winner on a second unseen block.
5. ROLL: advance and repeat.

Regime labels use only BTC candles available at or before each signal entry.
No challenge/exam outcome is used to choose its own filter.
"""
from __future__ import annotations

import csv
import os
import sqlite3
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from analysis.market_regime_analyzer import classify_regime
from analysis.walk_forward_backtest import DATASET, evaluate_row, load_rows, policy_subset
from data.historical_market_data import get_spot_symbols

DB_NAME = "crypto_data.db"
REPORT_PATH = Path("reports/regime_aware_rolling_validation.csv")
LEARN_DAYS = int(os.getenv("AGCA_REGIME_LEARN_DAYS", "120"))
CHALLENGE_DAYS = int(os.getenv("AGCA_REGIME_CHALLENGE_DAYS", "30"))
EXAM_DAYS = int(os.getenv("AGCA_REGIME_EXAM_DAYS", "30"))
STEP_DAYS = int(os.getenv("AGCA_REGIME_STEP_DAYS", "30"))
MIN_LEARN_N = int(os.getenv("AGCA_REGIME_MIN_LEARN_N", "12"))
MIN_CHALLENGE_N = int(os.getenv("AGCA_REGIME_MIN_CHALLENGE_N", "5"))
BASE_POLICY = {"impact_min": 0, "confidence_min": 70, "event_type": "ALL", "direction_filter": "negative"}


def metrics(rows):
    n = len(rows)
    if not n:
        return {"n": 0, "hit": 0.0, "avg": 0.0, "score": -999.0}
    hit = sum(r["hit"] for r in rows) / n
    avg = sum(r["directional_return"] for r in rows) / n
    reliability = n / (n + 20.0)
    return {"n": n, "hit": hit, "avg": avg, "score": avg * reliability}


def filter_rows(rows, allowed):
    if allowed is None:
        return list(rows)
    return [r for r in rows if r["regime_key"] in allowed]


def learn_filter(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["regime_key"]].append(row)

    ranked = []
    for key, subset in grouped.items():
        m = metrics(subset)
        if m["n"] < MIN_LEARN_N:
            continue
        # Reward hit + return while shrinking small samples.
        reliability = m["n"] / (m["n"] + 20.0)
        quality = ((m["hit"] - 0.5) * 100.0 + m["avg"] * 20.0) * reliability
        ranked.append((quality, key, m))

    positive = [x for x in ranked if x[0] > 0 and x[2]["hit"] > 0.5 and x[2]["avg"] > 0]
    positive.sort(reverse=True, key=lambda x: x[0])
    if not positive:
        return None, []

    # Keep up to three independently supported regimes. This is deliberately
    # coarse to reduce the temptation to fit tiny regime niches.
    chosen = frozenset(x[1] for x in positive[:3])
    return chosen, positive[:3]


def describe_filter(value):
    if value is None:
        return "ALL_REGIMES"
    return ";".join(sorted(value))


def same_filter(a, b):
    return a == b


def main():
    if DATASET != "historical":
        print("HATA: AGCA_NEWS_DATASET=historical ile çalıştırılmalı.")
        return

    connection = sqlite3.connect(DB_NAME)
    try:
        symbols = get_spot_symbols()
        evaluated = []
        for row in load_rows(connection):
            evaluated.extend(evaluate_row(connection, row, symbols))

        base = [r for r in evaluated if r["horizon"] == "1h"]
        base = policy_subset(base, BASE_POLICY["impact_min"], BASE_POLICY["confidence_min"], BASE_POLICY["event_type"], BASE_POLICY["direction_filter"])

        cache = {}
        rows = []
        for row in base:
            t = row["entry_time"]
            if t not in cache:
                cache[t] = classify_regime(connection, t)
            regime = cache[t]
            if not regime:
                continue
            item = dict(row)
            item["regime_key"] = f"{regime['trend']}+{regime['volatility']}"
            rows.append(item)

        if not rows:
            print("Regime rolling validation: değerlendirilebilir veri yok.")
            return

        start = min(r["entry_time"] for r in rows)
        end = max(r["exit_time"] for r in rows)
        learn_delta = timedelta(days=LEARN_DAYS)
        challenge_delta = timedelta(days=CHALLENGE_DAYS)
        exam_delta = timedelta(days=EXAM_DAYS)
        step_delta = timedelta(days=STEP_DAYS)

        print("AG Crypto Analyst - Leakage-Safe Regime-Aware Rolling Validation")
        print("Base 1h policy: impact>=0 | confidence>=70 | event=ALL | direction=negative")
        print(f"Pencereler: LEARN={LEARN_DAYS}g | CHALLENGE={CHALLENGE_DAYS}g | EXAM={EXAM_DAYS}g | STEP={STEP_DAYS}g")
        print("Akış: geçmişten rejim öğren -> unseen challenge -> promote/reject -> frozen exam -> roll")
        print("Leakage: rejim etiketi sinyal anındaki BTC verisiyle; challenge/exam sonucu seçimden önce görünmez.")
        print()

        champion = None  # None means base policy active in all regimes.
        report = []
        cycle = 0
        learn_start = start

        while True:
            learn_end = learn_start + learn_delta
            challenge_end = learn_end + challenge_delta
            exam_end = challenge_end + exam_delta
            if exam_end > end:
                break
            cycle += 1

            learn = [r for r in rows if learn_start <= r["exit_time"] < learn_end]
            challenge = [r for r in rows if learn_end <= r["entry_time"] < challenge_end]
            exam = [r for r in rows if challenge_end <= r["entry_time"] < exam_end]

            challenger, learned = learn_filter(learn)
            champ_m = metrics(filter_rows(challenge, champion))
            challenger_m = metrics(filter_rows(challenge, challenger))

            if same_filter(champion, challenger):
                decision = "KEEP_CHAMPION"
                winner = champion
                challenge_m = champ_m
            elif challenger_m["n"] >= MIN_CHALLENGE_N and challenger_m["score"] > champ_m["score"] and challenger_m["hit"] >= champ_m["hit"]:
                decision = "PROMOTE_CHALLENGER"
                winner = challenger
                challenge_m = challenger_m
            else:
                decision = "REJECT_CHALLENGER"
                winner = champion
                challenge_m = champ_m

            champion = winner
            exam_m = metrics(filter_rows(exam, champion))
            base_exam_m = metrics(exam)

            print(f"Cycle {cycle}: {learn_start.date()} -> {exam_end.date()}")
            print(f"  learned={describe_filter(challenger)}")
            print(f"  {decision} -> champion={describe_filter(champion)}")
            print(f"  challenge: n={challenge_m['n']} hit={challenge_m['hit']*100:.1f}% avg={challenge_m['avg']:+.3f}%")
            print(f"  FROZEN EXAM: filtered n={exam_m['n']} hit={exam_m['hit']*100:.1f}% avg={exam_m['avg']:+.3f}% | base n={base_exam_m['n']} hit={base_exam_m['hit']*100:.1f}% avg={base_exam_m['avg']:+.3f}%")
            if learned:
                print("  learn top regimes: " + " | ".join(f"{k} n={m['n']} hit={m['hit']*100:.1f}% avg={m['avg']:+.3f}%" for _, k, m in learned))
            print()

            report.append({
                "cycle": cycle,
                "learn_start": learn_start.isoformat(),
                "learn_end": learn_end.isoformat(),
                "challenge_end": challenge_end.isoformat(),
                "exam_end": exam_end.isoformat(),
                "decision": decision,
                "challenger": describe_filter(challenger),
                "champion": describe_filter(champion),
                "challenge_n": challenge_m["n"],
                "challenge_hit": challenge_m["hit"],
                "challenge_avg": challenge_m["avg"],
                "exam_n": exam_m["n"],
                "exam_hit": exam_m["hit"],
                "exam_avg": exam_m["avg"],
                "base_exam_n": base_exam_m["n"],
                "base_exam_hit": base_exam_m["hit"],
                "base_exam_avg": base_exam_m["avg"],
            })
            learn_start += step_delta

        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        fields = list(report[0].keys()) if report else []
        if fields:
            with REPORT_PATH.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(report)

        total_n = sum(r["exam_n"] for r in report)
        base_n = sum(r["base_exam_n"] for r in report)
        filtered_hit = sum(r["exam_hit"] * r["exam_n"] for r in report) / total_n if total_n else 0.0
        filtered_avg = sum(r["exam_avg"] * r["exam_n"] for r in report) / total_n if total_n else 0.0
        base_hit = sum(r["base_exam_hit"] * r["base_exam_n"] for r in report) / base_n if base_n else 0.0
        base_avg = sum(r["base_exam_avg"] * r["base_exam_n"] for r in report) / base_n if base_n else 0.0

        print(f"Tamamlanan cycle: {cycle}")
        print("Toplam FROZEN EXAM karşılaştırması:")
        print(f"  REGIME-AWARE: n={total_n} | hit_rate={filtered_hit*100:.1f}% | directional_return={filtered_avg:+.3f}%")
        print(f"  BASE POLICY : n={base_n} | hit_rate={base_hit*100:.1f}% | directional_return={base_avg:+.3f}%")
        print("Sonuç dosyası:", REPORT_PATH)
        print("Not: ancak frozen EXAM üstünlüğü tekrar ederse rejim filtresi üretim adayı sayılır.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
