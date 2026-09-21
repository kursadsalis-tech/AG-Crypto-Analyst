"""Produce conservative market and asset risk limits from closed 1h candles.

This module does not create trade signals. It measures observable volatility,
drawdown, range and liquidity, then publishes caps that a later Decision Engine
must obey. Missing data tightens or blocks risk; it never increases permission.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from database.runtime_store import connect as runtime_connect
import statistics
import time
from datetime import datetime, timezone

import requests
from data.truth_engine import (
    audited_validation,
    fetch_and_validate_btc_anchor,
    validate_closed_klines,
)


DB_NAME = "crypto_data.db"
BINANCE_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
BINANCE_USD_M_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
OKX_CANDLES_URL = "https://www.okx.com/api/v5/market/candles"
USER_AGENT = "AG-Crypto-Analyst/1.0.0"
POLICY_VERSION = "MARKET_RISK_POLICY_V3_CONTEXT_SIZES_PAPER_RISK"
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
BEHAVIOR_UNIVERSE_MAX_AGE_SECONDS = 600

BASE_POSITION_CAPS = {
    "BTCUSDT": 15.0,
    "ETHUSDT": 10.0,
}
DEFAULT_ALT_POSITION_CAP = 5.0

TIER_MULTIPLIERS = {
    "NORMAL": 1.00,
    "ELEVATED": 0.60,
    "HIGH": 0.25,
    # Valid, liquid but extreme context is still testable in PAPER at a tiny
    # size. Extreme market observations are not execution impossibilities.
    "EXTREME": 0.05,
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
        # In PAPER this is a stress-experiment envelope, not a learning ban.
        # Real-order authority is controlled separately and remains off.
        "max_total_exposure_pct": 2.0,
        "max_alt_exposure_pct": 1.0,
        "max_single_position_pct": 0.25,
        "risk_per_trade_pct": 0.05,
        "daily_loss_limit_pct": 0.25,
        "max_new_positions": 1,
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


def behavior_risk_symbols(con, now=None):
    """Return the latest eligible dynamic behavior universe, fail-closed on age."""
    now = now or utcnow()
    if not con.execute("""SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='market_behavior_minute_features'""").fetchone():
        return ()
    latest = con.execute("""SELECT MAX(minute_end_timestamp_ms)
        FROM market_behavior_minute_features WHERE eligible_for_learning=1""").fetchone()[0]
    if latest is None or now.timestamp() * 1000 - int(latest) > (
        BEHAVIOR_UNIVERSE_MAX_AGE_SECONDS * 1000
    ):
        return ()
    instruments = con.execute("""SELECT DISTINCT instrument
        FROM market_behavior_minute_features
        WHERE minute_end_timestamp_ms=? AND eligible_for_learning=1
        ORDER BY instrument""", (latest,)).fetchall()
    symbols = []
    for (instrument,) in instruments:
        value = str(instrument).upper()
        # Binance-native minute rows keep the exact exchangeInfo symbol.
        # Historical OKX rows are NOT eligible to authorise Binance risk
        # coverage by guessing another venue's contract identity.
        if value.endswith("USDT") and "-" not in value and value not in symbols:
            symbols.append(value)
    return tuple(symbols)


def pct_change(new_value, old_value):
    if old_value <= 0:
        raise ValueError("yüzde değişim için başlangıç değeri pozitif olmalı")
    return (new_value / old_value - 1.0) * 100.0


def fetch_closed_1h_klines(
    symbol,
    url=BINANCE_KLINES_URL,
    source_prefix="BINANCE_SPOT_KLINES",
):
    response = requests.get(
        url,
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
    selected = closed[-MIN_CLOSED_CANDLES:]
    return validate_closed_klines(
        selected,
        source=f"{source_prefix}:{symbol}",
        minimum_rows=MIN_CLOSED_CANDLES,
    )


def _okx_swap_instrument(symbol):
    value = str(symbol).upper()
    if not value.endswith("USDT") or len(value) <= 4:
        raise ValueError(f"{symbol}: unsupported USDT swap symbol")
    return f"{value[:-4]}-USDT-SWAP"


def fetch_okx_closed_1h_klines(symbol):
    response = requests.get(
        OKX_CANDLES_URL,
        params={
            "instId": _okx_swap_instrument(symbol),
            "bar": "1H",
            "limit": KLINE_LIMIT,
        },
        timeout=20,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or str(payload.get("code")) != "0":
        raise RuntimeError(f"{symbol}: OKX swap candle response failed")
    rows = []
    for row in reversed(payload.get("data") or []):
        if not isinstance(row, list) or len(row) < 9 or str(row[8]) != "1":
            continue
        opened = int(row[0])
        rows.append([
            opened, row[1], row[2], row[3], row[4], row[5],
            opened + 3_600_000 - 1, row[7], 0, 0, 0, 0,
        ])
    if len(rows) < MIN_CLOSED_CANDLES:
        raise RuntimeError(
            f"{symbol}: OKX risk calculation has insufficient closed candles "
            f"({len(rows)}/{MIN_CLOSED_CANDLES})"
        )
    return validate_closed_klines(
        rows[-MIN_CLOSED_CANDLES:],
        source=f"OKX_SWAP_KLINES:{symbol}",
        minimum_rows=MIN_CLOSED_CANDLES,
    )


def fetch_dynamic_closed_1h_klines(symbol):
    """Same-venue risk candles through AGCA's existing private DB egress.

    Actual GCE VM rejects direct fapi REST with HTTP 451; never mask its
    failure with OKX candles or the dissimilar Binance spot contract. Only
    recent CLOSED Binance USD-M 1h rows authorise dynamic risk coverage.
    """
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{1,26}USDT", symbol):
        raise RuntimeError("INVALID_BINANCE_USD_M_RISK_SYMBOL")
    database_url = os.getenv("AGCA_REMOTE_DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("BINANCE_USD_M_PRIVATE_EGRESS_UNAVAILABLE")
    from urllib.parse import urlencode
    url = BINANCE_USD_M_KLINES_URL + "?" + urlencode({
        "symbol": symbol, "interval": "1h", "limit": KLINE_LIMIT,
    })
    try:
        import psycopg
        with psycopg.connect(database_url, connect_timeout=5) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '20000ms'")
                cursor.execute(
                    "SELECT extensions.http_set_curlopt('CURLOPT_TIMEOUT_MS', %s)",
                    ("15000",),
                )
                cursor.execute(
                    "SELECT status, content_type, content FROM extensions.http_get(%s)",
                    (url,),
                )
                result = cursor.fetchone()
    except Exception as exc:
        raise RuntimeError("BINANCE_USD_M_KLINES_PRIVATE_EGRESS_ERROR") from exc
    if (not result or result[0] != 200 or
            str(result[1] or "").split(";", 1)[0].lower() != "application/json"):
        raise RuntimeError("BINANCE_USD_M_KLINES_HTTP_UNAVAILABLE")
    try:
        rows = json.loads(result[2])
        if not isinstance(rows, list):
            raise ValueError("expected kline array")
        now_ms = int(time.time()*1000)
        closed = [row for row in rows if isinstance(row, list)
                  and len(row) >= 8 and int(row[6]) < now_ms]
    except (ValueError, TypeError, IndexError, OverflowError) as exc:
        raise RuntimeError("BINANCE_USD_M_KLINES_INVALID_RESPONSE") from exc
    if len(closed) < MIN_CLOSED_CANDLES:
        raise RuntimeError("BINANCE_USD_M_KLINES_INSUFFICIENT_CLOSED_CANDLES")
    # Validate source and close-clock semantics; no symbol aliases.
    return validate_closed_klines(
        closed[-MIN_CLOSED_CANDLES:],
        source=f"BINANCE_USD_M_KLINES_PRIVATE_EGRESS:{symbol}",
        minimum_rows=MIN_CLOSED_CANDLES,
    )


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
    market_score = 0
    execution_score = 0

    abs_return = abs(float(metrics["return_24h_pct"]))
    points = threshold_score(abs_return, ((15.0, 3), (8.0, 2), (4.0, 1)))
    if points:
        market_score += points
        reasons.append(f"ABS_RETURN_24H_{points}")

    range_24h = float(metrics["range_24h_pct"])
    points = threshold_score(range_24h, ((25.0, 3), (15.0, 2), (8.0, 1)))
    if points:
        market_score += points
        reasons.append(f"RANGE_24H_{points}")

    volatility = float(metrics["hourly_volatility_pct"])
    points = threshold_score(volatility, ((5.0, 3), (3.0, 2), (1.5, 1)))
    if points:
        market_score += points
        reasons.append(f"HOURLY_VOL_24H_{points}")

    drawdown = -float(metrics["drawdown_7d_pct"])
    points = threshold_score(drawdown, ((35.0, 3), (25.0, 2), (15.0, 1)))
    if points:
        market_score += points
        reasons.append(f"DRAWDOWN_7D_{points}")

    quote_volume = float(metrics["quote_volume_24h"])
    if quote_volume < 2_000_000:
        execution_score += 3
        reasons.append("LIQUIDITY_UNDER_2M")
    elif quote_volume < 10_000_000:
        execution_score += 2
        reasons.append("LIQUIDITY_UNDER_10M")
    elif quote_volume < 50_000_000:
        execution_score += 1
        reasons.append("LIQUIDITY_UNDER_50M")

    liquidity_hard_block = quote_volume < 2_000_000
    market_hard_block = range_24h >= 30.0 or volatility >= 7.0
    # Only execution feasibility is an asset-level veto. Volatility/range are
    # contextual sizing inputs; the behavior core owns opportunity selection.
    hard_block = liquidity_hard_block
    score = market_score + execution_score
    if liquidity_hard_block:
        tier = "EXTREME"
        reasons.append("LIQUIDITY_HARD_BLOCK")
    elif market_hard_block or score >= 10:
        tier = "EXTREME"
        if market_hard_block:
            reasons.append("EXTREME_MARKET_CONTEXT_SIZE_ONLY")
    elif score >= 7:
        tier = "HIGH"
    elif score >= 4:
        tier = "ELEVATED"
    else:
        tier = "NORMAL"

    symbol = metrics["symbol"]
    base_cap = BASE_POSITION_CAPS.get(symbol, DEFAULT_ALT_POSITION_CAP)
    max_position = base_cap * TIER_MULTIPLIERS[tier]
    if liquidity_hard_block:
        max_position = 0.0
    volatility_stop = max(2.0, min(12.0, volatility * 2.5))

    result = dict(metrics)
    result.update({
        "risk_score": score,
        "market_risk_score": market_score,
        "execution_risk_score": execution_score,
        "risk_tier": tier,
        "hard_block": hard_block,
        "liquidity_hard_block": liquidity_hard_block,
        "market_hard_block": market_hard_block,
        "base_position_cap_pct": base_cap,
        "pre_global_max_position_pct": round(max_position, 2),
        "max_position_pct": round(max_position, 2),
        "volatility_stop_reference_pct": round(volatility_stop, 2),
        "new_position_allowed": not liquidity_hard_block,
        "risk_reasons": reasons or ["NO_ELEVATED_RISK_TRIGGER"],
    })
    return result


def reference_stress_level(asset):
    ret = float(asset["return_24h_pct"])
    price_range = float(asset["range_24h_pct"])
    drawdown = float(asset["drawdown_7d_pct"])
    if ret <= -12.0 or price_range >= 20.0 or drawdown <= -25.0:
        return "TAIL"
    if ret <= -6.0 or abs(ret) >= 10.0 or price_range >= 12.0 or drawdown <= -15.0:
        return "STRESS"
    if abs(ret) >= 4.0 or price_range >= 8.0 or drawdown <= -8.0:
        return "CAUTION"
    return "NORMAL"


def build_global_policy(assets, source_errors, created_at=None):
    created_at = created_at or iso(utcnow())
    by_symbol = {asset["symbol"]: asset for asset in assets}
    reference_assets = [asset for asset in assets if asset["symbol"] in SYMBOLS]
    reference_by_symbol = {asset["symbol"]: asset for asset in reference_assets}
    reference_errors = {
        key: value for key, value in source_errors.items()
        if key == "CROSS_SOURCE_BTC" or key in SYMBOLS
    }
    coverage_count = len(reference_by_symbol)
    coverage_total = len(SYMBOLS)
    coverage_ratio = coverage_count / coverage_total
    market_stress_count = sum(
        asset["market_risk_score"] >= 4 or asset["market_hard_block"]
        for asset in reference_assets
    )
    market_stress_ratio = (
        market_stress_count / coverage_count if coverage_count else 0.0
    )
    asset_block_count = sum(asset["hard_block"] for asset in assets)
    liquidity_block_count = sum(
        asset["liquidity_hard_block"] for asset in assets
    )
    reasons = []

    if "CROSS_SOURCE_BTC" in reference_errors:
        state = "DATA_BLOCKED"
        reasons.append("BTC_CROSS_SOURCE_VALIDATION_FAILED")
    elif "BTCUSDT" not in reference_by_symbol:
        state = "DATA_BLOCKED"
        reasons.append("BTC_REFERENCE_MISSING")
    elif coverage_ratio < MIN_COVERAGE_RATIO:
        state = "DATA_BLOCKED"
        reasons.append("COVERAGE_BELOW_80_PERCENT")
    else:
        btc = reference_by_symbol["BTCUSDT"]
        eth = reference_by_symbol.get("ETHUSDT")
        btc_level = reference_stress_level(btc)
        eth_level = reference_stress_level(eth) if eth else "MISSING"

        if (
            btc_level == "TAIL"
            or market_stress_ratio >= 0.75
        ):
            state = "LOCKDOWN"
            reasons.append("MARKET_TAIL_RISK")
        elif (
            btc_level == "STRESS"
            or eth_level == "TAIL"
            or market_stress_ratio >= 0.50
        ):
            state = "DEFENSIVE"
            reasons.append("MARKET_STRESS")
        elif (
            btc_level == "CAUTION"
            or eth_level in {"STRESS", "CAUTION"}
            or market_stress_ratio >= 0.25
            or reference_errors
        ):
            state = "CAUTION"
            reasons.append("ELEVATED_MARKET_RISK")
        else:
            state = "NORMAL"
            reasons.append("NO_GLOBAL_STRESS_TRIGGER")

    if reference_errors:
        reasons.append("PARTIAL_SOURCE_ERRORS")
    if asset_block_count:
        reasons.append(f"ASSET_BLOCKS_{asset_block_count}")

    limits = dict(GLOBAL_LIMITS[state])
    new_positions_allowed = state != "DATA_BLOCKED"
    global_cap = limits["max_single_position_pct"]
    for asset in assets:
        asset["max_position_pct"] = round(
            min(float(asset["pre_global_max_position_pct"]), global_cap), 2
        )
        asset["new_position_allowed"] = bool(
            new_positions_allowed
            and not asset["liquidity_hard_block"]
            and asset["max_position_pct"] > 0
        )

    return {
        "created_at": created_at,
        "policy_version": POLICY_VERSION,
        "market_state": state,
        "coverage_count": coverage_count,
        "coverage_total": coverage_total,
        "coverage_ratio": coverage_ratio,
        "market_stress_count": market_stress_count,
        "market_stress_ratio": market_stress_ratio,
        "asset_block_count": asset_block_count,
        "liquidity_block_count": liquidity_block_count,
        "new_positions_allowed": new_positions_allowed,
        "policy_reasons": reasons,
        "dynamic_behavior_symbol_count": sum(
            asset["symbol"] not in SYMBOLS for asset in assets
        ),
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
        f"Dinamik piyasa stresi: {policy['market_stress_count']}/"
        f"{policy['coverage_count']} | coin bazlı blok: "
        f"{policy['asset_block_count']} | düşük hacim bloğu: "
        f"{policy['liquidity_block_count']}"
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
    order = {symbol: index for index, symbol in enumerate(SYMBOLS)}
    for asset in sorted(
        assets, key=lambda item: (order.get(item["symbol"], len(order)), item["symbol"])
    ):
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
    con = runtime_connect(DB_NAME)
    try:
        dynamic_symbols = behavior_risk_symbols(con)
        try:
            audited_validation(
                con,
                gate_name="RISK_REFERENCE_PRICE",
                source="BINANCE_VS_COINGECKO:BTCUSD",
                validator=fetch_and_validate_btc_anchor,
            )
        except Exception as exc:
            source_errors["CROSS_SOURCE_BTC"] = str(exc)
    finally:
        con.close()
    requested_symbols = tuple(dict.fromkeys((*SYMBOLS, *dynamic_symbols)))
    for symbol in requested_symbols:
        try:
            candles = (
                fetch_dynamic_closed_1h_klines(symbol)
                if symbol in dynamic_symbols
                else fetch_closed_1h_klines(symbol)
            )
            metrics = candle_metrics(symbol, candles)
            assets.append(classify_asset_risk(metrics))
        except Exception as exc:
            source_errors[symbol] = str(exc)

    policy = build_global_policy(assets, source_errors, created_at)
    con = runtime_connect(DB_NAME)
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
        "requested_symbols": requested_symbols,
    })
    return result


if __name__ == "__main__":
    main()
