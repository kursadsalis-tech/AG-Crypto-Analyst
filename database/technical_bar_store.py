"""Local-only bounded cache of canonical Binance USD-M closed technical bars."""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

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
    connection.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_{TABLE}_close
        ON {TABLE}(close_time_ms)
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


def _upsert_prepared(connection, prepared):
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


def upsert_bars(connection, bars):
    """General helper for setup/tests; realtime persistence uses prebootstrapped schema."""
    ensure_table(connection)
    return _upsert_prepared(connection, [_validated(bar) for bar in bars])


def _table_exists(connection):
    return bool(connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE,),
    ).fetchone())


def _schema_ready(connection):
    if not _table_exists(connection):
        return False
    indexes = {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
            (TABLE,),
        ).fetchall()
    }
    return {
        f"ix_{TABLE}_symbol_close",
        f"ix_{TABLE}_close",
    }.issubset(indexes)


def bootstrap_schema(database_path):
    """Install technical-bar table/indexes outside the closed-minute write path."""
    connection = sqlite3.connect(database_path, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    try:
        ensure_table(connection)
        connection.commit()
    finally:
        connection.close()


def _writer_connection(database_path):
    # technical_minute_bars is local-only. RuntimeConnection construction would
    # repeat remote outbox/trigger schema work even though this table is never
    # mirrored, creating avoidable writer contention on the canonical DB.
    connection = sqlite3.connect(database_path, timeout=30)
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def _database_locked(exc):
    return (
        isinstance(exc, sqlite3.OperationalError)
        and "database is locked" in str(exc).lower()
    )


def _persist_bars_once(database_path, bars, *, now_ms, retention_days):
    cutoff = int(now_ms) - int(retention_days) * 86_400_000
    prepared = [_validated(bar) for bar in bars]
    connection = _writer_connection(database_path)
    try:
        if not _schema_ready(connection):
            raise RuntimeError("technical bar schema unavailable")
        inserted = _upsert_prepared(connection, prepared)
        pruned = connection.execute(
            f"DELETE FROM {TABLE} WHERE close_time_ms < ?", (cutoff,)
        ).rowcount
        # Release the single SQLite writer slot before telemetry COUNT work.
        connection.commit()
        count = connection.execute(
            f"SELECT COUNT(*) FROM {TABLE}"
        ).fetchone()[0]
    finally:
        connection.close()
    return {
        "upserted": inserted,
        "pruned": pruned,
        "row_count": int(count),
        "retention_days": int(retention_days),
        "remote_mirror_enabled_for_table": False,
    }


def persist_bars(
    database_path, bars, *, now_ms=None,
    retention_days=DEFAULT_RETENTION_DAYS, attempts=4,
):
    """Persist prepared closed bars with bounded fresh-connection retries."""
    now_ms = int(now_ms or datetime.now(timezone.utc).timestamp() * 1000)
    probe = sqlite3.connect(database_path, timeout=5)
    try:
        ready = _schema_ready(probe)
    finally:
        probe.close()
    if not ready:
        bootstrap_schema(database_path)
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        try:
            return _persist_bars_once(
                database_path, bars, now_ms=now_ms,
                retention_days=retention_days,
            )
        except sqlite3.OperationalError as exc:
            if not _database_locked(exc) or attempt >= attempts:
                raise
        time.sleep(min(0.5 * attempt, 2.0))
    raise RuntimeError("unreachable technical-bar persistence retry state")


def recent_count(connection, symbol, *, since_ms):
    # Read paths must never perform DDL on the hot runtime connection.  The
    # technical worker owns schema creation during startup; a missing table is
    # simply no cached history yet.
    if not _table_exists(connection):
        return 0
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
    # This function is used inside every pre-trade technical plan.  Running
    # CREATE TABLE / CREATE INDEX here can wait on the concurrent kline writer's
    # schema lock for the full SQLite busy timeout for each candidate.  Keep the
    # decision hot path strictly read-only and fail closed when bootstrap has not
    # created the cache yet.
    if not _table_exists(connection):
        return []
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
