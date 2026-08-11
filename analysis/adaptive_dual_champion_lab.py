"""Adaptive dual-champion laboratory for AG Crypto Analyst.

Goal
----
Choose between the established NEWS BASE and the provisional weighted
REGIME_AWARE multifactor model without leaking future information.

The selector itself is learned only from completed historical outcomes in a
rolling LEARN window. It evaluates recent relative performance of the two
champions and chooses one for CHALLENGE/EXAM. Selection is then frozen for the
entire EXAM block.

Champions:
- NEWS_BASE: robust 1h policy, no multifactor selection.
- MULTIFACTOR: REGIME_AWARE weights, keep=50%, cutoff=0.249.

This module does not optimize the multifactor weights or cutoff. It only tests
whether an adaptive fallback between two already-defined champions improves
out-of-sample stability.
"""
from __future__ import annotations

import csv
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

from analysis.walk_forward_backtest import DATASET, evaluate_row, load_rows, policy_subset
from analysis.weighted_multifactor_champion_lab import WEIGHT_SETS, apply_model, enrich, metrics
from data.historical_market_data import get_spot_symbols

DB_NAME = "crypto_data.db"
REPORT_PATH = Path("reports/adaptive_dual_champion_lab.csv")
LEARN_DAYS = int(os.getenv("AGCA_DUAL_LEARN_DAYS", "120"))
CHALLENGE_DAYS = int(os.getenv("AGCA_DUAL_CHALLENGE_DAYS", "30"))
EXAM_DAYS = int(os.getenv("AGCA_DUAL_EXAM_DAYS", "30"))
STEP_DAYS = int(os.getenv("AGCA_DUAL_STEP_DAYS", "30"))
RECENT_DAYS = int(os.getenv("AGCA_DUAL_RECENT_DAYS", "60"))
MIN_RECENT_N = int(os.getenv("AGCA_DUAL_MIN_RECENT_N", "8"))
MIN_CHALLENGE_N = int(os.getenv("AGCA_DUAL_MIN_CHALLENGE_N", "5"))

REGIME_WEIGHTS = dict(next(weights for name, weights in WEIGHT_SETS if name == "REGIME_AWARE"))
MULTIFACTOR_MODEL = {"name": "REGIME_AWARE", "weights": REGIME_WEIGHTS, "keep_fraction": 0.50, "cutoff": 0.249}


def choose_from_recent(rows, recent_start):
    recent = [r for r in rows if r["exit_time"] >= recent_start]
    base_m = metrics(recent)
    multi_m = metrics(apply_model(recent, MULTIFACTOR_MODEL))

    if multi_m["n"] < MIN_RECENT_N:
        return "NEWS_BASE", base_m, multi_m, "LOW_MULTI_EVIDENCE"
    if base_m["n"] < MIN_RECENT_N:
        return "MULTIFACTOR", base_m, multi_m, "LOW_BASE_EVIDENCE"

    # Favor multifactor only when it clearly improves hit rate and does not
    # sacrifice directional return. Otherwise preserve the stable base policy.
    hit_edge = multi_m["hit"] - base_m["hit"]
    avg_edge = multi_m["avg"] - base_m["avg"]
    if hit_edge >= 0.05 and avg_edge >= -0.02:
        return "MULTIFACTOR", base_m, multi_m, "RECENT_MULTI_EDGE"
    return "NEWS_BASE", base_m, multi_m, "BASE_STABILITY"


def selected_rows(rows, champion):
    return apply_model(rows, MULTIFACTOR_MODEL) if champion == "MULTIFACTOR" else list(rows)


def write_report(rows):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with REPORT_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


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
        base = [r for r in evaluated if r["horizon"] == "1h"]
        base = policy_subset(base, 0, 70, "ALL", "negative")
        rows = enrich(con, symbols, base)
        if not rows:
            print("Adaptive dual champion için veri yok.")
            return

        start, end = min(r["entry_time"] for r in rows), max(r["exit_time"] for r in rows)
        cursor, cycle, report = start, 0, []

        print("AG Crypto Analyst - Adaptive Dual-Champion Lab")
        print("Champions: NEWS_BASE vs REGIME_AWARE multifactor keep=50% cutoff=0.249")
        print(f"Pencereler: LEARN={LEARN_DAYS}g | CHALLENGE={CHALLENGE_DAYS}g | EXAM={EXAM_DAYS}g | STEP={STEP_DAYS}g")
        print(f"Selector: LEARN'in son {RECENT_DAYS} günündeki göreli performans; seçim EXAM boyunca dondurulur.")
        print("Leakage: seçim yalnızca sonucu tamamlanmış geçmiş gözlemlerden yapılır.")
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
            recent_start = learn_end - timedelta(days=RECENT_DAYS)

            learned_choice, recent_base, recent_multi, reason = choose_from_recent(learn, recent_start)
            ch_base = metrics(challenge)
            ch_multi = metrics(apply_model(challenge, MULTIFACTOR_MODEL))

            # Challenge acts as a guardrail: if learned choice has insufficient
            # evidence or loses both hit-rate and score, fall back to the other
            # champion before EXAM. No EXAM outcome is used here.
            chosen_ch = ch_multi if learned_choice == "MULTIFACTOR" else ch_base
            other_ch = ch_base if learned_choice == "MULTIFACTOR" else ch_multi
            final_choice = learned_choice
            decision = "KEEP_LEARNED"
            if chosen_ch["n"] < MIN_CHALLENGE_N:
                final_choice = "NEWS_BASE" if learned_choice == "MULTIFACTOR" else "MULTIFACTOR"
                decision = "FALLBACK_LOW_N"
            elif other_ch["n"] >= MIN_CHALLENGE_N and other_ch["hit"] > chosen_ch["hit"] and other_ch["score"] > chosen_ch["score"]:
                final_choice = "NEWS_BASE" if learned_choice == "MULTIFACTOR" else "MULTIFACTOR"
                decision = "FALLBACK_CHALLENGE_LOSS"

            selected_ch = ch_multi if final_choice == "MULTIFACTOR" else ch_base
            ex_selected = metrics(selected_rows(exam, final_choice))
            ex_base = metrics(exam)
            ex_multi = metrics(apply_model(exam, MULTIFACTOR_MODEL))

            print(f"Cycle {cycle}: {cursor.date()} -> {exam_end.date()}")
            print(f"  LEARN selector={learned_choice} reason={reason} | recent base={recent_base['hit']*100:.1f}% n={recent_base['n']} | recent multi={recent_multi['hit']*100:.1f}% n={recent_multi['n']}")
            print(f"  {decision} -> FROZEN choice={final_choice} | challenge hit={selected_ch['hit']*100:.1f}% n={selected_ch['n']} avg={selected_ch['avg']:+.3f}%")
            print(f"  EXAM selected: n={ex_selected['n']} hit={ex_selected['hit']*100:.1f}% avg={ex_selected['avg']:+.3f}% | base={ex_base['hit']*100:.1f}% n={ex_base['n']} | multi={ex_multi['hit']*100:.1f}% n={ex_multi['n']}")
            print()

            report.append({
                "cycle": cycle,
                "learn_start": cursor.isoformat(),
                "exam_end": exam_end.isoformat(),
                "learned_choice": learned_choice,
                "selector_reason": reason,
                "decision": decision,
                "final_choice": final_choice,
                "recent_base_n": recent_base["n"], "recent_base_hit": recent_base["hit"], "recent_base_avg": recent_base["avg"],
                "recent_multi_n": recent_multi["n"], "recent_multi_hit": recent_multi["hit"], "recent_multi_avg": recent_multi["avg"],
                "challenge_n": selected_ch["n"], "challenge_hit": selected_ch["hit"], "challenge_avg": selected_ch["avg"],
                "exam_n": ex_selected["n"], "exam_hit": ex_selected["hit"], "exam_avg": ex_selected["avg"],
                "base_exam_n": ex_base["n"], "base_exam_hit": ex_base["hit"], "base_exam_avg": ex_base["avg"],
                "multi_exam_n": ex_multi["n"], "multi_exam_hit": ex_multi["hit"], "multi_exam_avg": ex_multi["avg"],
            })
            cursor += timedelta(days=STEP_DAYS)

        write_report(report)
        total_n = sum(r["exam_n"] for r in report)
        base_n = sum(r["base_exam_n"] for r in report)
        multi_n = sum(r["multi_exam_n"] for r in report)
        sel_hit = sum(r["exam_hit"] * r["exam_n"] for r in report) / total_n if total_n else 0.0
        sel_avg = sum(r["exam_avg"] * r["exam_n"] for r in report) / total_n if total_n else 0.0
        base_hit = sum(r["base_exam_hit"] * r["base_exam_n"] for r in report) / base_n if base_n else 0.0
        base_avg = sum(r["base_exam_avg"] * r["base_exam_n"] for r in report) / base_n if base_n else 0.0
        multi_hit = sum(r["multi_exam_hit"] * r["multi_exam_n"] for r in report) / multi_n if multi_n else 0.0
        multi_avg = sum(r["multi_exam_avg"] * r["multi_exam_n"] for r in report) / multi_n if multi_n else 0.0

        print(f"Tamamlanan cycle: {cycle}")
        print("Toplam FROZEN EXAM:")
        print(f"  ADAPTIVE  : n={total_n} | hit_rate={sel_hit*100:.1f}% | directional_return={sel_avg:+.3f}%")
        print(f"  NEWS BASE : n={base_n} | hit_rate={base_hit*100:.1f}% | directional_return={base_avg:+.3f}%")
        print(f"  MULTIFACT : n={multi_n} | hit_rate={multi_hit*100:.1f}% | directional_return={multi_avg:+.3f}%")
        print("Sonuç dosyası:", REPORT_PATH)
        print("Not: adaptif seçim ancak frozen EXAM'da hem doğruluk hem stabilite avantajı gösterirse üretim adayıdır.")
    finally:
        con.close()


if __name__ == "__main__":
    main()
