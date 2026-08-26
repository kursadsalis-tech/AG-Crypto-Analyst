"""Fail-closed USD-M funding and conservative 1x liquidation mechanics."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import re
from urllib.parse import urlencode


FUNDING_ENDPOINT = "https://fapi.binance.com/fapi/v1/fundingRate"
_SYMBOL = re.compile(r"^[A-Z0-9]{5,30}$")


class FuturesCostError(RuntimeError):
    pass


@dataclass(frozen=True)
class FundingEvent:
    symbol: str
    funding_time_ms: int
    funding_rate: float
    mark_price: float


def _milliseconds(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.astimezone(timezone.utc).timestamp() * 1000)


def fetch_funding_history_via_postgres(
    *, database_url: str, symbol: str, start: datetime, end: datetime,
    timeout_seconds: float = 20,
) -> list[FundingEvent]:
    symbol = symbol.upper()
    if not _SYMBOL.fullmatch(symbol) or end <= start:
        raise FuturesCostError("INVALID_FUNDING_REQUEST")
    start_ms, end_ms = _milliseconds(start), _milliseconds(end)
    url = FUNDING_ENDPOINT + "?" + urlencode({
        "symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 1000,
    })
    if not database_url:
        raise FuturesCostError("SUPABASE_DATABASE_URL_UNAVAILABLE")
    try:
        import psycopg
        with psycopg.connect(database_url, connect_timeout=max(1, int(timeout_seconds))) as con:
            with con.cursor() as cursor:
                cursor.execute(
                    "SELECT status, content_type, content FROM extensions.http_get(%s)",
                    (url,),
                )
                row = cursor.fetchone()
    except Exception as exc:
        raise FuturesCostError("FUNDING_SOURCE_UNAVAILABLE") from exc
    if not row or row[0] != 200 or not str(row[1]).startswith("application/json"):
        raise FuturesCostError("INVALID_FUNDING_RESPONSE")
    try:
        payload = json.loads(row[2])
    except json.JSONDecodeError as exc:
        raise FuturesCostError("INVALID_FUNDING_JSON") from exc
    if not isinstance(payload, list):
        raise FuturesCostError("INVALID_FUNDING_PAYLOAD")
    events = []
    for item in payload:
        try:
            event = FundingEvent(
                symbol=str(item["symbol"]),
                funding_time_ms=int(item["fundingTime"]),
                funding_rate=float(item["fundingRate"]),
                mark_price=float(item["markPrice"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise FuturesCostError("INVALID_FUNDING_EVENT") from exc
        if (
            event.symbol != symbol
            or not all(math.isfinite(v) for v in (event.funding_rate, event.mark_price))
            or event.mark_price <= 0
            or not (start_ms < event.funding_time_ms <= end_ms)
        ):
            raise FuturesCostError("INVALID_FUNDING_EVENT")
        events.append(event)
    if events != sorted(events, key=lambda event: event.funding_time_ms):
        raise FuturesCostError("UNORDERED_FUNDING_HISTORY")
    return events


def directional_funding_payment(
    *, direction: str, quantity: float, events: list[FundingEvent],
) -> float:
    """Return signed account cash cost: positive=paid, negative=received."""
    if direction not in {"LONG", "SHORT"} or quantity <= 0 or not math.isfinite(quantity):
        raise FuturesCostError("INVALID_FUNDING_POSITION")
    side = 1.0 if direction == "LONG" else -1.0
    return sum(quantity * event.mark_price * event.funding_rate * side for event in events)


def liquidation_price(
    *, entry_price: float, direction: str, leverage: float = 1.0,
    maintenance_margin_rate: float = 0.005,
) -> float:
    """Conservative isolated-margin threshold; not an account-specific quote."""
    values = (entry_price, leverage, maintenance_margin_rate)
    if (
        direction not in {"LONG", "SHORT"}
        or not all(math.isfinite(v) for v in values)
        or entry_price <= 0 or leverage <= 0
        or not 0 <= maintenance_margin_rate < 1
    ):
        raise FuturesCostError("INVALID_LIQUIDATION_INPUT")
    if direction == "LONG":
        return max(0.0, entry_price * (1.0 - 1.0 / leverage) / (1.0 - maintenance_margin_rate))
    return entry_price * (1.0 + 1.0 / leverage) / (1.0 + maintenance_margin_rate)


def liquidation_breached(*, direction: str, threshold: float, low: float, high: float) -> bool:
    if not all(math.isfinite(v) for v in (threshold, low, high)) or low <= 0 or high < low:
        raise FuturesCostError("INVALID_LIQUIDATION_CANDLE")
    return low <= threshold if direction == "LONG" else high >= threshold
