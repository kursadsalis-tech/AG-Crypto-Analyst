"""Forward-only shadow validation for the locked AG Crypto Analyst 1h Champion.

A valid prediction may be born only from news first collected AFTER the
validator's own start timestamp. Existing/pre-start articles are never turned
into prospective predictions.

Locked research candidates:
- NEWS_BASE: negative news, confidence >= 70
- REGIME_AWARE multifactor: fixed weights and frozen cutoff 0.249

After one hour, the prediction is settled from Binance minute data near the
pre-committed due timestamp. No outcome changes weights or cutoff.
"""
from __future__ import annotations

import json
import os
import sqlite3
from database.runtime_store import connect as runtime_connect
import time
from datetime import datetime, timedelta, timezone

import requests
from data.truth_engine import DataValidationError, validate_closed_klines

DB_NAME = "crypto_data.db"
BINANCE = os.getenv("AGCA_BINANCE_SPOT_BASE_URL", "https://data-api.binance.vision")
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
MODEL_VERSION = "REGIME_AWARE_FORWARD_V2"
LEGACY_INVALID_VERSION = "REGIME_AWARE_FORWARD_V1"


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
        if x.get("quoteAsset") == "USDT"
        and x.get("status") == "TRADING"
        and x.get("isSpotTradingAllowed", True)
    }


def current_price(symbol):
    return float(get_json("/api/v3/ticker/price", {"symbol": symbol})["price"])


def closed_1h_klines(symbol, limit=100):
    raw = get_json("/api/v3/klines", {"symbol": symbol, "interval": "1h", "limit": limit})
    now_ms = int(time.time() * 1000)
    closed = [x for x in raw if len(x) >= 8 and int(x[6]) < now_ms]
    return validate_closed_klines(
        closed,
        source=f"BINANCE_KLINES:{symbol}",
        minimum_rows=min(limit - 1, 60),
    )


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
    previous_volume = volumes[-21:-1]
    base_vol = sum(previous_volume) / len(previous_volume) if previous_volume and sum(previous_volume) else 0.0
    volume_ratio = volumes[-1] / base_vol if base_vol else 1.0
    return {
        "rsi": rsi(closes),
        "histogram": histogram,
        "ema_spread_pct": ema_spread_pct,
        "ret6": ret6,
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
        "confidence": confidence_f,
        "impact": impact_f,
        "rsi": rsi_f,
        "macd": macd_f,
        "ema": ema_f,
        "momentum": momentum_f,
        "volume": volume_f,
        "regime": regime_f,
    }


def score(features):
    denom = sum(abs(v) for v in LOCKED_WEIGHTS.values()) or 1.0
    return sum(features[k] * w for k, w in LOCKED_WEIGHTS.items()) / denom


def create_tables(con):
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
    con.execute("""
        CREATE TABLE IF NOT EXISTS forward_shadow_state (
            model_version TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    con.commit()


def invalidate_legacy_seed(con):
    changed = con.execute("""
        UPDATE forward_shadow_predictions
        SET status='INVALID_PRESTART_SEED'
        WHERE model_version=? AND status='OPEN'
    """, (LEGACY_INVALID_VERSION,)).rowcount
    con.commit()
    return changed


def get_or_create_start(con):
    row = con.execute(
        "SELECT started_at FROM forward_shadow_state WHERE model_version=?",
        (MODEL_VERSION,),
    ).fetchone()
    if row:
        return parse_dt(row[0]), False
    started = utcnow()
    con.execute(
        "INSERT INTO forward_shadow_state (model_version,started_at,created_at) VALUES (?,?,?)",
        (MODEL_VERSION, iso(started), iso(started)),
    )
    con.commit()
    return started, True


def eligible_news(con, started_at):
    return con.execute("""
        SELECT a.news_id, a.coins, a.direction, a.impact_score, a.confidence_score,
               n.title, n.published_at, n.collected_at
        FROM crypto_news_analysis a
        JOIN crypto_news n ON n.id=a.news_id
        WHERE a.direction='negative'
          AND a.confidence_score>=70
          AND datetime(n.collected_at) >= datetime(?)
        ORDER BY a.news_id
    """, (iso(started_at),)).fetchall()


def record_new_predictions(con, started_at):
    symbols = spot_symbols()
    regime, btc_ret24 = btc_regime()
    created = skipped = 0
    now = utcnow()
    for row in eligible_news(con, started_at):
        news_id, coins, direction, impact, confidence, title, published_at, collected_at = row
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

            # Forward-only rule: entry exists only now, after the article was first
            # collected after validator start. No pre-start article can enter V2.
            entry_time = utcnow()
            price = current_price(symbol)
            tech = technical_features(symbol)
            features = feature_vector(confidence, impact, tech, regime, price)
            mf_score = score(features)
            due = entry_time + timedelta(hours=HORIZON_HOURS)
            payload = dict(features)
            payload.update({
                "btc_ret24": btc_ret24,
                "news_published_at": published_at,
                "news_collected_at": collected_at,
                "title": title,
            })
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
            print(
                f"YENİ #{news_id} {coin}: base=YES | "
                f"multifactor={'YES' if mf_score >= LOCKED_CUTOFF else 'NO'} | "
                f"score={mf_score:.3f} | entry={price:.8g}"
            )
    con.commit()
    return created, skipped


def price_at_due(symbol, due):
    start_ms = int(due.timestamp() * 1000)
    raw = get_json("/api/v3/klines", {
        "symbol": symbol,
        "interval": "1m",
        "startTime": start_ms,
        "endTime": start_ms + 5 * 60 * 1000,
        "limit": 5,
    })
    if not raw:
        raise DataValidationError(
            f"BINANCE_SETTLEMENT:{symbol}", ("EMPTY_SETTLEMENT_PAYLOAD",)
        )
    validate_closed_klines(
        raw,
        source=f"BINANCE_SETTLEMENT:{symbol}",
        now=due + timedelta(minutes=6),
        minimum_rows=1,
        interval=timedelta(minutes=1),
        maximum_staleness=timedelta(minutes=7),
    )
    return float(raw[0][1]), datetime.fromtimestamp(int(raw[0][0]) / 1000.0, tz=timezone.utc)


def settle_due(con):
    now = utcnow()
    rows = con.execute("""
        SELECT id,symbol,entry_price,due_time
        FROM forward_shadow_predictions
        WHERE model_version=? AND status='OPEN' AND due_time<=?
        ORDER BY due_time
    """, (MODEL_VERSION, iso(now))).fetchall()
    settled = 0
    for pred_id, symbol, entry_price, due_raw in rows:
        due = parse_dt(due_raw)
        exit_price, exit_time = price_at_due(symbol, due)
        if exit_price is None:
            continue
        ret = (exit_price / float(entry_price) - 1.0) * 100.0
        directional = -ret
        hit = int(directional > 0.0)
        con.execute("""
            UPDATE forward_shadow_predictions
            SET exit_time=?,exit_price=?,return_pct=?,directional_return=?,hit=?,
                status='SETTLED',settled_at=?
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
            FROM forward_shadow_predictions
            WHERE model_version=? AND status='SETTLED' AND {condition}
        """, (MODEL_VERSION,)).fetchone()
        open_n = con.execute(f"""
            SELECT COUNT(*) FROM forward_shadow_predictions
            WHERE model_version=? AND status='OPEN' AND {condition}
        """, (MODEL_VERSION,)).fetchone()[0]
        rate = (hits / n * 100.0) if n else 0.0
        print(
            f"  {label:<11}: settled={n} | open={open_n} | "
            f"hit_rate={rate:.1f}% | directional_return={avg:+.3f}%"
        )


def main():
    con = runtime_connect(DB_NAME)
    try:
        create_tables(con)
        invalidated = invalidate_legacy_seed(con)
        started_at, first_start = get_or_create_start(con)

        print("AG Crypto Analyst - Forward Shadow Validation")
        print(f"LOCKED model: {MODEL_VERSION} | cutoff={LOCKED_CUTOFF:.3f} | horizon={HORIZON_HOURS}h")
        print(f"Forward başlangıcı: {iso(started_at)}")
        print("Kural: yalnızca bu başlangıçtan SONRA toplanan haberler tahmin doğurabilir.")
        if invalidated:
            print(f"Düzeltme: önceki V1 çalıştırmasındaki {invalidated} pre-start tahmin geçersiz olarak işaretlendi.")
        if first_start:
            print("V2 başlangıç mührü şimdi oluşturuldu; mevcut eski haberlerden tahmin üretilmeyecek.")

        settled = settle_due(con)
        created, skipped = record_new_predictions(con, started_at)
        print(f"\nBu çalıştırma: yeni tahmin={created} | sonuçlanan={settled} | spot eşleşmeyen={skipped}")
        summary(con)
        return {
            "status": "SUCCESS" if skipped == 0 else "PARTIAL",
            "created": created,
            "settled": settled,
            "skipped": skipped,
        }
    finally:
        con.close()


if __name__ == "__main__":
    main()
