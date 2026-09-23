"""Local-only bounded cache of canonical Binance USD-M closed technical bars."""
from __future__ import annotations

from datetime import datetime, timezone

from database.runtime_store import connect


TABLE = "technical_minute_bars"
DEFAULT_RETENTION_DAYS = 3
MARKET_TYPE = "USD_M_PERPETUAL"
PROVIDER = "BINANCE"


def ensure_table(connection):
    connection.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            symbol TEXT NOT NULL,
            market_type TEXT NOT NULL,
            open_time_ms INTEGER NOT NULL,
            close_time_ms INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            quote_volume REAL NOT NULL,
            trade_count INTEGER NOT NULL,
            taker_buy_quote_volume REAL NOT NULL,
            provider TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(symbol,market_type,open_time_ms)
        )
    """)
    connection.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_{TABLE}_symbol_close
        ON {TABLE}(symbol,market_type,close_time_ms)
    """)


def _validated(bar):
    required = (
        "symbol", "open_time_ms", "close_time_ms", "open", "high", "low",
        "close", "volume", "quote_volume", "trade_count",
        "taker_buy_quote_volume", "source",
    )
    if any(key not in bar for key in required):
        raise ValueError("incomplete technical bar")
    symbol = str(bar["symbol"]).upper()
    if not symbol.endswith("USDT") or "_" in symbol:
        raise ValueError("unsupported technical bar symbol")
    opened = int(bar["open_time_ms"])
    closed = int(bar["close_time_ms"])
    if opened <= 0 or closed <= opened or closed - opened != 59_999:
        raise ValueError("invalid one-minute technical bar timestamp")
    prices = tuple(float(bar[key]) for key in ("open", "high", "low", "close"))
    if (
        min(prices) <= 0
        or prices[1] < max(prices[0], prices[3])
        or prices[2] > min(prices[0], prices[3])
        or prices[1] < prices[2]
    ):
        raise ValueError("invalid technical bar OHLC")
    volume = float(bar["volume"])
    quote_volume = float(bar["quote_volume"])
    taker_buy_quote = float(bar["taker_buy_quote_volume"])
    trade_count = int(bar["trade_count"])
    if min(volume, quote_volume, taker_buy_quote) < 0 or trade_count < 0:
        raise ValueError("invalid technical bar volume")
    return (
        symbol, MARKET_TYPE, opened, closed,
        *prices, volume, quote_volume, trade_count, taker_buy_quote,
        PROVIDER, str(bar["source"]),
        datetime.now(timezone.utc).isoformat(),
    )


def upsert_bars(connection, bars):
    ensure_table(connection)
    prepared = [_validated(bar) for bar in bars]
    if not prepared:
        return 0
    connection.executemany(f"""
        INSERT INTO {TABLE}(
            symbol,market_type,open_time_ms,close_time_ms,
            open,high,low,close,volume,quote_volume,trade_count,
            taker_buy_quote_volume,provider,source,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(symbol,market_type,open_time_ms) DO UPDATE SET
            close_time_ms=excluded.close_time_ms,
            open=excluded.open,
            high=excluded.high,
            low=excluded.low,
            close=excluded.close,
            volume=excluded.volume,
            quote_volume=excluded.quote_volume,
            trade_count=excluded.trade_count,
            taker_buy_quote_volume=excluded.taker_buy_quote_volume,
            provider=excluded.provider,
            source=excluded.source,
            created_at=excluded.created_at
    """, prepared)
    return len(prepared)


def persist_bars(database_path, bars, *, now_ms=None, retention_days=DEFAULT_RETENTION_DAYS):
    now_ms = int(now_ms or datetime.now(timezone.utc).timestamp() * 1000)
    cutoff = now_ms - int(retention_days) * 86_400_000
    with connect(database_path) as connection:
        inserted = upsert_bars(connection, bars)
        pruned = connection.execute(
            f"DELETE FROM {TABLE} WHERE close_time_ms < ?", (cutoff,)
        ).rowcount
        count = connection.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    return {
        "upserted": inserted,
        "pruned": pruned,
        "row_count": int(count),
        "retention_days": int(retention_days),
        "remote_mirror_enabled_for_table": False,
    }


def recent_count(connection, symbol, *, since_ms):
    ensure_table(connection)
    return int(connection.execute(f"""
        SELECT COUNT(*) FROM {TABLE}
        WHERE symbol=? AND market_type=? AND provider=?
          AND close_time_ms>=?
    """, (
        str(symbol).upper(), MARKET_TYPE, PROVIDER, int(since_ms),
    )).fetchone()[0])


def symbols_needing_bootstrap(
    connection, symbols, *, now_ms, minimum_bars=360, lookback_hours=36
):
    since_ms = int(now_ms) - int(lookback_hours) * 3_600_000
    needed = []
    for symbol in sorted({str(value).upper() for value in symbols}):
        count = recent_count(connection, symbol, since_ms=since_ms)
        if count < int(minimum_bars):
            needed.append({"symbol": symbol, "count": count})
    return needed


def load_recent(connection, symbol, *, limit=720, before_ms=None):
    ensure_table(connection)
    params = [str(symbol).upper(), MARKET_TYPE, PROVIDER]
    where = "symbol=? AND market_type=? AND provider=?"
    if before_ms is not None:
        where += " AND close_time_ms<=?"
        params.append(int(before_ms))
    params.append(int(limit))
    rows = connection.execute(f"""
        SELECT open_time_ms,close_time_ms,open,high,low,close,
               volume,quote_volume,trade_count,taker_buy_quote_volume,source
        FROM {TABLE}
        WHERE {where}
        ORDER BY open_time_ms DESC
        LIMIT ?
    """, tuple(params)).fetchall()
    return [{
        "symbol": str(symbol).upper(),
        "open_time_ms": int(row[0]),
        "close_time_ms": int(row[1]),
        "open": float(row[2]),
        "high": float(row[3]),
        "low": float(row[4]),
        "close": float(row[5]),
        "volume": float(row[6]),
        "quote_volume": float(row[7]),
        "trade_count": int(row[8]),
        "taker_buy_quote_volume": float(row[9]),
        "source": row[10],
    } for row in reversed(rows)]
