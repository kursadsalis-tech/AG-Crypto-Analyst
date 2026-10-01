"""Bounded, local-only symbol risk evidence; no trade/exit or network authority.

The global 15-minute policy remains authoritative. This support producer fills
only missing dynamic-symbol coverage using 360 exact closed USD-M minute bars.
The shorter measured window is explicit: no 24-hour volume or seven-day risk
statistic is extrapolated or fabricated. Full historical risk takes precedence.
"""
from __future__ import annotations

import json
import math
import sqlite3
from database.sqlite_policy import connect_local, is_sqlite_busy
import statistics
import time
from datetime import datetime, timezone, timedelta

from database.technical_bar_store import load_recent
from data.binance_technical_bars import SOURCE_ARCHIVE, SOURCE_WS
from data.truth_engine import validate_closed_klines

CONTRACT_VERSION = "LOCAL_SYMBOL_RISK_V1_360_CLOSED_1M"
TABLE = "local_symbol_risk_evidence"
BAR_COUNT = 360
MAX_BAR_AGE_SECONDS = 180
MAX_EVIDENCE_AGE_SECONDS = 120
MAX_POLICY_AGE_SECONDS = 1800
MAX_SYMBOLS_PER_REFRESH = 12
MAX_REFRESH_SECONDS = 2.0
REFRESH_INTERVAL_SECONDS = 15
# <=30% of the ordinary 5% alt cap while long-window risk is unavailable.
MAX_LOCAL_CAP_PCT = 1.5
MIN_OBSERVED_QUOTE_VOLUME = 500_000.0


def _time(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _table_exists(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def latest_policy_limits(con, now=None):
    """Return actual fresh global caps, or None; never invent a risk allowance."""
    now = now or datetime.now(timezone.utc)
    if not _table_exists(con, "risk_policy_snapshots"):
        return None
    con_columns = {row[1] for row in con.execute("PRAGMA table_info(risk_policy_snapshots)")}
    columns = ("id", "created_at", "new_positions_allowed", "max_total_exposure_pct",
               "max_alt_exposure_pct", "max_single_position_pct", "risk_per_trade_pct",
               "max_new_positions")
    if not set(columns).issubset(con_columns):
        return None
    row = con.execute(f"SELECT {','.join(columns)} FROM risk_policy_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return None
    policy = dict(zip(columns, row))
    if policy["new_positions_allowed"] not in (0, 1):
        return None
    stamp = _time(policy["created_at"])
    if stamp is None or not -60 <= (now - stamp).total_seconds() <= MAX_POLICY_AGE_SECONDS:
        return None
    try:
        for key in columns[3:]:
            policy[key] = float(policy[key])
            if not math.isfinite(policy[key]) or policy[key] < 0:
                return None
        if policy["max_new_positions"] != int(policy["max_new_positions"]):
            return None
    except (ValueError, TypeError, OverflowError):
        return None
    policy["max_new_positions"] = int(policy["max_new_positions"])
    return policy


def bootstrap_schema(database_path):
    with connect_local(database_path, timeout=1.0) as con:
        con.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE}(
            policy_snapshot_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            evidence_json TEXT,
            real_order_allowed INTEGER NOT NULL DEFAULT 0 CHECK(real_order_allowed=0),
            PRIMARY KEY(policy_snapshot_id,symbol)
        )""")


def measured_risk(symbol, bars, policy, now):
    if any(row.get("source") not in {SOURCE_WS, SOURCE_ARCHIVE} for row in bars):
        raise ValueError("NON_CANONICAL_LOCAL_RISK_SOURCE")
    candles = [[bar["open_time_ms"], bar["open"], bar["high"], bar["low"],
                bar["close"], bar["volume"], bar["close_time_ms"], bar["quote_volume"]]
               for bar in bars]
    validate_closed_klines(candles, source=CONTRACT_VERSION, now=now,
                          minimum_rows=BAR_COUNT, interval=timedelta(minutes=1),
                          maximum_staleness=timedelta(seconds=MAX_BAR_AGE_SECONDS))
    if len(candles) != BAR_COUNT or any(row[6] - row[0] != 59_999 for row in candles):
        raise ValueError("LOCAL_RISK_WINDOW_INVALID")
    closes = [float(row[4]) for row in candles]
    returns = [(closes[i] / closes[i-1] - 1) * 100 for i in range(1, len(closes))]
    volatility = statistics.pstdev(returns)
    price_range = (max(row[2] for row in candles) / min(row[3] for row in candles) - 1) * 100
    drawdown = (closes[-1] / max(closes) - 1) * 100
    quote_volume = sum(float(row[7]) for row in candles)
    # Explicit short-window uncertainty reduction; volatile valid evidence sizes
    # down, while inadequate measured liquidity is an execution-feasibility veto.
    context_multiplier = min(1.0, 0.5 / max(volatility, 0.5), 10.0 / max(price_range, 10.0))
    cap = min(MAX_LOCAL_CAP_PCT * context_multiplier, policy["max_single_position_pct"])
    allowed = (bool(policy["new_positions_allowed"]) and quote_volume >= MIN_OBSERVED_QUOTE_VOLUME
               and cap > 0 and policy["risk_per_trade_pct"] > 0)
    reasons = ["BOUNDED_LOCAL_WINDOW_RISK", "LONG_WINDOW_UNAVAILABLE_SIZE_REDUCED"]
    if quote_volume < MIN_OBSERVED_QUOTE_VOLUME:
        cap = 0.0
        reasons.append("LOCAL_OBSERVED_LIQUIDITY_INSUFFICIENT")
    return {
        "contract_version": CONTRACT_VERSION, "symbol": symbol,
        "allowed": allowed, "cap_pct": round(cap, 6), "price": closes[-1],
        "risk_as_of": datetime.fromtimestamp(candles[-1][6] / 1000, timezone.utc).isoformat(),
        "generated_at": now.isoformat(), "policy_created_at": policy["created_at"],
        "policy_snapshot_id": int(policy["id"]),
        "risk_per_trade_pct": policy["risk_per_trade_pct"],
        "stop_reference_pct": min(5.0, max(0.5, volatility * 2.5)),
        "source": "BINANCE_USD_M_LOCAL_CLOSED_1M", "measured_window_minutes": BAR_COUNT,
        "measured_quote_volume": quote_volume, "measured_minute_volatility_pct": volatility,
        "measured_range_pct": price_range, "measured_drawdown_pct": drawdown,
        "long_window_metrics_available": False, "risk_reasons": reasons,
        "real_order_allowed": False,
    }


def load_local_risks(con, policy_id, now=None):
    now = now or datetime.now(timezone.utc)
    if not _table_exists(con, TABLE):
        return {}
    policy = latest_policy_limits(con, now)
    if not policy or int(policy["id"]) != int(policy_id) or not policy["new_positions_allowed"]:
        return {}
    rows = con.execute(f"""SELECT symbol,contract_version,evidence_json FROM {TABLE}
        WHERE policy_snapshot_id=? AND status='READY'""", (policy_id,)).fetchall()
    result = {}
    for symbol, version, payload in rows:
        if version != CONTRACT_VERSION:
            continue
        try:
            value = json.loads(payload)
            generated = _time(value.get("generated_at"))
            as_of = _time(value.get("risk_as_of"))
            if (value.get("contract_version") != CONTRACT_VERSION
                    or value.get("symbol") != symbol or value.get("policy_snapshot_id") != policy_id
                    or generated is None or as_of is None
                    or not 0 <= (now - generated).total_seconds() <= MAX_EVIDENCE_AGE_SECONDS
                    or not 0 <= (now - as_of).total_seconds() <= MAX_BAR_AGE_SECONDS
                    or value.get("real_order_allowed") is not False):
                continue
            if any(not math.isfinite(float(value[key])) or float(value[key]) <= 0
                   for key in ("price", "stop_reference_pct", "risk_per_trade_pct")):
                continue
            if (value.get("source") != "BINANCE_USD_M_LOCAL_CLOSED_1M"
                    or value.get("measured_window_minutes") != BAR_COUNT
                    or value.get("long_window_metrics_available") is not False
                    or not 0.5 <= float(value["stop_reference_pct"]) <= 5.0):
                continue
            measured = {key: float(value[key]) for key in (
                "measured_quote_volume", "measured_minute_volatility_pct",
                "measured_range_pct", "measured_drawdown_pct",
            )}
            if (any(not math.isfinite(number) for number in measured.values())
                    or any(measured[key] < 0 for key in (
                        "measured_quote_volume", "measured_minute_volatility_pct", "measured_range_pct"))
                    or (value.get("allowed") and measured["measured_quote_volume"] < MIN_OBSERVED_QUOTE_VOLUME)):
                continue
            cap = float(value["cap_pct"])
            if not math.isfinite(cap) or cap < 0 or not isinstance(value.get("allowed"), bool):
                continue
            value["cap_pct"] = min(cap, MAX_LOCAL_CAP_PCT, policy["max_single_position_pct"])
            value["risk_per_trade_pct"] = min(value["risk_per_trade_pct"], policy["risk_per_trade_pct"])
            result[symbol] = value
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            continue
    return result


def refresh_local_symbol_risk(database_path, *, now=None, max_symbols=MAX_SYMBOLS_PER_REFRESH,
                              max_seconds=MAX_REFRESH_SECONDS):
    """Off-loop support tick, bounded reads/writes with no network or trade calls.

    Schema is installed once at startup. Failed symbols rotate behind untried
    symbols; no one absent history can starve the rest of a dynamic universe.
    """
    now = now or datetime.now(timezone.utc)
    result = {"contract_version": CONTRACT_VERSION, "status": "READY", "attempted": 0,
              "ready": 0, "vetoed": 0, "errors": {}, "real_order_allowed": False}
    started = time.monotonic()
    deadline = started + max(0.05, min(float(max_seconds), MAX_REFRESH_SECONDS))
    con = connect_local(database_path, timeout=0.25)
    con.execute("PRAGMA busy_timeout=250")
    con.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        if not _table_exists(con, TABLE):
            result["status"] = "SCHEMA_UNAVAILABLE"
            return result
        policy = latest_policy_limits(con, now)
        if not policy:
            result["status"] = "GLOBAL_POLICY_UNAVAILABLE"
            return result
        if not policy["new_positions_allowed"]:
            result["status"] = "GLOBAL_POLICY_BLOCKED"
            return result
        if not _table_exists(con, "market_behavior_minute_features"):
            result["status"] = "NO_DYNAMIC_EVIDENCE"
            return result
        columns = {row[1] for row in con.execute("PRAGMA table_info(market_behavior_minute_features)")}
        if "market_data_provider" not in columns:
            result["status"] = "NON_CANONICAL_BEHAVIOR_SCHEMA"
            return result
        now_ms = int(now.timestamp() * 1000)
        # The same 180-second evidence window as the behavior core. Reference
        # membership never defines dynamic eligibility.
        instruments = con.execute("""SELECT instrument,MAX(minute_end_timestamp_ms)
            FROM market_behavior_minute_features
            WHERE minute_end_timestamp_ms BETWEEN ? AND ?
              AND market_data_provider='BINANCE' AND eligible_for_learning=1
            GROUP BY instrument""", (now_ms - 180_000, now_ms)).fetchall()
        full = {row[0] for row in con.execute("SELECT symbol FROM asset_risk_snapshots WHERE policy_snapshot_id=?", (policy["id"],))}
        prior = {row[0]: row[1] for row in con.execute(f"SELECT symbol,attempted_at FROM {TABLE} WHERE policy_snapshot_id=?", (policy["id"],))}
        symbols = []
        for instrument, latest in instruments:
            if not str(instrument).endswith("-USDT-SWAP"):
                continue
            symbol = str(instrument)[:-10].replace("-", "").upper() + "USDT"
            attempted = _time(prior.get(symbol))
            if symbol in full or (attempted and (now-attempted).total_seconds() < REFRESH_INTERVAL_SECONDS):
                continue
            symbols.append((prior.get(symbol, ""), -int(latest), symbol))
        symbols.sort()
        result["needed"] = len(symbols)
        for _, _, symbol in symbols[:max(1, min(int(max_symbols), MAX_SYMBOLS_PER_REFRESH))]:
            if time.monotonic() >= deadline:
                result["status"] = "BUDGET_EXHAUSTED"
                break
            result["attempted"] += 1
            error, evidence = None, None
            try:
                bars = load_recent(con, symbol, limit=BAR_COUNT, before_ms=now_ms-1)
                evidence = measured_risk(symbol, bars, policy, now)
                result["ready"] += int(evidence["allowed"])
                result["vetoed"] += int(not evidence["allowed"])
            except (ValueError, KeyError, TypeError) as exc:
                error = f"{type(exc).__name__}:{exc}"[:350]
                result["errors"][symbol] = error
            con.execute(f"""INSERT INTO {TABLE}(policy_snapshot_id,symbol,contract_version,
                attempted_at,status,error,evidence_json,real_order_allowed) VALUES(?,?,?,?,?,?,?,0)
                ON CONFLICT(policy_snapshot_id,symbol) DO UPDATE SET
                contract_version=excluded.contract_version,attempted_at=excluded.attempted_at,
                status=excluded.status,error=excluded.error,evidence_json=excluded.evidence_json""",
                (policy["id"], symbol, CONTRACT_VERSION, now.isoformat(),
                 "READY" if evidence else "EVIDENCE_UNAVAILABLE", error,
                 json.dumps(evidence, sort_keys=True) if evidence else None))
            con.commit()
        if result["errors"]:
            result["status"] = "PARTIAL"
    except sqlite3.OperationalError as exc:
        con.rollback()
        interrupted = getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_INTERRUPT
        if not is_sqlite_busy(exc) and not (interrupted and time.monotonic() >= deadline):
            raise
        result["status"] = "BUDGET_EXHAUSTED" if interrupted else "SQLITE_UNAVAILABLE"
        result["errors"]["LOCAL_STORE"] = str(exc)[:200]
    finally:
        con.close()
        result["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
    return result
