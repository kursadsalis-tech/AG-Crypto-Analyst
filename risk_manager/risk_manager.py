"""Produce conservative market and asset risk limits from closed 1h candles.

This module does not create trade signals. It measures observable volatility,
drawdown, range and liquidity, then publishes caps that a later Decision Engine
must obey. Missing data tightens or blocks risk; it never increases permission.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import time
from datetime import datetime, timezone

import requests


DB_NAME = "crypto_data.db"
BINANCE_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
USER_AGENT = "AG-Crypto-Analyst/1.0.0"
POLICY_VERSION = "MARKET_RISK_POLICY_V1"
KLINE_LIMIT = 170
MIN_CLOSED_CANDLES = 169
MIN_COVERAGE_RATIO = 0.80

SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "ARKMUSDT",
    "FIDAUSDT",
    "WLDUSDT",
    "TIAUSDT",
    "ETHFIUSDT",
    "BIOUSDT",
    "LQTYUSDT",
    "ILVUSDT",
    "SLPUSDT",
    "UMAUSDT",
    "IOUSDT",
)

BASE_POSITION_CAPS = {
    "BTCUSDT": 15.0,
    "ETHUSDT": 10.0,
}
DEFAULT_ALT_POSITION_CAP = 5.0

TIER_MULTIPLIERS = {
    "NORMAL": 1.00,
    "ELEVATED": 0.60,
    "HIGH": 0.25,
    "EXTREME": 0.00,
}

GLOBAL_LIMITS = {
    "NORMAL": {
        "max_total_exposure_pct": 60.0,
        "max_alt_exposure_pct": 25.0,
        "max_single_position_pct": 10.0,
        "risk_per_trade_pct": 0.75,
        "daily_loss_limit_pct": 2.00,
        "max_new_positions": 3,
    },
    "CAUTION": {
        "max_total_exposure_pct": 40.0,
        "max_alt_exposure_pct": 15.0,
        "max_single_position_pct": 5.0,
        "risk_per_trade_pct": 0.50,
        "daily_loss_limit_pct": 1.50,
        "max_new_positions": 2,
    },
    "DEFENSIVE": {
        "max_total_exposure_pct": 20.0,
        "max_alt_exposure_pct": 5.0,
        "max_single_position_pct": 3.0,
        "risk_per_trade_pct": 0.25,
        "daily_loss_limit_pct": 0.75,
        "max_new_positions": 1,
    },
    "LOCKDOWN": {
        "max_total_exposure_pct": 0.0,
        "max_alt_exposure_pct": 0.0,
        "max_single_position_pct": 0.0,
        "risk_per_trade_pct": 0.0,
        "daily_loss_limit_pct": 0.0,
        "max_new_positions": 0,
    },
    "DATA_BLOCKED": {
        "max_total_exposure_pct": 0.0,
        "max_alt_exposure_pct": 0.0,
        "max_single_position_pct": 0.0,
        "risk_per_trade_pct": 0.0,
        "daily_loss_limit_pct": 0.0,
        "max_new_positions": 0,
    },
}


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def pct_change(new_value, old_value):
    if old_value <= 0:
        raise ValueError("yüzde değişim için başlangıç değeri pozitif olmalı")
    return (new_value / old_value - 1.0) * 100.0


def fetch_closed_1h_klines(symbol):
    response = requests.get(
        BINANCE_KLINES_URL,
        params={"symbol": symbol, "interval": "1h", "limit": KLINE_LIMIT},
        timeout=20,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"{symbol}: Binance kline yanıtı liste değil")

    now_ms = int(time.time() * 1000)
    closed = [row for row in payload if len(row) >= 8 and int(row[6]) < now_ms]
    if len(closed) < MIN_CLOSED_CANDLES:
        raise RuntimeError(
            f"{symbol}: risk hesabı için yetersiz kapalı mum "
            f"({len(closed)}/{MIN_CLOSED_CANDLES})"
        )
    return closed[-MIN_CLOSED_CANDLES:]


def candle_metrics(symbol, candles):
    if len(candles) < MIN_CLOSED_CANDLES:
        raise ValueError(f"{symbol}: {MIN_CLOSED_CANDLES} kapalı mum gerekli")

    closes = [float(row[4]) for row in candles]
    if any(value <= 0 for value in closes):
        raise ValueError(f"{symbol}: sıfır/negatif kapanış bulundu")

    last_price = closes[-1]
    return_24h = pct_change(last_price, closes[-25])
    recent_24 = candles[-24:]
    high_24h = max(float(row[2]) for row in recent_24)
    low_24h = min(float(row[3]) for row in recent_24)
    if low_24h <= 0:
        raise ValueError(f"{symbol}: geçersiz 24h düşük fiyat")
    range_24h = (high_24h / low_24h - 1.0) * 100.0

    hourly_returns = [
        pct_change(closes[index], closes[index - 1])
        for index in range(len(closes) - 24, len(closes))
    ]
    hourly_volatility = statistics.pstdev(hourly_returns)
    peak_7d = max(closes[-168:])
    drawdown_7d = pct_change(last_price, peak_7d)
    quote_volume_24h = sum(float(row[7]) for row in recent_24)
    if quote_volume_24h < 0:
        raise ValueError(f"{symbol}: geçersiz quote volume")

    close_time = datetime.fromtimestamp(
        int(candles[-1][6]) / 1000.0, tz=timezone.utc
    )
    return {
        "symbol": symbol,
        "as_of": iso(close_time),
        "price": last_price,
        "return_24h_pct": return_24h,
        "range_24h_pct": range_24h,
        "hourly_volatility_pct": hourly_volatility,
        "drawdown_7d_pct": drawdown_7d,
        "quote_volume_24h": quote_volume_24h,
    }


def threshold_score(value, thresholds):
    """Return the first matching severity score from descending thresholds."""
    for boundary, points in thresholds:
        if value >= boundary:
            return points
    return 0


def classify_asset_risk(metrics):
    reasons = []
    score = 0

    abs_return = abs(float(metrics["return_24h_pct"]))
    points = threshold_score(abs_return, ((15.0, 3), (8.0, 2), (4.0, 1)))
    if points:
        score += points
        reasons.append(f"ABS_RETURN_24H_{points}")

    range_24h = float(metrics["range_24h_pct"])
    points = threshold_score(range_24h, ((25.0, 3), (15.0, 2), (8.0, 1)))
    if points:
        score += points
        reasons.append(f"RANGE_24H_{points}")

    volatility = float(metrics["hourly_volatility_pct"])
    points = threshold_score(volatility, ((5.0, 3), (3.0, 2), (1.5, 1)))
    if points:
        score += points
        reasons.append(f"HOURLY_VOL_24H_{points}")

    drawdown = -float(metrics["drawdown_7d_pct"])
    points = threshold_score(drawdown, ((35.0, 3), (25.0, 2), (15.0, 1)))
    if points:
        score += points
        reasons.append(f"DRAWDOWN_7D_{points}")

    quote_volume = float(metrics["quote_volume_24h"])
    if quote_volume < 2_000_000:
        score += 3
        reasons.append("LIQUIDITY_UNDER_2M")
    elif quote_volume < 10_000_000:
        score += 2
        reasons.append("LIQUIDITY_UNDER_10M")
    elif quote_volume < 50_000_000:
        score += 1
        reasons.append("LIQUIDITY_UNDER_50M")

    hard_block = (
        quote_volume < 2_000_000
        or range_24h >= 30.0
        or volatility >= 7.0
    )
    if hard_block:
        tier = "EXTREME"
        reasons.append("HARD_BLOCK")
    elif score >= 10:
        tier = "EXTREME"
    elif score >= 7:
        tier = "HIGH"
    elif score >= 4:
        tier = "ELEVATED"
    else:
        tier = "NORMAL"

    symbol = metrics["symbol"]
    base_cap = BASE_POSITION_CAPS.get(symbol, DEFAULT_ALT_POSITION_CAP)
    max_position = base_cap * TIER_MULTIPLIERS[tier]
    volatility_stop = max(2.0, min(12.0, volatility * 2.5))

    result = dict(metrics)
    result.update({
        "risk_score": score,
        "risk_tier": tier,
        "base_position_cap_pct": base_cap,
        "pre_global_max_position_pct": round(max_position, 2),
        "max_position_pct": round(max_position, 2),
        "volatility_stop_reference_pct": round(volatility_stop, 2),
        "new_position_allowed": tier != "EXTREME",
        "risk_reasons": reasons or ["NO_ELEVATED_RISK_TRIGGER"],
    })
    return result


def build_global_policy(assets, source_errors, created_at=None):
    created_at = created_at or iso(utcnow())
    by_symbol = {asset["symbol"]: asset for asset in assets}
    coverage_count = len(by_symbol)
    coverage_total = len(SYMBOLS)
    coverage_ratio = coverage_count / coverage_total
    reasons = []

    if "BTCUSDT" not in by_symbol:
        state = "DATA_BLOCKED"
        reasons.append("BTC_REFERENCE_MISSING")
    elif coverage_ratio < MIN_COVERAGE_RATIO:
        state = "DATA_BLOCKED"
        reasons.append("COVERAGE_BELOW_80_PERCENT")
    else:
        btc = by_symbol["BTCUSDT"]
        risky_count = sum(
            asset["risk_tier"] in {"HIGH", "EXTREME"}
            for asset in assets
        )
        risky_ratio = risky_count / coverage_count
        btc_return = float(btc["return_24h_pct"])
        btc_range = float(btc["range_24h_pct"])
        btc_drawdown = float(btc["drawdown_7d_pct"])

        if (
            btc_return <= -12.0
            or btc_range >= 20.0
            or btc_drawdown <= -25.0
            or risky_ratio >= 0.75
        ):
            state = "LOCKDOWN"
            reasons.append("MARKET_TAIL_RISK")
        elif (
            btc_return <= -6.0
            or abs(btc_return) >= 10.0
            or btc_range >= 12.0
            or btc_drawdown <= -15.0
            or risky_ratio >= 0.50
        ):
            state = "DEFENSIVE"
            reasons.append("MARKET_STRESS")
        elif (
            abs(btc_return) >= 4.0
            or btc_range >= 8.0
            or btc_drawdown <= -8.0
            or risky_ratio >= 0.25
            or source_errors
        ):
            state = "CAUTION"
            reasons.append("ELEVATED_MARKET_RISK")
        else:
            state = "NORMAL"
            reasons.append("NO_GLOBAL_STRESS_TRIGGER")

    if source_errors:
        reasons.append("PARTIAL_SOURCE_ERRORS")

    limits = dict(GLOBAL_LIMITS[state])
    new_positions_allowed = state not in {"LOCKDOWN", "DATA_BLOCKED"}
    global_cap = limits["max_single_position_pct"]
    for asset in assets:
        asset["max_position_pct"] = round(
            min(float(asset["pre_global_max_position_pct"]), global_cap), 2
        )
        asset["new_position_allowed"] = bool(
            new_positions_allowed
            and asset["risk_tier"] != "EXTREME"
            and asset["max_position_pct"] > 0
        )

    return {
        "created_at": created_at,
        "policy_version": POLICY_VERSION,
        "market_state": state,
        "coverage_count": coverage_count,
        "coverage_total": coverage_total,
        "coverage_ratio": coverage_ratio,
        "new_positions_allowed": new_positions_allowed,
        "policy_reasons": reasons,
        **limits,
    }


def ensure_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS risk_policy_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            market_state TEXT NOT NULL,
            coverage_count INTEGER NOT NULL,
            coverage_total INTEGER NOT NULL,
            max_total_exposure_pct REAL NOT NULL,
            max_alt_exposure_pct REAL NOT NULL,
            max_single_position_pct REAL NOT NULL,
            risk_per_trade_pct REAL NOT NULL,
            daily_loss_limit_pct REAL NOT NULL,
            max_new_positions INTEGER NOT NULL,
            new_positions_allowed INTEGER NOT NULL,
            reason_json TEXT NOT NULL,
            detail_json TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS asset_risk_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_snapshot_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            as_of TEXT NOT NULL,
            price REAL NOT NULL,
            return_24h_pct REAL NOT NULL,
            range_24h_pct REAL NOT NULL,
            hourly_volatility_pct REAL NOT NULL,
            drawdown_7d_pct REAL NOT NULL,
            quote_volume_24h REAL NOT NULL,
            risk_score INTEGER NOT NULL,
            risk_tier TEXT NOT NULL,
            max_position_pct REAL NOT NULL,
            volatility_stop_reference_pct REAL NOT NULL,
            new_position_allowed INTEGER NOT NULL,
            reason_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(policy_snapshot_id, symbol),
            FOREIGN KEY(policy_snapshot_id) REFERENCES risk_policy_snapshots(id)
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS ix_asset_risk_symbol_time
        ON asset_risk_snapshots(symbol, created_at)
    """)
    con.commit()


def save_snapshot(con, policy, assets, source_errors):
    detail = dict(policy)
    detail["assets"] = assets
    detail["source_errors"] = source_errors
    cursor = con.execute("""
        INSERT INTO risk_policy_snapshots (
            created_at, policy_version, market_state, coverage_count,
            coverage_total, max_total_exposure_pct, max_alt_exposure_pct,
            max_single_position_pct, risk_per_trade_pct,
            daily_loss_limit_pct, max_new_positions, new_positions_allowed,
            reason_json, detail_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        policy["created_at"],
        policy["policy_version"],
        policy["market_state"],
        policy["coverage_count"],
        policy["coverage_total"],
        policy["max_total_exposure_pct"],
        policy["max_alt_exposure_pct"],
        policy["max_single_position_pct"],
        policy["risk_per_trade_pct"],
        policy["daily_loss_limit_pct"],
        policy["max_new_positions"],
        int(policy["new_positions_allowed"]),
        json.dumps(policy["policy_reasons"], ensure_ascii=False),
        json.dumps(detail, ensure_ascii=False),
    ))
    snapshot_id = int(cursor.lastrowid)

    for asset in assets:
        con.execute("""
            INSERT INTO asset_risk_snapshots (
                policy_snapshot_id, symbol, as_of, price, return_24h_pct,
                range_24h_pct, hourly_volatility_pct, drawdown_7d_pct,
                quote_volume_24h, risk_score, risk_tier, max_position_pct,
                volatility_stop_reference_pct, new_position_allowed,
                reason_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            snapshot_id,
            asset["symbol"],
            asset["as_of"],
            asset["price"],
            asset["return_24h_pct"],
            asset["range_24h_pct"],
            asset["hourly_volatility_pct"],
            asset["drawdown_7d_pct"],
            asset["quote_volume_24h"],
            asset["risk_score"],
            asset["risk_tier"],
            asset["max_position_pct"],
            asset["volatility_stop_reference_pct"],
            int(asset["new_position_allowed"]),
            json.dumps(asset["risk_reasons"], ensure_ascii=False),
            policy["created_at"],
        ))
    con.commit()
    return snapshot_id


def render_result(policy, assets, source_errors, snapshot_id):
    print("AG Crypto Analyst - Risk Manager v1.0.0")
    print(
        f"Policy: {policy['policy_version']} | durum: {policy['market_state']} | "
        f"kapsam: {policy['coverage_count']}/{policy['coverage_total']}"
    )
    print(
        f"Limitler: toplam=%{policy['max_total_exposure_pct']:.1f} | "
        f"alt=%{policy['max_alt_exposure_pct']:.1f} | "
        f"tek pozisyon=%{policy['max_single_position_pct']:.1f} | "
        f"işlem riski=%{policy['risk_per_trade_pct']:.2f} | "
        f"günlük kayıp=%{policy['daily_loss_limit_pct']:.2f} | "
        f"yeni pozisyon={policy['max_new_positions']}"
    )
    print("SYMBOL       24H%   RANGE%   VOL%    DD7D%       QVOL$  TIER       CAP%  NEW")
    for asset in sorted(assets, key=lambda item: SYMBOLS.index(item["symbol"])):
        print(
            f"{asset['symbol']:<11} "
            f"{asset['return_24h_pct']:>+6.2f} "
            f"{asset['range_24h_pct']:>8.2f} "
            f"{asset['hourly_volatility_pct']:>6.2f} "
            f"{asset['drawdown_7d_pct']:>+8.2f} "
            f"{asset['quote_volume_24h'] / 1_000_000:>10.1f}M "
            f"{asset['risk_tier']:<9} "
            f"{asset['max_position_pct']:>5.2f}  "
            f"{'YES' if asset['new_position_allowed'] else 'NO'}"
        )
    if source_errors:
        print("Kaynak hataları:")
        for symbol, message in sorted(source_errors.items()):
            print(f"- {symbol}: {message}")
    print(f"Snapshot: {snapshot_id}")
    print(
        "Not: Bu çıktı sinyal üretmez; yalnızca ilerideki kararların aşamayacağı "
        "risk tavanlarını tanımlar. QVOL emir defteri derinliği değil, 24h spot "
        "hacim vekilidir. Eksik veri riski gevşetmez."
    )


def main():
    created_at = iso(utcnow())
    assets = []
    source_errors = {}
    for symbol in SYMBOLS:
        try:
            candles = fetch_closed_1h_klines(symbol)
            metrics = candle_metrics(symbol, candles)
            assets.append(classify_asset_risk(metrics))
        except Exception as exc:
            source_errors[symbol] = str(exc)

    policy = build_global_policy(assets, source_errors, created_at)
    con = sqlite3.connect(DB_NAME)
    try:
        ensure_tables(con)
        snapshot_id = save_snapshot(con, policy, assets, source_errors)
    finally:
        con.close()

    render_result(policy, assets, source_errors, snapshot_id)
    result = dict(policy)
    result.update({
        "snapshot_id": snapshot_id,
        "assets": assets,
        "source_errors": source_errors,
    })
    return result


if __name__ == "__main__":
    main()
