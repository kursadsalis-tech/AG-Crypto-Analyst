"""Long-lived Binance USD-M closed 1m technical-bar collector.

This worker is data-only. It has no trading authority and never calls the
restricted USD-M REST market-data API. The dynamic broad universe is refreshed
from canonical Binance WebSocket ticker/book snapshots while one independent
kline websocket stays connected continuously.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sqlite3
import ssl
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import certifi
import websockets

from data.binance_live_evidence import MARKET_RAW_BASE, fetch_market_tickers
from data.binance_market_universe import build_universe
from data.binance_technical_bars import normalize_closed_1m_kline
from database.technical_bar_store import persist_bars


DB_PATH = os.getenv("AGCA_DATABASE_PATH", "/var/lib/agca/crypto_data.db")
HEALTH_PATH = Path(os.getenv(
    "AGCA_TECHNICAL_KLINE_HEALTH",
    "/var/lib/agca/technical-kline-health.json",
))
UNIVERSE_REFRESH_SECONDS = float(os.getenv(
    "AGCA_TECHNICAL_UNIVERSE_REFRESH_SECONDS", "60"
))
RECENT_SEED_MINUTES = int(os.getenv(
    "AGCA_TECHNICAL_RECENT_SEED_MINUTES", "30"
))
OPEN_TIMEOUT_SECONDS = float(os.getenv(
    "AGCA_BINANCE_WS_OPEN_TIMEOUT_SECONDS", "10"
))
FLUSH_GRACE_MS = 1_500
SUBSCRIPTION_REFRESH_SECONDS = 1.0
SUBSCRIPTION_CHUNK = 100
UNIVERSE_RETENTION_SECONDS = float(os.getenv(
    "AGCA_TECHNICAL_UNIVERSE_RETENTION_SECONDS", "3600"
))
HEALTH_CONTRACT_VERSION = "AGCA_TECHNICAL_KLINE_HEALTH_V1_BINANCE_WS"


def _atomic_json_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def write_health(status: str, **details) -> None:
    _atomic_json_write(HEALTH_PATH, {
        "health_contract_version": HEALTH_CONTRACT_VERSION,
        "status": status,
        "at": datetime.now(timezone.utc).isoformat(),
        "execution_mode": "PAPER_ONLY",
        "market_data_provider": "BINANCE",
        "transport": "USD_M_WEBSOCKET_KLINE_1M",
        "may_send_real_orders": False,
        **details,
    })


def subscription_params(symbols) -> list[str]:
    return [
        f"{str(symbol).lower()}@kline_1m"
        for symbol in sorted({
            str(symbol).upper()
            for symbol in symbols
            if str(symbol).upper().endswith("USDT")
            and "_" not in str(symbol)
        })
    ]


def selected_symbols(tickers, *, snapshot_ms: int) -> tuple[str, ...]:
    universe = build_universe(tickers, snapshot_ms=int(snapshot_ms))
    return tuple(sorted({
        str(row["symbol"]).upper()
        for row in universe.get("selected", [])
    }))


def recent_cached_symbols(
    database_path: str, *, now_ms: int, minutes: int = RECENT_SEED_MINUTES
) -> tuple[str, ...]:
    cutoff = int(now_ms) - max(1, int(minutes)) * 60_000
    try:
        with sqlite3.connect(database_path, timeout=5) as con:
            rows = con.execute(
                """SELECT DISTINCT symbol
                   FROM technical_minute_bars
                   WHERE provider='BINANCE'
                     AND market_type='USD_M_PERPETUAL'
                     AND close_time_ms>=?
                   ORDER BY symbol""",
                (cutoff,),
            ).fetchall()
    except sqlite3.OperationalError:
        return ()
    return tuple(str(row[0]).upper() for row in rows)


def _chunks(values, size=SUBSCRIPTION_CHUNK):
    values = list(values)
    size = max(1, int(size))
    for index in range(0, len(values), size):
        yield values[index:index + size]


class UniverseState:
    """Continuity-first technical universe.

    Broad discovery snapshots are sampled from change-driven websocket streams,
    so one short snapshot can omit perfectly valid symbols.  A technical-bar
    collector must not unsubscribe immediately on that transient absence or it
    creates holes that make exact contiguous plans impossible.
    """

    def __init__(self, initial=(), *, now_ms=None, retention_seconds=None):
        stamp = int(
            now_ms if now_ms is not None else time.time() * 1000
        )
        self.retention_ms = int(
            1000.0 * (
                UNIVERSE_RETENTION_SECONDS
                if retention_seconds is None else float(retention_seconds)
            )
        )
        initial_symbols = {
            str(value).upper() for value in initial
            if str(value).upper().endswith("USDT") and "_" not in str(value)
        }
        self.last_seen_ms = {symbol: stamp for symbol in initial_symbols}
        self.symbols = tuple(sorted(initial_symbols))
        self.updated_at_ms = 0
        self.selected_symbol_count = 0
        self.retained_symbol_count = len(self.symbols)

    def refresh(self, values, now_ms):
        normalized = {
            str(value).upper() for value in values
            if str(value).upper().endswith("USDT") and "_" not in str(value)
        }
        if not normalized:
            raise ValueError("technical kline universe cannot be empty")
        stamp = int(now_ms)
        for symbol in normalized:
            self.last_seen_ms[symbol] = stamp

        cutoff = stamp - max(0, self.retention_ms)
        self.last_seen_ms = {
            symbol: seen
            for symbol, seen in self.last_seen_ms.items()
            if seen >= cutoff
        }
        self.symbols = tuple(sorted(self.last_seen_ms))
        self.updated_at_ms = stamp
        self.selected_symbol_count = len(normalized)
        self.retained_symbol_count = len(self.symbols) - len(normalized)
        return self.symbols


async def refresh_universe(state: UniverseState, stop_event: asyncio.Event):
    while not stop_event.is_set():
        try:
            tickers = await fetch_market_tickers()
            snapshot_ms = int(time.time() * 1000)
            symbols = selected_symbols(tickers, snapshot_ms=snapshot_ms)
            state.refresh(symbols, snapshot_ms)
            write_health(
                "CONNECTED",
                subscribed_symbol_count=len(state.symbols),
                selected_symbol_count=state.selected_symbol_count,
                retained_symbol_count=state.retained_symbol_count,
                universe_retention_seconds=UNIVERSE_RETENTION_SECONDS,
                universe_updated_at_ms=state.updated_at_ms,
            )
            delay = UNIVERSE_REFRESH_SECONDS
        except Exception as exc:
            write_health(
                "DEGRADED",
                error=f"UNIVERSE_REFRESH_{type(exc).__name__}",
                subscribed_symbol_count=len(state.symbols),
                universe_updated_at_ms=state.updated_at_ms,
            )
            delay = min(15.0, UNIVERSE_REFRESH_SECONDS)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(1.0, delay))
        except asyncio.TimeoutError:
            pass


async def persist_pending(
    pending: dict[tuple[str, int], dict],
    state: UniverseState,
    stop_event: asyncio.Event,
):
    while not stop_event.is_set():
        await asyncio.sleep(0.5)
        if not pending:
            continue
        now_ms = int(time.time() * 1000)
        ready_keys = [
            key for key, bar in pending.items()
            if now_ms - int(bar["close_time_ms"]) >= FLUSH_GRACE_MS
        ]
        if not ready_keys:
            continue
        ready = [pending[key] for key in ready_keys]
        try:
            result = await asyncio.to_thread(
                persist_bars,
                DB_PATH,
                ready,
                now_ms=now_ms,
            )
        except Exception as exc:
            write_health(
                "DEGRADED",
                error=f"PERSIST_{type(exc).__name__}",
                subscribed_symbol_count=len(state.symbols),
                pending_bar_count=len(pending),
            )
            continue
        for key in ready_keys:
            pending.pop(key, None)
        latest = max(int(bar["close_time_ms"]) for bar in ready)
        minute_symbols = len({
            str(bar["symbol"]).upper()
            for bar in ready
            if int(bar["close_time_ms"]) == latest
        })
        write_health(
            "CONNECTED",
            subscribed_symbol_count=len(state.symbols),
            universe_updated_at_ms=state.updated_at_ms,
            pending_bar_count=len(pending),
            last_closed_minute_ms=latest,
            last_flush_symbol_count=minute_symbols,
            last_flush_upserted=int(result["upserted"]),
            cache_row_count=int(result["row_count"]),
        )


async def kline_stream(state: UniverseState, stop_event: asyncio.Event):
    context = ssl.create_default_context(cafile=certifi.where())
    pending: dict[tuple[str, int], dict] = {}
    backoff = 1.0
    while not stop_event.is_set():
        if not state.symbols:
            await asyncio.sleep(1)
            continue
        try:
            async with websockets.connect(
                MARKET_RAW_BASE,
                ssl=context,
                open_timeout=OPEN_TIMEOUT_SECONDS,
                ping_interval=20,
                ping_timeout=10,
                max_queue=8192,
            ) as socket:
                active = set()
                request_id = 1

                async def send_change(method: str, params: list[str]):
                    nonlocal request_id
                    for chunk in _chunks(params):
                        await socket.send(json.dumps({
                            "method": method,
                            "params": chunk,
                            "id": request_id,
                        }))
                        request_id += 1

                initial = subscription_params(state.symbols)
                await send_change("SUBSCRIBE", initial)
                active = set(initial)
                backoff = 1.0
                write_health(
                    "CONNECTED",
                    subscribed_symbol_count=len(active),
                    universe_updated_at_ms=state.updated_at_ms,
                )

                async def receiver():
                    messages_since_yield = 0
                    while not stop_event.is_set():
                        try:
                            async with asyncio.timeout(1):
                                raw = await socket.recv()
                        except TimeoutError:
                            continue
                        message = json.loads(raw)
                        if not isinstance(message, dict):
                            continue
                        if message.get("code") is not None:
                            raise RuntimeError(
                                f"BINANCE_SUBSCRIPTION_ERROR_{message.get('code')}"
                            )
                        bar = normalize_closed_1m_kline(message)
                        if bar is not None:
                            pending[(
                                str(bar["symbol"]).upper(),
                                int(bar["open_time_ms"]),
                            )] = bar
                        messages_since_yield += 1
                        if messages_since_yield >= 64:
                            messages_since_yield = 0
                            await asyncio.sleep(0)

                async def refresh_subscriptions():
                    nonlocal active
                    while not stop_event.is_set():
                        await asyncio.sleep(SUBSCRIPTION_REFRESH_SECONDS)
                        desired = set(subscription_params(state.symbols))
                        removed = sorted(active - desired)
                        added = sorted(desired - active)
                        if removed:
                            await send_change("UNSUBSCRIBE", removed)
                        if added:
                            await send_change("SUBSCRIBE", added)
                        active = desired

                tasks = [
                    asyncio.create_task(receiver()),
                    asyncio.create_task(refresh_subscriptions()),
                    asyncio.create_task(
                        persist_pending(pending, state, stop_event)
                    ),
                ]
                try:
                    await asyncio.gather(*tasks)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except (
            OSError,
            asyncio.TimeoutError,
            ValueError,
            RuntimeError,
            json.JSONDecodeError,
            websockets.WebSocketException,
        ) as exc:
            write_health(
                "DEGRADED",
                error=f"KLINE_STREAM_{type(exc).__name__}",
                subscribed_symbol_count=len(state.symbols),
                pending_bar_count=len(pending),
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2.0, 30.0)


async def run():
    if os.getenv("AGCA_EXECUTION_MODE", "PAPER_ONLY") != "PAPER_ONLY":
        raise RuntimeError("technical kline worker accepts PAPER_ONLY exclusively")
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop_event.set)

    now_ms = int(time.time() * 1000)
    seed = recent_cached_symbols(DB_PATH, now_ms=now_ms)
    state = UniverseState(seed, now_ms=now_ms)
    write_health(
        "STARTING",
        subscribed_symbol_count=len(state.symbols),
        seed_symbol_count=len(seed),
    )
    try:
        await asyncio.gather(
            refresh_universe(state, stop_event),
            kline_stream(state, stop_event),
        )
    finally:
        write_health(
            "STOPPED",
            subscribed_symbol_count=len(state.symbols),
            universe_updated_at_ms=state.updated_at_ms,
        )


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
