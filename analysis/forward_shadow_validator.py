"""Forward-only shadow validation for the locked AG Crypto Analyst 1h Champion.

This module does NOT backfill predictions. A prediction is born only when this
program sees an eligible live news analysis for the first time. Its entry price
and timestamp are frozen immediately. After one hour, the result is settled
from Binance minute data near the pre-committed exit timestamp.

Locked research candidates:
- NEWS_BASE: negative news, confidence >= 70
- REGIME_AWARE multifactor: fixed weights, keep logic represented by the frozen
  score cutoff 0.249 discovered before forward validation.

No outcome is used to change weights or cutoff. This is intentionally boring:
its job is to provide honest prospective evidence.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import requests

DB_NAME = "crypto_data.db"
BINANCE = "https://api.binance.com"
HORIZON_HOURS = 1
LOCKED_CUTOFF = 0.249
LOCKED_WEIGHTS = {
    "confidence": 2.0,
    "impact": 1.0,
    "rsi": 1.0,
    "macd": 1.0,
    "ema": 1.0,
    "momentum": 1.0,
    "volume": 0.0,
    "regime": 2.0,
}
MODEL_VERSION = "REGIME_AWARE_FORWARD_V1"


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def parse_dt(value):
    if not value:
        return None
    value = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def clamp(value, low=-1.0, high=1.0):
    return max(low, min(high, value))


def get_json(path, params=None):
    response = requests.get(BINANCE + path, params=params or {}, timeout=15)
    response.raise_for_status()
    return response.json()


def spot_symbols():
    data = get_json("/api/v3/exchangeInfo")
    return {
        x["baseAsset"]: x["symbol"]
        for x in data.get("symbols", [])
        if x.get("quoteAsset") == "USDT" and x.get("status") == "TRADING" and x.get("isSpotTradingAllowed", True)
    }


def current_price(symbol):
    return float(get_json("/api/v3/ticker/price", {"symbol": symbol})["price"])


def closed_1h_klines(symbol, limit=100):
    raw = get_json("/api/v3/klines", {"symbol": symbol, "interval": "1h", "limit": limit})
    now_ms = int(time.time() * 1000)
    return [x for x in raw if int(x[6]) < now_ms]


def ema_series(values, period):
    if len(values) < period:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [sum(values[:period]) / period]
    for value in values[period:]:
        out.append(value * alpha + out[-1] * (1.0 - alpha))
    return out


def rsi(values, period=14):
    if len(values) <= period:
        return 50.0
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(x, 0.0) for x in changes[-period:]]
    losses = [max(-x, 0.0) for x in changes[-period:]]
    avg_gain, avg_loss = sum(gains) / period, sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def technical_features(symbol):
    candles = closed_1h_klines(symbol, 100)
    if len(candles) < 60:
        raise RuntimeError(f"{symbol}: teknik özellik için yetersiz kapalı mum")
    closes = [float(x[4]) for x in candles]
    volumes = [float(x[5]) for x in candles]
    e12, e26 = ema_series(closes, 12), ema_series(closes, 26)
    common = min(len(e12), len(e26))
    macd_line = [e12[-common + i] - e26[-common + i] for i in range(common)]
    signal = ema_series(macd_line, 9)
    histogram = macd_line[-1] - signal[-1] if signal else 0.0
    e20, e50 = ema_series(closes, 20), ema_series(closes, 50)
    ema_spread_pct = (e20[-1] / e50[-1] - 1.0) * 100.0
    ret6 = (closes[-1] / closes[-7] - 1.0) * 100.0
    base_vol = sum(volumes[-21:-1]) / 20.0 if sum(volumes[-21:-1]) else 0.0
    volume_ratio = volumes[-1] / base_vol if base_vol else 1.0
    return {
        "rsi": rsi(closes), "histogram": histogram,
        "ema_spread_pct": ema_spread_pct, "ret6": ret6,
        "volume_ratio": volume_ratio,
    }


def btc_regime():
    candles = closed_1h_klines("BTCUSDT", 80)
    closes = [float(x[4]) for x in candles]
    if len(closes) < 73:
        raise RuntimeError("BTC rejimi için yetersiz kapalı mum")
    ret24 = (closes[-1] / closes[-25] - 1.0) * 100.0
    if ret24 <= -3.0:
        trend = "STRONG_DOWN"
    elif ret24 <= -0.75:
        trend = "DOWN"
    elif ret24 < 0.75:
        trend = "SIDEWAYS"
    elif ret24 < 3.0:
        trend = "UP"
    else:
        trend = "STRONG_UP"
    return trend, ret24


def feature_vector(confidence, impact, tech, regime, entry_price):
    confidence_f = clamp((confidence - 70.0) / 20.0, 0.0, 1.0)
    impact_f = clamp((impact - 25.0) / 60.0, 0.0, 1.0)
    rsi_f = clamp((50.0 - tech["rsi"]) / 30.0)
    hist = tech["histogram"]
    macd_f = clamp((-hist / entry_price) * 1000.0) if entry_price else (1.0 if hist < 0 else -1.0)
    ema_f = clamp(-tech["ema_spread_pct"] / 3.0)
    momentum_f = clamp(-tech["ret6"] / 5.0)
    volume_f = clamp((tech["volume_ratio"] - 1.0) / 1.5, 0.0, 1.0)
    if regime == "STRONG_DOWN":
        regime_f = 1.0
    elif regime in ("DOWN", "SIDEWAYS"):
        regime_f = 0.5
    elif regime == "UP":
        regime_f = -0.5
    else:
        regime_f = -1.0
    return {
        "confidence": confidence_f, "impact": impact_f, "rsi": rsi_f,
        "macd": macd_f, "ema": ema_f, "momentum": momentum_f,
        "volume": volume_f, "regime": regime_f,
    }


def score(features):
    denom = sum(abs(v) for v in LOCKED_WEIGHTS.values()) or 1.0
    return sum(features[k] * w for k, w in LOCKED_WEIGHTS.items()) / denom


def create_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS forward_shadow_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_version TEXT NOT NULL,
            news_id INTEGER NOT NULL,
            coin TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            confidence INTEGER NOT NULL,
            impact INTEGER NOT NULL,
            entry_time TEXT NOT NULL,
            due_time TEXT NOT NULL,
            entry_price REAL NOT NULL,
            multifactor_score REAL NOT NULL,
            base_eligible INTEGER NOT NULL,
            multifactor_eligible INTEGER NOT NULL,
            feature_json TEXT NOT NULL,
            btc_regime TEXT NOT NULL,
            exit_time TEXT,
            exit_price REAL,
            return_pct REAL,
            directional_return REAL,
            hit INTEGER,
            status TEXT NOT NULL DEFAULT 'OPEN',
            created_at TEXT NOT NULL,
            settled_at TEXT,
            UNIQUE(model_version, news_id, coin)
        )
    """)
    con.commit()


def eligible_news(con):
    return con.execute("""
        SELECT a.news_id, a.coins, a.direction, a.impact_score, a.confidence_score,
               n.title, n.published_at
        FROM crypto_news_analysis a
        JOIN crypto_news n ON n.id=a.news_id
        WHERE a.direction='negative' AND a.confidence_score>=70
        ORDER BY a.news_id
    """).fetchall()


def record_new_predictions(con):
    symbols = spot_symbols()
    regime, btc_ret24 = btc_regime()
    created = skipped = 0
    now = utcnow()
    for row in eligible_news(con):
        news_id, coins, direction, impact, confidence, title, published_at = row
        for coin in [x.strip() for x in coins.split(",") if x.strip() and x.strip() != "MARKET"]:
            symbol = symbols.get(coin)
            if not symbol:
                skipped += 1
                continue
            exists = con.execute(
                "SELECT 1 FROM forward_shadow_predictions WHERE model_version=? AND news_id=? AND coin=?",
                (MODEL_VERSION, news_id, coin),
            ).fetchone()
            if exists:
                continue
            # Critical forward-only rule: old articles are never reconstructed as predictions.
            # The first observation time is the immutable entry time.
            entry_time = utcnow()
            price = current_price(symbol)
            tech = technical_features(symbol)
            features = feature_vector(confidence, impact, tech, regime, price)
            mf_score = score(features)
            due = entry_time + timedelta(hours=HORIZON_HOURS)
            payload = dict(features)
            payload.update({"btc_ret24": btc_ret24, "news_published_at": published_at, "title": title})
            con.execute("""
                INSERT INTO forward_shadow_predictions (
                    model_version,news_id,coin,symbol,direction,confidence,impact,
                    entry_time,due_time,entry_price,multifactor_score,base_eligible,
                    multifactor_eligible,feature_json,btc_regime,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                MODEL_VERSION, news_id, coin, symbol, direction, confidence, impact,
                iso(entry_time), iso(due), price, mf_score, 1,
                int(mf_score >= LOCKED_CUTOFF), json.dumps(payload, ensure_ascii=False),
                regime, iso(now),
            ))
            created += 1
            print(f"YENİ #{news_id} {coin}: base=YES | multifactor={'YES' if mf_score >= LOCKED_CUTOFF else 'NO'} | score={mf_score:.3f} | entry={price:.8g}")
    con.commit()
    return created, skipped


def price_at_due(symbol, due):
    start_ms = int(due.timestamp() * 1000)
    raw = get_json("/api/v3/klines", {
        "symbol": symbol, "interval": "1m", "startTime": start_ms,
        "endTime": start_ms + 5 * 60 * 1000, "limit": 5,
    })
    if not raw:
        return None, None
    return float(raw[0][1]), datetime.fromtimestamp(int(raw[0][0]) / 1000.0, tz=timezone.utc)


def settle_due(con):
    now = utcnow()
    rows = con.execute("""
        SELECT id,symbol,entry_price,due_time FROM forward_shadow_predictions
        WHERE status='OPEN' AND due_time<=? ORDER BY due_time
    """, (iso(now),)).fetchall()
    settled = 0
    for pred_id, symbol, entry_price, due_raw in rows:
        due = parse_dt(due_raw)
        exit_price, exit_time = price_at_due(symbol, due)
        if exit_price is None:
            continue
        ret = (exit_price / float(entry_price) - 1.0) * 100.0
        directional = -ret  # locked signal direction is negative/down.
        hit = int(directional > 0.0)
        con.execute("""
            UPDATE forward_shadow_predictions
            SET exit_time=?,exit_price=?,return_pct=?,directional_return=?,hit=?,status='SETTLED',settled_at=?
            WHERE id=?
        """, (iso(exit_time), exit_price, ret, directional, hit, iso(now), pred_id))
        settled += 1
    con.commit()
    return settled


def summary(con):
    print("\nFORWARD SHADOW SCOREBOARD")
    for label, condition in (
        ("NEWS BASE", "base_eligible=1"),
        ("MULTIFACTOR", "multifactor_eligible=1"),
    ):
        n, hits, avg = con.execute(f"""
            SELECT COUNT(*), COALESCE(SUM(hit),0), COALESCE(AVG(directional_return),0)
            FROM forward_shadow_predictions WHERE status='SETTLED' AND {condition}
        """).fetchone()
        open_n = con.execute(f"SELECT COUNT(*) FROM forward_shadow_predictions WHERE status='OPEN' AND {condition}").fetchone()[0]
        rate = (hits / n * 100.0) if n else 0.0
        print(f"  {label:<11}: settled={n} | open={open_n} | hit_rate={rate:.1f}% | directional_return={avg:+.3f}%")


def main():
    con = sqlite3.connect(DB_NAME)
    try:
        create_table(con)
        print("AG Crypto Analyst - Forward Shadow Validation")
        print(f"LOCKED model: {MODEL_VERSION} | cutoff={LOCKED_CUTOFF:.3f} | horizon={HORIZON_HOURS}h")
        print("Kural: geçmiş tahmin üretilmez; ilk görüldüğü anda entry dondurulur, model parametreleri değiştirilmez.")
        settled = settle_due(con)
        created, skipped = record_new_predictions(con)
        print(f"\nBu çalıştırma: yeni tahmin={created} | sonuçlanan={settled} | spot eşleşmeyen={skipped}")
        summary(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
