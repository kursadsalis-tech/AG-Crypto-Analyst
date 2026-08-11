"""AG Crypto Analyst rolling research laboratory.

Purpose
-------
Use the existing historical news + 1h market data as a chronological lab:

LEARN -> optimize candidate policies using only completed outcomes
CHALLENGE -> compare challenger against the current champion on unseen data
PROMOTE/REJECT -> freeze the winner
EXAM -> evaluate the frozen winner on a second unseen block
ROLL -> advance the clock and repeat

No challenge or exam outcome is available to the optimizer before its time.
Once the clock rolls forward, previously completed outcomes may naturally enter
future LEARN windows. This is the same information flow available in live use.

Default windows are intentionally conservative for the current ~360-day set:
- LEARN:     120 days
- CHALLENGE: 30 days
- EXAM:      30 days
- STEP:      30 days

The lab does not fetch data and does not modify live tables.
"""
from __future__ import annotations

import csv
import os
import sqlite3
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from analysis.walk_forward_backtest import (
    DATASET,
    HORIZONS,
    IMPACT_THRESHOLDS,
    CONFIDENCE_THRESHOLDS,
    evaluate_row,
    load_rows,
    policy_subset,
)
from data.historical_market_data import get_spot_symbols

DB_NAME = "crypto_data.db"
LEARN_DAYS = int(os.getenv("AGCA_LAB_LEARN_DAYS", "120"))
CHALLENGE_DAYS = int(os.getenv("AGCA_LAB_CHALLENGE_DAYS", "30"))
EXAM_DAYS = int(os.getenv("AGCA_LAB_EXAM_DAYS", "30"))
STEP_DAYS = int(os.getenv("AGCA_LAB_STEP_DAYS", "30"))
MIN_POLICY_ROWS = int(os.getenv("AGCA_LAB_MIN_POLICY_ROWS", "20"))
TOP_K = int(os.getenv("AGCA_LAB_TOP_K", "12"))
REPORT_PATH = Path("reports/rolling_research_lab.csv")


def metrics(rows):
    if not rows:
        return {"n": 0, "hit": 0.0, "avg": 0.0, "score": -999.0}
    n = len(rows)
    hit = sum(r["hit"] for r in rows) / n
    avg = sum(r["directional_return"] for r in rows) / n
    # Reliability shrinkage keeps tiny lucky samples from dominating.
    reliability = n / (n + 30.0)
    score = avg * reliability
    return {"n": n, "hit": hit, "avg": avg, "score": score}


def make_policy(impact_min, confidence_min, event_type, direction_filter, learn_rows):
    subset = policy_subset(learn_rows, impact_min, confidence_min, event_type, direction_filter)
    m = metrics(subset)
    return {
        "impact_min": impact_min,
        "confidence_min": confidence_min,
        "event_type": event_type,
        "direction_filter": direction_filter,
        "learn_n": m["n"],
        "learn_hit": m["hit"],
        "learn_avg": m["avg"],
        "learn_score": m["score"],
    }


def candidate_policies(learn_rows, horizon):
    rows = [r for r in learn_rows if r["horizon"] == horizon]
    if not rows:
        return []
    event_options = ["ALL"] + sorted({r["event_type"] for r in rows})
    directions = ["ALL", "positive", "negative"]
    candidates = []
    for impact_min in IMPACT_THRESHOLDS:
        for confidence_min in CONFIDENCE_THRESHOLDS:
            for event_type in event_options:
                for direction in directions:
                    p = make_policy(impact_min, confidence_min, event_type, direction, rows)
                    if p["learn_n"] >= MIN_POLICY_ROWS:
                        candidates.append(p)
    candidates.sort(key=lambda p: (p["learn_score"], p["learn_hit"], p["learn_n"]), reverse=True)
    return candidates[:TOP_K]


def apply(rows, horizon, policy):
    base = [r for r in rows if r["horizon"] == horizon]
    return policy_subset(base, policy["impact_min"], policy["confidence_min"], policy["event_type"], policy["direction_filter"])


def compare_on_challenge(champion, candidates, challenge_rows, horizon):
    pool = list(candidates)
    if champion is not None:
        pool.append(champion)
    if not pool:
        return champion, "NO_CANDIDATE", None

    scored = []
    for p in pool:
        m = metrics(apply(challenge_rows, horizon, p))
        # Primary: challenge score; secondary: hit rate; tertiary: sample count.
        scored.append((m["score"], m["hit"], m["n"], p, m))
    scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    best = scored[0]
    best_policy, best_m = best[3], best[4]

    if champion is None:
        return best_policy, "PROMOTE_INITIAL", best_m

    champ_m = metrics(apply(challenge_rows, horizon, champion))
    same = (
        champion["impact_min"] == best_policy["impact_min"]
        and champion["confidence_min"] == best_policy["confidence_min"]
        and champion["event_type"] == best_policy["event_type"]
        and champion["direction_filter"] == best_policy["direction_filter"]
    )
    if same:
        return champion, "KEEP_CHAMPION", champ_m

    # Challenger must beat champion on unseen challenge and have enough evidence.
    if best_m["n"] >= 8 and best_m["score"] > champ_m["score"]:
        return best_policy, "PROMOTE_CHALLENGER", best_m
    return champion, "REJECT_CHALLENGER", champ_m


def confidence_tier(policy):
    n = policy.get("learn_n", 0)
    hit = policy.get("learn_hit", 0.0)
    if n >= 30 and hit >= 0.90:
        return "A++"
    if n >= 30 and hit >= 0.80:
        return "A+"
    if n >= 25 and hit >= 0.70:
        return "A"
    return "B"


def write_report(rows):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "cycle", "horizon", "learn_start", "learn_end", "challenge_end", "exam_end",
        "decision", "tier", "impact_min", "confidence_min", "event_type", "direction_filter",
        "learn_n", "learn_hit", "learn_avg", "challenge_n", "challenge_hit", "challenge_avg",
        "exam_n", "exam_hit", "exam_avg",
    ]
    with REPORT_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


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
        if not evaluated:
            print("Rolling lab: değerlendirilebilir veri yok.")
            return

        start = min(r["entry_time"] for r in evaluated)
        end = max(r["exit_time"] for r in evaluated)
        learn_delta = timedelta(days=LEARN_DAYS)
        challenge_delta = timedelta(days=CHALLENGE_DAYS)
        exam_delta = timedelta(days=EXAM_DAYS)
        step_delta = timedelta(days=STEP_DAYS)

        print("AG Crypto Analyst - Rolling Research Laboratory")
        print(f"Veri aralığı : {start.isoformat()} -> {end.isoformat()}")
        print(f"Pencereler   : LEARN={LEARN_DAYS}g | CHALLENGE={CHALLENGE_DAYS}g | EXAM={EXAM_DAYS}g | STEP={STEP_DAYS}g")
        print("Akış         : Learn -> Challenge -> Promote/Reject -> Frozen Exam -> Roll")
        print("Leakage      : challenge/exam sonucu kendi seçiminden önce kullanılamaz")
        print()

        champions = {h: None for h in HORIZONS}
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

            # Only outcomes completed inside LEARN are optimization inputs.
            learn_rows = [r for r in evaluated if learn_start <= r["exit_time"] < learn_end]
            challenge_rows = [r for r in evaluated if learn_end <= r["entry_time"] < challenge_end]
            exam_rows = [r for r in evaluated if challenge_end <= r["entry_time"] < exam_end]

            print(f"Cycle {cycle}: {learn_start.date()} -> {exam_end.date()}")
            for horizon in HORIZONS:
                candidates = candidate_policies(learn_rows, horizon)
                winner, decision, challenge_m = compare_on_challenge(champions[horizon], candidates, challenge_rows, horizon)
                if winner is None:
                    print(f"  {horizon}: aday yok")
                    continue
                champions[horizon] = winner
                challenge_m = challenge_m or metrics(apply(challenge_rows, horizon, winner))
                exam_m = metrics(apply(exam_rows, horizon, winner))
                tier = confidence_tier(winner)
                print(
                    f"  {horizon}: {decision} | {tier} | event={winner['event_type']} | dir={winner['direction_filter']} | "
                    f"challenge={challenge_m['hit']*100:.1f}% n={challenge_m['n']} | "
                    f"EXAM={exam_m['hit']*100:.1f}% n={exam_m['n']} avg={exam_m['avg']:.3f}%"
                )
                report.append({
                    "cycle": cycle, "horizon": horizon,
                    "learn_start": learn_start.isoformat(), "learn_end": learn_end.isoformat(),
                    "challenge_end": challenge_end.isoformat(), "exam_end": exam_end.isoformat(),
                    "decision": decision, "tier": tier,
                    "impact_min": winner["impact_min"], "confidence_min": winner["confidence_min"],
                    "event_type": winner["event_type"], "direction_filter": winner["direction_filter"],
                    "learn_n": winner.get("learn_n", 0), "learn_hit": winner.get("learn_hit", 0.0), "learn_avg": winner.get("learn_avg", 0.0),
                    "challenge_n": challenge_m["n"], "challenge_hit": challenge_m["hit"], "challenge_avg": challenge_m["avg"],
                    "exam_n": exam_m["n"], "exam_hit": exam_m["hit"], "exam_avg": exam_m["avg"],
                })
            print()
            learn_start += step_delta

        write_report(report)
        print(f"Tamamlanan cycle: {cycle}")
        print("Sonuç dosyası:", REPORT_PATH)

        print("\nToplam frozen EXAM özeti:")
        by_h = defaultdict(list)
        for row in report:
            by_h[row["horizon"]].append(row)
        for horizon in HORIZONS:
            rows = by_h[horizon]
            total_n = sum(r["exam_n"] for r in rows)
            if total_n == 0:
                print(f"  {horizon}: n=0")
                continue
            weighted_hit = sum(r["exam_hit"] * r["exam_n"] for r in rows) / total_n
            weighted_avg = sum(r["exam_avg"] * r["exam_n"] for r in rows) / total_n
            print(f"  {horizon}: n={total_n} | hit_rate={weighted_hit*100:.1f}% | directional_return={weighted_avg:.3f}%")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
