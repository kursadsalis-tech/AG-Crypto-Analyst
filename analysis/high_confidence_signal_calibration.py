"""Leakage-safe calibration of high-confidence tiers for the robust 1h policy.

Base policy is kept fixed:
  impact>=0, confidence>=70, event=ALL, direction=negative

Goal: test whether stricter confidence/impact/event slices can define A/A+/A++
alerts that improve unseen hit rate without collapsing sample size.

Flow per rolling cycle:
  LEARN -> rank candidate tiers -> CHALLENGE -> freeze winner -> EXAM -> roll
Challenge/exam outcomes never participate in their own candidate selection.
"""
from __future__ import annotations

import csv
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

from analysis.walk_forward_backtest import DATASET, evaluate_row, load_rows, policy_subset
from data.historical_market_data import get_spot_symbols

DB_NAME = "crypto_data.db"
REPORT_PATH = Path("reports/high_confidence_signal_calibration.csv")
LEARN_DAYS = int(os.getenv("AGCA_CAL_LEARN_DAYS", "120"))
CHALLENGE_DAYS = int(os.getenv("AGCA_CAL_CHALLENGE_DAYS", "30"))
EXAM_DAYS = int(os.getenv("AGCA_CAL_EXAM_DAYS", "30"))
STEP_DAYS = int(os.getenv("AGCA_CAL_STEP_DAYS", "30"))
MIN_LEARN_N = int(os.getenv("AGCA_CAL_MIN_LEARN_N", "15"))
MIN_CHALLENGE_N = int(os.getenv("AGCA_CAL_MIN_CHALLENGE_N", "5"))
BASE = {"impact": 0, "confidence": 70, "event": "ALL"}
CONFIDENCES = (70, 80, 90)
IMPACTS = (0, 35, 50, 65)


def metrics(rows):
    n = len(rows)
    if not n:
        return {"n": 0, "hit": 0.0, "avg": 0.0, "score": -999.0}
    hit = sum(r["hit"] for r in rows) / n
    avg = sum(r["directional_return"] for r in rows) / n
    reliability = n / (n + 25.0)
    # Hit rate is primary for alert calibration; return is a useful tie-breaker.
    score = ((hit - 0.5) * 100.0 + avg * 12.0) * reliability
    return {"n": n, "hit": hit, "avg": avg, "score": score}


def apply(rows, p):
    return policy_subset(rows, p["impact"], p["confidence"], p["event"], "negative")


def key(p):
    return (p["impact"], p["confidence"], p["event"])


def label(p):
    return f"impact>={p['impact']}|confidence>={p['confidence']}|event={p['event']}"


def candidates(learn):
    events = ["ALL"] + sorted({r["event_type"] for r in learn})
    out = []
    for impact in IMPACTS:
        for confidence in CONFIDENCES:
            for event in events:
                p = {"impact": impact, "confidence": confidence, "event": event}
                m = metrics(apply(learn, p))
                if m["n"] >= MIN_LEARN_N:
                    out.append((m["score"], m["hit"], m["n"], p, m))
    out.sort(reverse=True, key=lambda x: (x[0], x[1], x[2]))
    return out


def tier(hit, n):
    if n >= 20 and hit >= .90:
        return "A++"
    if n >= 20 and hit >= .85:
        return "A+"
    if n >= 15 and hit >= .75:
        return "A"
    return "B"


def main():
    if DATASET != "historical":
        print("HATA: AGCA_NEWS_DATASET=historical ile çalıştırılmalı.")
        return
    con = sqlite3.connect(DB_NAME)
    try:
        symbols = get_spot_symbols()
        evaluated = []
        for row in load_rows(con):
            evaluated.extend(evaluate_row(con, row, symbols))
        rows = [r for r in evaluated if r["horizon"] == "1h"]
        rows = policy_subset(rows, 0, 70, "ALL", "negative")
        if not rows:
            print("Kalibrasyon için veri yok.")
            return

        start = min(r["entry_time"] for r in rows)
        end = max(r["exit_time"] for r in rows)
        champion = dict(BASE)
        report = []
        cycle = 0
        cursor = start

        print("AG Crypto Analyst - Leakage-Safe High-Confidence 1h Calibration")
        print("Base: impact>=0 | confidence>=70 | event=ALL | direction=negative")
        print(f"Pencereler: LEARN={LEARN_DAYS}g | CHALLENGE={CHALLENGE_DAYS}g | EXAM={EXAM_DAYS}g | STEP={STEP_DAYS}g")
        print("Amaç: A/A+/A++ sinyallerini unseen EXAM performansıyla kalibre etmek; küçük örneklem zirvelerini reddetmek.")
        print()

        while True:
            learn_end = cursor + timedelta(days=LEARN_DAYS)
            challenge_end = learn_end + timedelta(days=CHALLENGE_DAYS)
            exam_end = challenge_end + timedelta(days=EXAM_DAYS)
            if exam_end > end:
                break
            cycle += 1
            learn = [r for r in rows if cursor <= r["exit_time"] < learn_end]
            challenge = [r for r in rows if learn_end <= r["entry_time"] < challenge_end]
            exam = [r for r in rows if challenge_end <= r["entry_time"] < exam_end]

            ranked = candidates(learn)
            challenger = ranked[0][3] if ranked else dict(BASE)
            challenger_learn = ranked[0][4] if ranked else metrics(apply(learn, challenger))
            champ_ch = metrics(apply(challenge, champion))
            challenger_ch = metrics(apply(challenge, challenger))

            if key(challenger) == key(champion):
                decision = "KEEP_CHAMPION"
                winner = champion
                ch_m = champ_ch
            elif (challenger_ch["n"] >= MIN_CHALLENGE_N and challenger_ch["hit"] > champ_ch["hit"]
                  and challenger_ch["score"] > champ_ch["score"]):
                decision = "PROMOTE_CHALLENGER"
                winner = challenger
                ch_m = challenger_ch
            else:
                decision = "REJECT_CHALLENGER"
                winner = champion
                ch_m = champ_ch

            champion = dict(winner)
            ex = metrics(apply(exam, champion))
            base_ex = metrics(apply(exam, BASE))
            ex_tier = tier(ex["hit"], ex["n"])

            print(f"Cycle {cycle}: {cursor.date()} -> {exam_end.date()}")
            print(f"  challenger={label(challenger)} | learn n={challenger_learn['n']} hit={challenger_learn['hit']*100:.1f}% avg={challenger_learn['avg']:+.3f}%")
            print(f"  {decision} -> champion={label(champion)}")
            print(f"  challenge n={ch_m['n']} hit={ch_m['hit']*100:.1f}% avg={ch_m['avg']:+.3f}%")
            print(f"  FROZEN EXAM [{ex_tier}]: calibrated n={ex['n']} hit={ex['hit']*100:.1f}% avg={ex['avg']:+.3f}% | base n={base_ex['n']} hit={base_ex['hit']*100:.1f}% avg={base_ex['avg']:+.3f}%")
            print()

            report.append({
                "cycle": cycle, "learn_start": cursor.isoformat(), "exam_end": exam_end.isoformat(),
                "decision": decision, "challenger": label(challenger), "champion": label(champion),
                "challenge_n": ch_m["n"], "challenge_hit": ch_m["hit"], "challenge_avg": ch_m["avg"],
                "exam_tier": ex_tier, "exam_n": ex["n"], "exam_hit": ex["hit"], "exam_avg": ex["avg"],
                "base_exam_n": base_ex["n"], "base_exam_hit": base_ex["hit"], "base_exam_avg": base_ex["avg"],
            })
            cursor += timedelta(days=STEP_DAYS)

        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        if report:
            with REPORT_PATH.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(report[0]))
                writer.writeheader(); writer.writerows(report)

        n = sum(r["exam_n"] for r in report)
        bn = sum(r["base_exam_n"] for r in report)
        hit = sum(r["exam_hit"] * r["exam_n"] for r in report) / n if n else 0
        avg = sum(r["exam_avg"] * r["exam_n"] for r in report) / n if n else 0
        bh = sum(r["base_exam_hit"] * r["base_exam_n"] for r in report) / bn if bn else 0
        ba = sum(r["base_exam_avg"] * r["base_exam_n"] for r in report) / bn if bn else 0
        print(f"Tamamlanan cycle: {cycle}")
        print("Toplam FROZEN EXAM:")
        print(f"  CALIBRATED : n={n} | hit_rate={hit*100:.1f}% | directional_return={avg:+.3f}%")
        print(f"  BASE POLICY: n={bn} | hit_rate={bh*100:.1f}% | directional_return={ba:+.3f}%")
        print("Sonuç dosyası:", REPORT_PATH)
        print("Not: yüksek tier ancak unseen EXAM'da tekrar ederse alarm güven katmanına adaydır.")
    finally:
        con.close()


if __name__ == "__main__":
    main()
