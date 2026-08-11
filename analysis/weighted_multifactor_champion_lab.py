"""Weighted multi-factor Champion/Challenger laboratory for AG Crypto Analyst.

This module moves beyond binary technical filters. It starts from the proven
1h news base policy and builds a continuous confluence score from information
available at signal time only:

- news confidence
- news impact
- RSI bearishness
- MACD bearishness
- EMA20/EMA50 bearish spread
- 6h bearish momentum
- relative volume
- BTC market-regime context

Candidate weight sets and score cutoffs are optimized only on LEARN, compared
against the current Champion on unseen CHALLENGE, then frozen for EXAM.
Challenge/EXAM outcomes never participate in their own selection.
"""
from __future__ import annotations

import csv
import math
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

from analysis.market_regime_analyzer import classify_regime
from analysis.news_technical_confluence_lab import technical_state
from analysis.walk_forward_backtest import DATASET, evaluate_row, load_rows, policy_subset
from data.historical_market_data import get_spot_symbols

DB_NAME = "crypto_data.db"
REPORT_PATH = Path("reports/weighted_multifactor_champion_lab.csv")
LEARN_DAYS = int(os.getenv("AGCA_MULTI_LEARN_DAYS", "120"))
CHALLENGE_DAYS = int(os.getenv("AGCA_MULTI_CHALLENGE_DAYS", "30"))
EXAM_DAYS = int(os.getenv("AGCA_MULTI_EXAM_DAYS", "30"))
STEP_DAYS = int(os.getenv("AGCA_MULTI_STEP_DAYS", "30"))
MIN_LEARN_N = int(os.getenv("AGCA_MULTI_MIN_LEARN_N", "18"))
MIN_CHALLENGE_N = int(os.getenv("AGCA_MULTI_MIN_CHALLENGE_N", "5"))

# None means the established base 1h news policy with no multifactor selection.
BASE_MODEL = None

# Deliberately small, interpretable model family to reduce overfitting.
WEIGHT_SETS = [
    ("BALANCED",       {"confidence":2, "impact":1, "rsi":1, "macd":1, "ema":1, "momentum":1, "volume":0, "regime":0}),
    ("NEWS_HEAVY",     {"confidence":3, "impact":2, "rsi":1, "macd":1, "ema":1, "momentum":1, "volume":0, "regime":0}),
    ("TECH_HEAVY",     {"confidence":1, "impact":1, "rsi":2, "macd":2, "ema":2, "momentum":2, "volume":0, "regime":0}),
    ("MOMENTUM",       {"confidence":2, "impact":1, "rsi":1, "macd":2, "ema":1, "momentum":3, "volume":1, "regime":0}),
    ("REGIME_AWARE",   {"confidence":2, "impact":1, "rsi":1, "macd":1, "ema":1, "momentum":1, "volume":0, "regime":2}),
    ("NEWS_REGIME",    {"confidence":3, "impact":2, "rsi":0, "macd":1, "ema":0, "momentum":1, "volume":0, "regime":2}),
]
KEEP_FRACTIONS = (0.35, 0.50, 0.65, 0.80)


def clamp(value, low=-1.0, high=1.0):
    return max(low, min(high, value))


def feature_vector(row):
    # All features are oriented so +1 supports a negative-news/downside signal.
    confidence = clamp((row["confidence"] - 70.0) / 20.0, 0.0, 1.0)
    impact = clamp((row["impact"] - 25.0) / 60.0, 0.0, 1.0)
    rsi = clamp((50.0 - row["rsi"]) / 30.0)

    price = row.get("entry_price", 0.0) or 0.0
    histogram = row["histogram"]
    macd = clamp((-histogram / price) * 1000.0) if price else (1.0 if histogram < 0 else -1.0)

    ema_spread = row["ema_spread_pct"]
    ema = clamp(-ema_spread / 3.0)
    momentum = clamp(-row["ret6"] / 5.0)
    volume = clamp((row["volume_ratio"] - 1.0) / 1.5, 0.0, 1.0)

    trend = row["btc_trend"]
    if trend == "STRONG_DOWN": regime = 1.0
    elif trend in ("DOWN", "SIDEWAYS"): regime = 0.5
    elif trend == "UP": regime = -0.5
    else: regime = -1.0

    return {
        "confidence": confidence, "impact": impact, "rsi": rsi, "macd": macd,
        "ema": ema, "momentum": momentum, "volume": volume, "regime": regime,
    }


def model_score(row, weights):
    fv = row["features"]
    denom = sum(abs(v) for v in weights.values()) or 1.0
    return sum(fv[k] * w for k, w in weights.items()) / denom


def metrics(rows):
    n = len(rows)
    if not n:
        return {"n": 0, "hit": 0.0, "avg": 0.0, "score": -999.0}
    hit = sum(r["hit"] for r in rows) / n
    avg = sum(r["directional_return"] for r in rows) / n
    reliability = n / (n + 25.0)
    # Accuracy is primary; return and evidence size prevent tiny lucky models.
    score = ((hit - 0.5) * 100.0 + avg * 12.0) * reliability
    return {"n": n, "hit": hit, "avg": avg, "score": score}


def quantile(values, q):
    if not values:
        return 0.0
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def apply_model(rows, model):
    if model is None:
        return list(rows)
    return [r for r in rows if model_score(r, model["weights"]) >= model["cutoff"]]


def model_label(model):
    if model is None:
        return "BASE_ALL"
    return f"{model['name']}|keep={int(model['keep_fraction']*100)}%|cut={model['cutoff']:.3f}"


def same_model(a, b):
    return model_label(a) == model_label(b)


def learn_candidates(rows):
    candidates = []
    for name, weights in WEIGHT_SETS:
        scored = [(model_score(r, weights), r) for r in rows]
        if not scored:
            continue
        values = [x[0] for x in scored]
        for keep in KEEP_FRACTIONS:
            cutoff = quantile(values, 1.0 - keep)
            model = {"name": name, "weights": weights, "keep_fraction": keep, "cutoff": cutoff}
            selected = apply_model(rows, model)
            m = metrics(selected)
            if m["n"] >= MIN_LEARN_N:
                candidates.append((m["score"], m["hit"], m["n"], model, m))
    # Base is always an eligible learning reference.
    bm = metrics(rows)
    candidates.append((bm["score"], bm["hit"], bm["n"], None, bm))
    candidates.sort(reverse=True, key=lambda x: (x[0], x[1], x[2]))
    return candidates


def entry_price(con, symbol, entry_time):
    row = con.execute(
        "SELECT open FROM market_klines WHERE symbol=? AND interval='1h' AND open_time=? LIMIT 1",
        (symbol, entry_time.isoformat()),
    ).fetchone()
    return float(row[0]) if row else 0.0


def enrich(con, symbols, rows):
    tech_cache, regime_cache, out = {}, {}, []
    for row in rows:
        symbol = symbols.get(row["coin"])
        if not symbol:
            continue
        t = row["entry_time"]
        tk = (symbol, t)
        if tk not in tech_cache:
            tech_cache[tk] = technical_state(con, symbol, t)
        if t not in regime_cache:
            regime_cache[t] = classify_regime(con, t)
        tech, regime = tech_cache[tk], regime_cache[t]
        if not tech or not regime:
            continue
        item = dict(row)
        item.update(tech)
        item["btc_trend"] = regime["trend"]
        item["entry_price"] = entry_price(con, symbol, t)

        # Compute EMA spread directly from state buckets is too coarse, so fetch
        # a short history and use latest EMA values through the shared helpers.
        candles = con.execute(
            """SELECT close FROM market_klines WHERE symbol=? AND interval='1h' AND close_time<=?
               ORDER BY close_time DESC LIMIT 60""",
            (symbol, t.isoformat()),
        ).fetchall()
        closes = [float(x[0]) for x in reversed(candles)]
        from technical_analysis.technical_analyzer import calculate_ema_series
        e20 = calculate_ema_series(closes, 20)
        e50 = calculate_ema_series(closes, 50)
        if not e20 or not e50 or e50[-1] == 0:
            continue
        item["ema_spread_pct"] = (e20[-1] / e50[-1] - 1.0) * 100.0
        item["features"] = feature_vector(item)
        out.append(item)
    return out


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
            print("Weighted multifactor için değerlendirilebilir veri yok.")
            return

        start, end = min(r["entry_time"] for r in rows), max(r["exit_time"] for r in rows)
        champion, cursor, cycle, report = BASE_MODEL, start, 0, []

        print("AG Crypto Analyst - Weighted Multi-Factor Champion/Challenger Lab")
        print("Base 1h news policy: impact>=0 | confidence>=70 | event=ALL | direction=negative")
        print("Faktörler: confidence + impact + RSI + MACD + EMA spread + 6h momentum + volume + BTC regime")
        print(f"Pencereler: LEARN={LEARN_DAYS}g | CHALLENGE={CHALLENGE_DAYS}g | EXAM={EXAM_DAYS}g | STEP={STEP_DAYS}g")
        print("Akış: LEARN ağırlık/cutoff -> unseen CHALLENGE -> promote/reject -> FROZEN EXAM -> roll")
        print("Leakage: tüm özellikler sinyal anına kadar; challenge/exam sonucu kendi seçiminden önce görünmez.")
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

            ranked = learn_candidates(learn)
            challenger = ranked[0][3]
            lm = ranked[0][4]
            cm = metrics(apply_model(challenge, champion))
            qm = metrics(apply_model(challenge, challenger))

            if same_model(challenger, champion):
                decision, winner, chm = "KEEP_CHAMPION", champion, cm
            elif qm["n"] >= MIN_CHALLENGE_N and qm["hit"] > cm["hit"] and qm["score"] > cm["score"]:
                decision, winner, chm = "PROMOTE_CHALLENGER", challenger, qm
            else:
                decision, winner, chm = "REJECT_CHALLENGER", champion, cm
            champion = winner

            ex = metrics(apply_model(exam, champion))
            bx = metrics(exam)
            print(f"Cycle {cycle}: {cursor.date()} -> {exam_end.date()}")
            print(f"  challenger={model_label(challenger)} | learn n={lm['n']} hit={lm['hit']*100:.1f}% avg={lm['avg']:+.3f}%")
            print(f"  {decision} -> champion={model_label(champion)}")
            print(f"  challenge n={chm['n']} hit={chm['hit']*100:.1f}% avg={chm['avg']:+.3f}%")
            print(f"  FROZEN EXAM: multifactor n={ex['n']} hit={ex['hit']*100:.1f}% avg={ex['avg']:+.3f}% | base n={bx['n']} hit={bx['hit']*100:.1f}% avg={bx['avg']:+.3f}%")
            print("  learn top3: " + " | ".join(f"{model_label(x[3])} n={x[4]['n']} hit={x[4]['hit']*100:.1f}% avg={x[4]['avg']:+.3f}%" for x in ranked[:3]))
            print()

            report.append({
                "cycle": cycle, "learn_start": cursor.isoformat(), "exam_end": exam_end.isoformat(),
                "decision": decision, "challenger": model_label(challenger), "champion": model_label(champion),
                "challenge_n": chm["n"], "challenge_hit": chm["hit"], "challenge_avg": chm["avg"],
                "exam_n": ex["n"], "exam_hit": ex["hit"], "exam_avg": ex["avg"],
                "base_exam_n": bx["n"], "base_exam_hit": bx["hit"], "base_exam_avg": bx["avg"],
            })
            cursor += timedelta(days=STEP_DAYS)

        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        if report:
            with REPORT_PATH.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(report[0])); w.writeheader(); w.writerows(report)

        n, bn = sum(r["exam_n"] for r in report), sum(r["base_exam_n"] for r in report)
        hit = sum(r["exam_hit"] * r["exam_n"] for r in report) / n if n else 0.0
        avg = sum(r["exam_avg"] * r["exam_n"] for r in report) / n if n else 0.0
        bh = sum(r["base_exam_hit"] * r["base_exam_n"] for r in report) / bn if bn else 0.0
        ba = sum(r["base_exam_avg"] * r["base_exam_n"] for r in report) / bn if bn else 0.0
        print(f"Tamamlanan cycle: {cycle}")
        print("Toplam FROZEN EXAM:")
        print(f"  MULTIFACTOR: n={n} | hit_rate={hit*100:.1f}% | directional_return={avg:+.3f}%")
        print(f"  NEWS BASE  : n={bn} | hit_rate={bh*100:.1f}% | directional_return={ba:+.3f}%")
        print("Sonuç dosyası:", REPORT_PATH)
        print("Not: model yalnızca unseen EXAM avantajı tekrarlanırsa Champion adayıdır.")
    finally:
        con.close()


if __name__ == "__main__":
    main()
