"""Leakage-safe News + Technical Confluence laboratory for the robust 1h policy.

Base news policy remains frozen:
    impact>=0, confidence>=70, event=ALL, direction=negative

Technical state is computed from each coin's stored 1h candles using only
candles closed at or before the news signal entry time. Candidate technical
filters are learned on LEARN, challenged on unseen CHALLENGE, then frozen for
EXAM. No challenge/exam outcome participates in its own feature selection.
"""
from __future__ import annotations

import csv
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

from analysis.walk_forward_backtest import DATASET, evaluate_row, load_rows, policy_subset, parse_time
from data.historical_market_data import get_spot_symbols
from technical_analysis.technical_analyzer import calculate_ema_series, calculate_macd, calculate_rsi

DB_NAME = "crypto_data.db"
REPORT_PATH = Path("reports/news_technical_confluence_lab.csv")
LEARN_DAYS = int(os.getenv("AGCA_CONFLUENCE_LEARN_DAYS", "120"))
CHALLENGE_DAYS = int(os.getenv("AGCA_CONFLUENCE_CHALLENGE_DAYS", "30"))
EXAM_DAYS = int(os.getenv("AGCA_CONFLUENCE_EXAM_DAYS", "30"))
STEP_DAYS = int(os.getenv("AGCA_CONFLUENCE_STEP_DAYS", "30"))
MIN_LEARN_N = int(os.getenv("AGCA_CONFLUENCE_MIN_LEARN_N", "15"))
MIN_CHALLENGE_N = int(os.getenv("AGCA_CONFLUENCE_MIN_CHALLENGE_N", "5"))
BASE_FILTER = "ALL_TECH"


def metrics(rows):
    n = len(rows)
    if not n:
        return {"n": 0, "hit": 0.0, "avg": 0.0, "score": -999.0}
    hit = sum(r["hit"] for r in rows) / n
    avg = sum(r["directional_return"] for r in rows) / n
    reliability = n / (n + 25.0)
    score = ((hit - 0.5) * 100.0 + avg * 12.0) * reliability
    return {"n": n, "hit": hit, "avg": avg, "score": score}


def candles_before(con, symbol, entry_time, limit=100):
    rows = con.execute(
        """SELECT close_time, close, volume FROM market_klines
           WHERE symbol=? AND interval='1h' AND close_time<=?
           ORDER BY close_time DESC LIMIT ?""",
        (symbol, entry_time.isoformat(), limit),
    ).fetchall()
    rows.reverse()
    return [(parse_time(t), float(c), float(v)) for t, c, v in rows]


def technical_state(con, symbol, entry_time):
    candles = candles_before(con, symbol, entry_time, 100)
    if len(candles) < 60:
        return None
    closes = [x[1] for x in candles]
    volumes = [x[2] for x in candles]
    rsi = calculate_rsi(closes)
    macd, signal, histogram = calculate_macd(closes)
    ema20s = calculate_ema_series(closes, 20)
    ema50s = calculate_ema_series(closes, 50)
    if rsi is None or histogram is None or not ema20s or not ema50s:
        return None
    ema20, ema50 = ema20s[-1], ema50s[-1]
    ret6 = (closes[-1] / closes[-7] - 1) * 100 if len(closes) >= 7 else 0.0
    vol20 = sum(volumes[-20:]) / 20
    volume_ratio = volumes[-1] / vol20 if vol20 else 1.0

    if rsi >= 70:
        rsi_bucket = "OVERBOUGHT"
    elif rsi <= 30:
        rsi_bucket = "OVERSOLD"
    elif rsi >= 50:
        rsi_bucket = "RSI_POS"
    else:
        rsi_bucket = "RSI_NEG"
    macd_bucket = "MACD_POS" if histogram > 0 else "MACD_NEG"
    ema_bucket = "EMA_POS" if ema20 > ema50 else "EMA_NEG"
    momentum = "MOM_UP" if ret6 > 0 else "MOM_DOWN"
    volume = "VOL_HIGH" if volume_ratio >= 1.5 else "VOL_NORMAL"
    score = 0
    score += 1 if rsi >= 50 and rsi < 70 else -1
    score += 1 if histogram > 0 else -1
    score += 1 if ema20 > ema50 else -1
    tech_direction = "TECH_POS" if score >= 1 else "TECH_NEG"
    return {
        "rsi_bucket": rsi_bucket, "macd_bucket": macd_bucket,
        "ema_bucket": ema_bucket, "momentum": momentum, "volume_bucket": volume,
        "tech_direction": tech_direction, "rsi": rsi, "histogram": histogram,
        "ret6": ret6, "volume_ratio": volume_ratio,
    }


def matches(row, name):
    if name == BASE_FILTER:
        return True
    if name == "TECH_NEG": return row["tech_direction"] == "TECH_NEG"
    if name == "TECH_POS": return row["tech_direction"] == "TECH_POS"
    if name == "MACD_NEG": return row["macd_bucket"] == "MACD_NEG"
    if name == "MACD_POS": return row["macd_bucket"] == "MACD_POS"
    if name == "EMA_NEG": return row["ema_bucket"] == "EMA_NEG"
    if name == "EMA_POS": return row["ema_bucket"] == "EMA_POS"
    if name == "MOM_DOWN": return row["momentum"] == "MOM_DOWN"
    if name == "MOM_UP": return row["momentum"] == "MOM_UP"
    if name == "RSI_NEG": return row["rsi_bucket"] in ("RSI_NEG", "OVERSOLD")
    if name == "RSI_POS": return row["rsi_bucket"] in ("RSI_POS", "OVERBOUGHT")
    if name == "NEG_CONFLUENCE": return row["macd_bucket"] == "MACD_NEG" and row["ema_bucket"] == "EMA_NEG"
    if name == "POS_CONFLUENCE": return row["macd_bucket"] == "MACD_POS" and row["ema_bucket"] == "EMA_POS"
    if name == "NEG_MOM_CONFLUENCE": return row["tech_direction"] == "TECH_NEG" and row["momentum"] == "MOM_DOWN"
    if name == "HIGH_VOLUME": return row["volume_bucket"] == "VOL_HIGH"
    return False


FILTERS = [BASE_FILTER, "TECH_NEG", "TECH_POS", "MACD_NEG", "MACD_POS", "EMA_NEG", "EMA_POS",
           "MOM_DOWN", "MOM_UP", "RSI_NEG", "RSI_POS", "NEG_CONFLUENCE", "POS_CONFLUENCE",
           "NEG_MOM_CONFLUENCE", "HIGH_VOLUME"]


def subset(rows, name):
    return [r for r in rows if matches(r, name)]


def learn_candidate(rows):
    ranked = []
    for name in FILTERS:
        m = metrics(subset(rows, name))
        if m["n"] >= MIN_LEARN_N:
            ranked.append((m["score"], m["hit"], m["n"], name, m))
    ranked.sort(reverse=True, key=lambda x: (x[0], x[1], x[2]))
    return ranked


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

        cache, rows = {}, []
        for row in base:
            symbol = symbols.get(row["coin"])
            if not symbol:
                continue
            ck = (symbol, row["entry_time"])
            if ck not in cache:
                cache[ck] = technical_state(con, symbol, row["entry_time"])
            state = cache[ck]
            if state:
                item = dict(row); item.update(state); rows.append(item)

        if not rows:
            print("Teknik confluence için değerlendirilebilir veri yok.")
            return
        start, end = min(r["entry_time"] for r in rows), max(r["exit_time"] for r in rows)
        champion, cursor, cycle, report = BASE_FILTER, start, 0, []

        print("AG Crypto Analyst - Leakage-Safe News + Technical Confluence Lab")
        print("Base news policy: impact>=0 | confidence>=70 | event=ALL | direction=negative")
        print(f"Pencereler: LEARN={LEARN_DAYS}g | CHALLENGE={CHALLENGE_DAYS}g | EXAM={EXAM_DAYS}g | STEP={STEP_DAYS}g")
        print("Teknik özellikler: RSI + MACD + EMA20/50 + 6h momentum + hacim; yalnızca sinyal anında kapanmış 1h mumlar.")
        print("Akış: LEARN teknik filtre -> unseen CHALLENGE -> promote/reject -> FROZEN EXAM -> roll")
        print()

        while True:
            learn_end = cursor + timedelta(days=LEARN_DAYS)
            challenge_end = learn_end + timedelta(days=CHALLENGE_DAYS)
            exam_end = challenge_end + timedelta(days=EXAM_DAYS)
            if exam_end > end: break
            cycle += 1
            learn = [r for r in rows if cursor <= r["exit_time"] < learn_end]
            challenge = [r for r in rows if learn_end <= r["entry_time"] < challenge_end]
            exam = [r for r in rows if challenge_end <= r["entry_time"] < exam_end]
            ranked = learn_candidate(learn)
            challenger = ranked[0][3] if ranked else BASE_FILTER
            lm = ranked[0][4] if ranked else metrics(subset(learn, challenger))
            cm, qm = metrics(subset(challenge, champion)), metrics(subset(challenge, challenger))
            if challenger == champion:
                decision, winner, chm = "KEEP_CHAMPION", champion, cm
            elif qm["n"] >= MIN_CHALLENGE_N and qm["hit"] > cm["hit"] and qm["score"] > cm["score"]:
                decision, winner, chm = "PROMOTE_CHALLENGER", challenger, qm
            else:
                decision, winner, chm = "REJECT_CHALLENGER", champion, cm
            champion = winner
            ex, bx = metrics(subset(exam, champion)), metrics(exam)
            print(f"Cycle {cycle}: {cursor.date()} -> {exam_end.date()}")
            print(f"  challenger={challenger} | learn n={lm['n']} hit={lm['hit']*100:.1f}% avg={lm['avg']:+.3f}%")
            print(f"  {decision} -> champion={champion} | challenge n={chm['n']} hit={chm['hit']*100:.1f}% avg={chm['avg']:+.3f}%")
            print(f"  FROZEN EXAM: confluence n={ex['n']} hit={ex['hit']*100:.1f}% avg={ex['avg']:+.3f}% | base n={bx['n']} hit={bx['hit']*100:.1f}% avg={bx['avg']:+.3f}%")
            if ranked:
                print("  learn top3: " + " | ".join(f"{x[3]} n={x[4]['n']} hit={x[4]['hit']*100:.1f}% avg={x[4]['avg']:+.3f}%" for x in ranked[:3]))
            print()
            report.append({"cycle": cycle, "learn_start": cursor.isoformat(), "exam_end": exam_end.isoformat(),
                           "decision": decision, "challenger": challenger, "champion": champion,
                           "challenge_n": chm["n"], "challenge_hit": chm["hit"], "challenge_avg": chm["avg"],
                           "exam_n": ex["n"], "exam_hit": ex["hit"], "exam_avg": ex["avg"],
                           "base_exam_n": bx["n"], "base_exam_hit": bx["hit"], "base_exam_avg": bx["avg"]})
            cursor += timedelta(days=STEP_DAYS)

        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        if report:
            with REPORT_PATH.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(report[0])); w.writeheader(); w.writerows(report)
        n, bn = sum(r["exam_n"] for r in report), sum(r["base_exam_n"] for r in report)
        hit = sum(r["exam_hit"]*r["exam_n"] for r in report)/n if n else 0
        avg = sum(r["exam_avg"]*r["exam_n"] for r in report)/n if n else 0
        bh = sum(r["base_exam_hit"]*r["base_exam_n"] for r in report)/bn if bn else 0
        ba = sum(r["base_exam_avg"]*r["base_exam_n"] for r in report)/bn if bn else 0
        print(f"Tamamlanan cycle: {cycle}")
        print("Toplam FROZEN EXAM:")
        print(f"  NEWS+TECH : n={n} | hit_rate={hit*100:.1f}% | directional_return={avg:+.3f}%")
        print(f"  NEWS BASE : n={bn} | hit_rate={bh*100:.1f}% | directional_return={ba:+.3f}%")
        print("Sonuç dosyası:", REPORT_PATH)
        print("Not: teknik filtre ancak unseen EXAM avantajı tekrar ederse üretim adayıdır.")
    finally:
        con.close()


if __name__ == "__main__":
    main()
