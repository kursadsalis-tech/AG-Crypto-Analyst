"""Robustness and stability diagnostics for the weighted multifactor lab.

Reads reports/weighted_multifactor_champion_lab.csv and answers:
- Did multifactor beat the news base consistently or only in a few cycles?
- How often did the same model family remain Champion?
- How stable were keep fraction and cutoff?
- What were the best/worst frozen EXAM cycles?

This module does not re-optimize models. It only audits already-frozen results.
"""
from __future__ import annotations

import csv
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

SOURCE = Path("reports/weighted_multifactor_champion_lab.csv")
REPORT = Path("reports/multifactor_robustness_summary.csv")


def parse_model(label):
    if label == "BASE_ALL":
        return {"family": "BASE_ALL", "keep": 1.0, "cutoff": None}
    parts = label.split("|")
    family = parts[0]
    keep = None
    cutoff = None
    for p in parts[1:]:
        if p.startswith("keep="):
            keep = float(p.split("=")[1].replace("%", "")) / 100.0
        elif p.startswith("cut="):
            cutoff = float(p.split("=")[1])
    return {"family": family, "keep": keep, "cutoff": cutoff}


def weighted(rows, prefix):
    n = sum(int(float(r[f"{prefix}_n"])) for r in rows)
    if not n:
        return {"n": 0, "hit": 0.0, "avg": 0.0}
    hit = sum(float(r[f"{prefix}_hit"]) * int(float(r[f"{prefix}_n"])) for r in rows) / n
    avg = sum(float(r[f"{prefix}_avg"]) * int(float(r[f"{prefix}_n"])) for r in rows) / n
    return {"n": n, "hit": hit, "avg": avg}


def stdev_pp(values):
    return statistics.pstdev(values) * 100 if len(values) > 1 else 0.0


def main():
    if not SOURCE.exists():
        print(f"HATA: {SOURCE} bulunamadı. Önce weighted multifactor lab çalıştırılmalı.")
        return

    with SOURCE.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("HATA: multifactor raporu boş.")
        return

    mf = weighted(rows, "exam")
    base = weighted(rows, "base_exam")
    hit_delta = (mf["hit"] - base["hit"]) * 100
    avg_delta = mf["avg"] - base["avg"]

    family_counts = Counter()
    champion_counts = Counter()
    keep_values = defaultdict(list)
    cutoff_values = defaultdict(list)
    cycle_rows = []
    win_cycles = tie_cycles = lose_cycles = 0

    for r in rows:
        champ = parse_model(r["champion"])
        chal = parse_model(r["challenger"])
        family_counts[chal["family"]] += 1
        champion_counts[champ["family"]] += 1
        if champ["keep"] is not None:
            keep_values[champ["family"]].append(champ["keep"])
        if champ["cutoff"] is not None:
            cutoff_values[champ["family"]].append(champ["cutoff"])

        ex_n = int(float(r["exam_n"]))
        base_n = int(float(r["base_exam_n"]))
        ex_hit = float(r["exam_hit"])
        base_hit = float(r["base_exam_hit"])
        ex_avg = float(r["exam_avg"])
        base_avg = float(r["base_exam_avg"])
        delta = ex_hit - base_hit
        if delta > 1e-12:
            win_cycles += 1
        elif delta < -1e-12:
            lose_cycles += 1
        else:
            tie_cycles += 1
        cycle_rows.append({
            "cycle": int(r["cycle"]),
            "champion": r["champion"],
            "exam_n": ex_n,
            "exam_hit": ex_hit,
            "exam_avg": ex_avg,
            "base_n": base_n,
            "base_hit": base_hit,
            "base_avg": base_avg,
            "hit_delta_pp": delta * 100,
            "avg_delta": ex_avg - base_avg,
        })

    exam_hits = [x["exam_hit"] for x in cycle_rows if x["exam_n"] > 0]
    base_hits = [x["base_hit"] for x in cycle_rows if x["base_n"] > 0]
    worst = min(cycle_rows, key=lambda x: x["hit_delta_pp"])
    best = max(cycle_rows, key=lambda x: x["hit_delta_pp"])

    print("AG Crypto Analyst - Multifactor Robustness & Weight Stability Analyzer")
    print(f"Kaynak: {SOURCE}")
    print("Amaç: %82 başarının tek dönem etkisi mi yoksa tekrar eden bir avantaj mı olduğunu ölçmek.")
    print()
    print("Toplam frozen EXAM karşılaştırması:")
    print(f"  MULTIFACTOR: n={mf['n']} | hit_rate={mf['hit']*100:.1f}% | directional_return={mf['avg']:+.3f}%")
    print(f"  NEWS BASE  : n={base['n']} | hit_rate={base['hit']*100:.1f}% | directional_return={base['avg']:+.3f}%")
    print(f"  FARK       : hit={hit_delta:+.1f}pp | directional_return={avg_delta:+.3f}%")
    print()
    print(f"Cycle sonucu: multifactor üstün={win_cycles} | eşit={tie_cycles} | geride={lose_cycles}")
    print(f"Cycle hit oynaklığı: multifactor={stdev_pp(exam_hits):.1f}pp | base={stdev_pp(base_hits):.1f}pp")
    print(f"En güçlü cycle farkı: Cycle {best['cycle']} {best['hit_delta_pp']:+.1f}pp | champion={best['champion']}")
    print(f"En zayıf cycle farkı: Cycle {worst['cycle']} {worst['hit_delta_pp']:+.1f}pp | champion={worst['champion']}")
    print()

    print("Champion model ailesi sürekliliği:")
    for family, count in champion_counts.most_common():
        kv = keep_values.get(family, [])
        cv = cutoff_values.get(family, [])
        keep_text = f"keep ort={statistics.mean(kv)*100:.1f}%" if kv else "keep=n/a"
        if len(kv) > 1:
            keep_text += f" std={statistics.pstdev(kv)*100:.1f}pp"
        cut_text = f"cut ort={statistics.mean(cv):.3f}" if cv else "cut=n/a"
        if len(cv) > 1:
            cut_text += f" std={statistics.pstdev(cv):.3f}"
        print(f"  {family:14s} | champion_cycles={count} | {keep_text} | {cut_text}")
    print()

    print("LEARN tarafında en sık Challenger aileleri:")
    for family, count in family_counts.most_common():
        print(f"  {family:14s} | top_candidate_cycles={count}")
    print()

    # Simple audit grade: not a probability, only a research status.
    if hit_delta >= 5 and win_cycles >= 3 and lose_cycles <= 1 and mf["n"] >= 40:
        grade = "ROBUST_A"
    elif hit_delta > 0 and win_cycles >= 2 and mf["n"] >= 30:
        grade = "ROBUST_B"
    elif hit_delta > 0:
        grade = "WATCH"
    else:
        grade = "FRAGILE"
    print(f"Araştırma statüsü: {grade}")
    print("Not: bu statü başarı olasılığı değildir; frozen EXAM tekrarına ve örnekleme dayalı dayanıklılık etiketidir.")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    fields = ["cycle","champion","exam_n","exam_hit","exam_avg","base_n","base_hit","base_avg","hit_delta_pp","avg_delta"]
    with REPORT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(cycle_rows)
    print("Sonuç dosyası:", REPORT)


if __name__ == "__main__":
    main()
