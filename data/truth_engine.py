"""Fail-closed validation contracts for data used by product decisions.

Collectors may remain source-specific, but decision-bearing consumers must pass
their payloads through this module before computing features or signals.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests


AUDIT_TABLE = "data_validation_audit"
MAX_CROSS_SOURCE_DEVIATION_PCT = 2.0


@dataclass(frozen=True)
class DataValidationError(ValueError):
    source: str
    reasons: tuple[str, ...]

    def __str__(self):
        return f"{self.source}: DATA_BLOCKED ({'; '.join(self.reasons)})"


def _finite(value, label, reasons):
    try:
        number = float(value)
    except (TypeError, ValueError):
        reasons.append(f"{label}_NOT_NUMERIC")
        return None
    if not math.isfinite(number):
        reasons.append(f"{label}_NOT_FINITE")
        return None
    return number


def validate_closed_klines(
    rows,
    *,
    source,
    now=None,
    minimum_rows=1,
    interval=timedelta(hours=1),
    maximum_staleness=timedelta(hours=2, minutes=5),
):
    """Return rows only when their availability and OHLC contract is valid."""
    now = now or datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    reasons = []
    if not isinstance(rows, list):
        raise DataValidationError(source, ("PAYLOAD_NOT_LIST",))
    if len(rows) < minimum_rows:
        reasons.append(f"INSUFFICIENT_ROWS_{len(rows)}_OF_{minimum_rows}")

    previous_open = None
    for index, row in enumerate(rows):
        prefix = f"ROW_{index}"
        if not isinstance(row, (list, tuple)) or len(row) < 8:
            reasons.append(f"{prefix}_SCHEMA")
            continue
        open_ms = _finite(row[0], f"{prefix}_OPEN_TIME", reasons)
        close_ms = _finite(row[6], f"{prefix}_CLOSE_TIME", reasons)
        opened = _finite(row[1], f"{prefix}_OPEN", reasons)
        high = _finite(row[2], f"{prefix}_HIGH", reasons)
        low = _finite(row[3], f"{prefix}_LOW", reasons)
        close = _finite(row[4], f"{prefix}_CLOSE", reasons)
        volume = _finite(row[5], f"{prefix}_VOLUME", reasons)
        quote_volume = _finite(row[7], f"{prefix}_QUOTE_VOLUME", reasons)
        if None in (open_ms, close_ms, opened, high, low, close, volume, quote_volume):
            continue
        if close_ms >= now_ms:
            reasons.append(f"{prefix}_NOT_CLOSED")
        if close_ms <= open_ms:
            reasons.append(f"{prefix}_INVALID_TIME_RANGE")
        if previous_open is not None:
            expected = previous_open + int(interval.total_seconds() * 1000)
            if open_ms != expected:
                reasons.append(f"{prefix}_NON_CONTIGUOUS")
        previous_open = open_ms
        if min(opened, high, low, close) <= 0:
            reasons.append(f"{prefix}_NON_POSITIVE_PRICE")
        if high < max(opened, close, low) or low > min(opened, close, high):
            reasons.append(f"{prefix}_OHLC_INCONSISTENT")
        if volume < 0 or quote_volume < 0:
            reasons.append(f"{prefix}_NEGATIVE_VOLUME")

    if rows and isinstance(rows[-1], (list, tuple)) and len(rows[-1]) >= 7:
        last_close = _finite(rows[-1][6], "LAST_CLOSE_TIME", reasons)
        if last_close is not None:
            age = now - datetime.fromtimestamp(last_close / 1000, timezone.utc)
            if age < timedelta(0):
                reasons.append("LATEST_CANDLE_FROM_FUTURE")
            elif age > maximum_staleness:
                reasons.append("LATEST_CANDLE_STALE")
    if reasons:
        raise DataValidationError(source, tuple(dict.fromkeys(reasons)))
    return rows


def ensure_audit_table(con):
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            checked_at TEXT NOT NULL,
            gate_name TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL,
            record_count INTEGER NOT NULL,
            reasons_json TEXT NOT NULL,
            detail_json TEXT NOT NULL
        )
    """)
    con.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_data_validation_gate_time
        ON {AUDIT_TABLE}(gate_name, checked_at)
    """)


def record_audit(
    con, *, gate_name, source, status, record_count=0, reasons=(), detail=None,
    checked_at=None,
):
    ensure_audit_table(con)
    checked_at = checked_at or datetime.now(timezone.utc)
    con.execute(f"""
        INSERT INTO {AUDIT_TABLE}(
            checked_at,gate_name,source,status,record_count,reasons_json,detail_json
        ) VALUES (?,?,?,?,?,?,?)
    """, (
        checked_at.astimezone(timezone.utc).isoformat(), gate_name, source, status,
        int(record_count), json.dumps(list(reasons), ensure_ascii=False),
        json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
    ))
    con.commit()


def record_cycle_validation(con, agent_name, status, message, checked_at=None):
    mapped = "PASS" if status == "SUCCESS" else "DATA_BLOCKED"
    reasons = () if mapped == "PASS" else (f"CYCLE_{status}",)
    record_audit(
        con,
        gate_name="SOURCE_CYCLE",
        source=agent_name,
        status=mapped,
        reasons=reasons,
        detail={"cycle_status": status, "message": str(message)[:1000]},
        checked_at=checked_at,
    )


def audited_validation(con, *, gate_name, source, validator, record_count=0):
    try:
        result = validator()
    except DataValidationError as exc:
        record_audit(
            con, gate_name=gate_name, source=source, status="DATA_BLOCKED",
            record_count=record_count, reasons=exc.reasons,
        )
        raise
    except Exception as exc:
        record_audit(
            con, gate_name=gate_name, source=source, status="DATA_BLOCKED",
            record_count=record_count,
            reasons=(f"SOURCE_ERROR_{type(exc).__name__}",),
        )
        raise
    record_audit(
        con, gate_name=gate_name, source=source, status="PASS",
        record_count=record_count,
    )
    return result


def validate_stored_candles(
    candles, *, source, require_contiguous=False, maximum_gap_ratio=0.0,
):
    """Validate normalized historical candle dictionaries without hiding rows."""
    reasons = []
    previous_open = None
    gaps = 0
    for index, candle in enumerate(candles):
        prefix = f"ROW_{index}"
        if not isinstance(candle, dict):
            reasons.append(f"{prefix}_SCHEMA")
            continue
        opened = _finite(candle.get("open"), f"{prefix}_OPEN", reasons)
        high = _finite(candle.get("high"), f"{prefix}_HIGH", reasons)
        low = _finite(candle.get("low"), f"{prefix}_LOW", reasons)
        close = _finite(candle.get("close"), f"{prefix}_CLOSE", reasons)
        volume = _finite(candle.get("volume"), f"{prefix}_VOLUME", reasons)
        quote = _finite(candle.get("quote_volume"), f"{prefix}_QUOTE_VOLUME", reasons)
        open_time = candle.get("open_time")
        close_time = candle.get("close_time")
        if not isinstance(open_time, datetime) or not isinstance(close_time, datetime):
            reasons.append(f"{prefix}_TIME_INVALID")
            continue
        if close_time <= open_time:
            reasons.append(f"{prefix}_INVALID_TIME_RANGE")
        if previous_open is not None:
            delta = (open_time - previous_open).total_seconds()
            if not 3599 <= delta <= 3601:
                gaps += 1
        previous_open = open_time
        if None in (opened, high, low, close, volume, quote):
            continue
        if min(opened, high, low, close) <= 0:
            reasons.append(f"{prefix}_NON_POSITIVE_PRICE")
        if high < max(opened, close, low) or low > min(opened, close, high):
            reasons.append(f"{prefix}_OHLC_INCONSISTENT")
        if volume < 0 or quote < 0:
            reasons.append(f"{prefix}_NEGATIVE_VOLUME")
    gap_ratio = gaps / max(1, len(candles) - 1)
    if require_contiguous and gap_ratio > maximum_gap_ratio:
        reasons.append(f"GAP_RATIO_{gap_ratio:.6f}_ABOVE_{maximum_gap_ratio:.6f}")
    if reasons:
        raise DataValidationError(source, tuple(dict.fromkeys(reasons)))
    return candles


def validate_cross_source_prices(primary, independent, *, source):
    reasons = []
    first = _finite(primary, "PRIMARY_PRICE", reasons)
    second = _finite(independent, "INDEPENDENT_PRICE", reasons)
    deviation = None
    if first is not None and second is not None:
        if first <= 0 or second <= 0:
            reasons.append("NON_POSITIVE_REFERENCE_PRICE")
        else:
            deviation = abs(first - second) / ((first + second) / 2.0) * 100.0
            if deviation > MAX_CROSS_SOURCE_DEVIATION_PCT:
                reasons.append(
                    f"CROSS_SOURCE_DEVIATION_{deviation:.4f}_ABOVE_"
                    f"{MAX_CROSS_SOURCE_DEVIATION_PCT:.4f}"
                )
    if reasons:
        raise DataValidationError(source, tuple(reasons))
    return deviation


def _parse_utc(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_news_articles(articles, *, source, now=None):
    now = now or datetime.now(timezone.utc)
    reasons = []
    seen_links = set()
    if not isinstance(articles, list) or not articles:
        raise DataValidationError(source, ("EMPTY_OR_NON_LIST_PAYLOAD",))
    for index, article in enumerate(articles):
        prefix = f"ROW_{index}"
        if not isinstance(article, dict):
            reasons.append(f"{prefix}_SCHEMA")
            continue
        if not str(article.get("source") or "").strip():
            reasons.append(f"{prefix}_SOURCE_MISSING")
        if not str(article.get("title") or "").strip():
            reasons.append(f"{prefix}_TITLE_MISSING")
        link = str(article.get("link") or "").strip()
        if not link.startswith(("https://", "http://")):
            reasons.append(f"{prefix}_LINK_INVALID")
        elif link in seen_links:
            reasons.append(f"{prefix}_DUPLICATE_LINK")
        seen_links.add(link)
        published = _parse_utc(article.get("published_at"))
        if published is None:
            reasons.append(f"{prefix}_PUBLISHED_TIME_INVALID")
        elif published > now + timedelta(minutes=10):
            reasons.append(f"{prefix}_PUBLISHED_TIME_FUTURE")
    if reasons:
        raise DataValidationError(source, tuple(dict.fromkeys(reasons)))
    return articles


def validate_social_records(records, *, source, now=None):
    articles = [{
        "source": row.get("source"),
        "title": row.get("title"),
        "link": row.get("link"),
        "published_at": row.get("published_at"),
    } for row in records if isinstance(row, dict)]
    if len(articles) != len(records):
        raise DataValidationError(source, ("RECORD_SCHEMA",))
    validate_news_articles(articles, source=source, now=now)
    ids = [str(row.get("external_id") or "").strip() for row in records]
    reasons = []
    if any(not value for value in ids):
        reasons.append("EXTERNAL_ID_MISSING")
    if len(ids) != len(set(ids)):
        reasons.append("DUPLICATE_EXTERNAL_ID")
    if reasons:
        raise DataValidationError(source, tuple(reasons))
    return records


def validate_metric_rows(rows, *, source):
    reasons = []
    identities = set()
    if not rows:
        raise DataValidationError(source, ("EMPTY_METRICS",))
    for index, row in enumerate(rows):
        prefix = f"ROW_{index}"
        if not isinstance(row, (tuple, list)) or len(row) < 5:
            reasons.append(f"{prefix}_SCHEMA")
            continue
        provider, entity, metric, value, unit = row[:5]
        if not all(str(item or "").strip() for item in (provider, entity, metric, unit)):
            reasons.append(f"{prefix}_IDENTITY_MISSING")
        identity = (provider, entity, metric)
        if identity in identities:
            reasons.append(f"{prefix}_DUPLICATE_METRIC")
        identities.add(identity)
        number = _finite(value, f"{prefix}_VALUE", reasons)
        if number is not None and number < 0:
            reasons.append(f"{prefix}_NEGATIVE_VALUE")
    if reasons:
        raise DataValidationError(source, tuple(dict.fromkeys(reasons)))
    return rows


def validate_macro_series(observations, *, source, now=None):
    now = now or datetime.now(timezone.utc)
    reasons = []
    previous = None
    if not observations:
        raise DataValidationError(source, ("EMPTY_SERIES",))
    for index, row in enumerate(observations):
        prefix = f"ROW_{index}"
        if not isinstance(row, (tuple, list)) or len(row) != 2:
            reasons.append(f"{prefix}_SCHEMA")
            continue
        try:
            observed = datetime.fromisoformat(str(row[0])).date()
        except ValueError:
            reasons.append(f"{prefix}_DATE_INVALID")
            continue
        if observed > now.date():
            reasons.append(f"{prefix}_DATE_FUTURE")
        if previous is not None and observed <= previous:
            reasons.append(f"{prefix}_DATE_NOT_STRICTLY_INCREASING")
        previous = observed
        _finite(row[1], f"{prefix}_VALUE", reasons)
    if reasons:
        raise DataValidationError(source, tuple(dict.fromkeys(reasons)))
    return observations


def validate_whale_events(events, *, source, now=None):
    now = now or datetime.now(timezone.utc)
    reasons = []
    identities = set()
    for index, event in enumerate(events):
        prefix = f"ROW_{index}"
        if not isinstance(event, dict):
            reasons.append(f"{prefix}_SCHEMA")
            continue
        identity = (event.get("chain"), event.get("txid"), event.get("output_index"))
        if not all(str(value if value is not None else "").strip() for value in identity):
            reasons.append(f"{prefix}_IDENTITY_MISSING")
        if identity in identities:
            reasons.append(f"{prefix}_DUPLICATE_EVENT")
        identities.add(identity)
        block_time = _parse_utc(event.get("block_time"))
        if block_time is None:
            reasons.append(f"{prefix}_BLOCK_TIME_INVALID")
        elif block_time > now + timedelta(minutes=5):
            reasons.append(f"{prefix}_BLOCK_TIME_FUTURE")
        for field in ("amount_native", "usd_estimate"):
            value = _finite(event.get(field), f"{prefix}_{field.upper()}", reasons)
            if value is not None and value <= 0:
                reasons.append(f"{prefix}_{field.upper()}_NON_POSITIVE")
        if not str(event.get("source") or "").strip():
            reasons.append(f"{prefix}_SOURCE_MISSING")
    if reasons:
        raise DataValidationError(source, tuple(dict.fromkeys(reasons)))
    return events


def fetch_and_validate_btc_anchor(timeout=12):
    """Compare Binance BTC/USDT with an independently operated CoinGecko feed."""
    first = requests.get(
        os.getenv("AGCA_BINANCE_SPOT_BASE_URL", "https://data-api.binance.vision")
        + "/api/v3/ticker/price",
        params={"symbol": "BTCUSDT"}, timeout=timeout,
        headers={"User-Agent": "AG-Crypto-Analyst/TruthEngine"},
    )
    first.raise_for_status()
    second = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": "bitcoin", "vs_currencies": "usd"}, timeout=timeout,
        headers={"User-Agent": "AG-Crypto-Analyst/TruthEngine"},
    )
    second.raise_for_status()
    try:
        primary = first.json()["price"]
        independent = second.json()["bitcoin"]["usd"]
    except (KeyError, TypeError, ValueError) as exc:
        raise DataValidationError("BTC_CROSS_SOURCE", ("RESPONSE_SCHEMA",)) from exc
    deviation = validate_cross_source_prices(
        primary, independent, source="BTC_CROSS_SOURCE",
    )
    return {
        "binance_price": float(primary),
        "coingecko_price": float(independent),
        "deviation_pct": deviation,
    }
