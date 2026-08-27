"""Point-in-time spot order-flow observations from closed Binance candles."""
from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timezone


WINDOW_HOURS = 24
MAX_AGE_MINUTES = 90.0


def parse_time(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def analyze_symbol(con: sqlite3.Connection, symbol: str, now: datetime):
    columns = {row[1] for row in con.execute("PRAGMA table_info(market_klines)")}
    if "taker_buy_quote_volume" not in columns:
        return {"state": "DATA_MISSING", "reason": "TAKER_FLOW_COLUMN_MISSING"}
    rows = con.execute("""
        SELECT close_time,open,close,quote_volume,taker_buy_quote_volume
        FROM market_klines
        WHERE symbol=? AND interval='1h' AND taker_buy_quote_volume IS NOT NULL
          AND close_time<=?
        ORDER BY close_time DESC LIMIT ?
    """, (symbol, now.astimezone(timezone.utc).isoformat(), WINDOW_HOURS)).fetchall()
    if not rows:
        return {"state": "DATA_MISSING", "reason": "NO_TAKER_FLOW_ROWS"}
    rows = list(reversed(rows))
    latest_at = parse_time(rows[-1][0])
    age_minutes = (now - latest_at).total_seconds() / 60.0
    if age_minutes < -1.0 or age_minutes > MAX_AGE_MINUTES:
        return {
            "state": "DATA_MISSING", "reason": "TAKER_FLOW_STALE",
            "age_minutes": age_minutes,
        }
    prepared = []
    for close_time, opened, closed, quote, taker_buy in rows:
        values = tuple(float(v) for v in (opened, closed, quote, taker_buy))
        if not all(math.isfinite(v) for v in values):
            return {"state": "DATA_MISSING", "reason": "NON_FINITE_FLOW_VALUE"}
        opened, closed, quote, taker_buy = values
        if opened <= 0 or closed <= 0 or quote <= 0 or not 0 <= taker_buy <= quote:
            return {"state": "DATA_MISSING", "reason": "INVALID_FLOW_VALUE"}
        prepared.append((close_time, opened, closed, quote, taker_buy))

    def window(hours):
        selected = prepared[-hours:]
        quote = sum(row[3] for row in selected)
        buy = sum(row[4] for row in selected)
        sell = quote - buy
        net = buy - sell
        price_return = (selected[-1][2] / selected[0][1] - 1.0) * 100.0
        direction = "BUY_PRESSURE" if net > 0 else (
            "SELL_PRESSURE" if net < 0 else "BALANCED"
        )
        confirms = (
            direction == "BALANCED"
            or (direction == "BUY_PRESSURE" and price_return > 0)
            or (direction == "SELL_PRESSURE" and price_return < 0)
        )
        return {
            "hours": hours, "buy_quote": buy, "sell_quote": sell,
            "net_quote": net, "buy_ratio_pct": buy / quote * 100.0,
            "price_return_pct": price_return, "direction": direction,
            "price_confirms": confirms,
        }

    windows = {hours: window(hours) for hours in (1, 4, 24) if len(prepared) >= hours}
    return {
        "state": "OBSERVATION_ONLY",
        "source": "BINANCE_SPOT_CLOSED_KLINES_TAKER_FLOW",
        "latest_at": rows[-1][0],
        "age_minutes": age_minutes,
        "complete_hours": len(prepared),
        "windows": windows,
        "policy_note": "Observed aggressor flow is not deposit flow or automatic trade authority.",
    }
