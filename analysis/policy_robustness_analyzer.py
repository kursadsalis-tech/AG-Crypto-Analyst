"""Analyze rolling research lab results for policy stability and robustness.

Reads reports/rolling_research_lab.csv produced locally by rolling_research_lab.
The goal is not to find the single best historical score, but to identify
policies that repeat across cycles with stable frozen-EXAM performance.

No live data is fetched and no model parameters are changed.
"""
from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path

INPUT_PATH = Path("reports/rolling_research_lab.csv")
OUTPUT_PATH = Path("reports/policy_robustness_summary.csv")


def f(row, key):
    try:
        return float(row.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def i(row, key):
    try:
        return int(float(row.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def policy_key(row):
    return (
        row["horizon"],
        i(row, "impact_min"),
        i(row, "confidence_min"),
        row["event_type"],
        row["direction_filter"],
    )


def weighted_avg(rows, value_key, weight_key="exam_n"):
    total = sum(i(r, weight_key) for r in rows)
    if total <= 0:
        return 0.0
    return sum(f(r, value_key) * i(r, weight_key) for r in rows) / total


def stddev(values):
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def robustness_score(cycles, exam_n, exam_hit, exam_avg, hit_std, positive_cycle_ratio):
    # Reward repeatability, sample size, directional accuracy and positive returns;
    # penalize unstable cycle-to-cycle hit rates. Score is comparative, not a
    # probability estimate.
    repeatability = min(cycles / 4.0, 1.0)
    evidence = min(exam_n / 80.0, 1.0)
    hit_edge = max(exam_hit - 0.50, 0.0)
    return_edge = max(exam_avg, 0.0)
    stability = max(1.0 - hit_std / 0.20, 0.0)
    return (
        30.0 * repeatability
        + 20.0 * evidence
        + 25.0 * min(hit_edge / 0.20, 1.0)
        + 10.0 * min(return_edge / 0.50, 1.0)
        + 10.0 * stability
        + 5.0 * positive_cycle_ratio
    )


def grade(score, cycles, exam_n):
    if score >= 80 and cycles >= 3 and exam_n >= 50:
        return "ROBUST_A"
    if score >= 65 and cycles >= 2 and exam_n >= 30:
        return "ROBUST_B"
    if score >= 50:
        return "WATCH"
    return "FRAGILE"


def main():
    if not INPUT_PATH.exists():
        print(f"HATA: {INPUT_PATH} bulunamadı. Önce rolling_research_lab çalıştırılmalı.")
        return

    with INPUT_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    groups = defaultdict(list)
    for row in rows:
        groups[policy_key(row)].append(row)

    summary = []
    for key, group in groups.items():
        horizon, impact_min, confidence_min, event_type, direction = key
        exam_n = sum(i(r, "exam_n") for r in group)
        cycles = len(group)
        exam_hit = weighted_avg(group, "exam_hit")
        exam_avg = weighted_avg(group, "exam_avg")
        challenge_hit = weighted_avg(group, "challenge_hit", "challenge_n")
        learn_hit = weighted_avg(group, "learn_hit", "learn_n")
        cycle_hits = [f(r, "exam_hit") for r in group if i(r, "exam_n") > 0]
        hit_std = stddev(cycle_hits)
        positive_cycles = sum(1 for r in group if i(r, "exam_n") > 0 and f(r, "exam_avg") > 0)
        positive_cycle_ratio = positive_cycles / len(cycle_hits) if cycle_hits else 0.0
        score = robustness_score(cycles, exam_n, exam_hit, exam_avg, hit_std, positive_cycle_ratio)
        summary.append({
            "horizon": horizon,
            "impact_min": impact_min,
            "confidence_min": confidence_min,
            "event_type": event_type,
            "direction_filter": direction,
            "cycles": cycles,
            "exam_n": exam_n,
            "learn_hit": learn_hit,
            "challenge_hit": challenge_hit,
            "exam_hit": exam_hit,
            "exam_avg": exam_avg,
            "exam_hit_std": hit_std,
            "positive_cycle_ratio": positive_cycle_ratio,
            "robustness_score": score,
            "grade": grade(score, cycles, exam_n),
        })

    summary.sort(key=lambda r: (r["horizon"], -r["robustness_score"], -r["exam_n"]))
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields = list(summary[0].keys()) if summary else []
    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary)

    print("AG Crypto Analyst - Policy Robustness Analyzer")
    print(f"Kaynak: {INPUT_PATH}")
    print(f"Politika varyantı: {len(summary)}")
    print("Amaç: tek yüksek skoru değil, cycle'lar arasında tekrar eden frozen-EXAM avantajını bulmak.")
    print()

    for horizon in ("1h", "4h", "24h"):
        subset = [r for r in summary if r["horizon"] == horizon]
        print(f"{horizon} en dayanıklı politikalar:")
        for r in subset[:5]:
            print(
                f"  {r['grade']} | score={r['robustness_score']:.1f} | cycles={r['cycles']} | "
                f"exam_n={r['exam_n']} | exam_hit={r['exam_hit']*100:.1f}% | "
                f"exam_avg={r['exam_avg']:.3f}% | std={r['exam_hit_std']*100:.1f}pp | "
                f"impact>={r['impact_min']} confidence>={r['confidence_min']} | "
                f"event={r['event_type']} | dir={r['direction_filter']}"
            )
        print()

    print("Sonuç dosyası:", OUTPUT_PATH)
    print("Not: robustness_score karşılaştırma puanıdır; başarı olasılığı değildir.")


if __name__ == "__main__":
    main()
