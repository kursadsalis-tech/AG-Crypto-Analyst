"""Validated exchange-rule cache for PAPER execution.

The cache is deliberately fail-closed: rules are usable only when they came
from the configured market's official exchangeInfo endpoint and are fresh.
This module contains no credentials or order-placement capability.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

from paper_trading.execution_contract import ExecutionRules


OFFICIAL_ENDPOINTS = {
    "SPOT": "https://data-api.binance.vision/api/v3/exchangeInfo",
    "USD_M_PERPETUAL": "https://fapi.binance.com/fapi/v1/exchangeInfo",
}


class ExchangeMetadataError(RuntimeError):
    """Raised when exact, fresh exchange rules cannot be proven."""


@dataclass(frozen=True)
class CachedExchangeMetadata:
    market: str
    source_url: str
    fetched_at: str
    payload_sha256: str
    symbols: dict[str, ExecutionRules]


def _positive(value: object, label: str) -> float:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExchangeMetadataError(f"INVALID_{label}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ExchangeMetadataError(f"INVALID_{label}")
    return float(parsed)


def _rules_for_symbol(symbol: dict) -> ExecutionRules:
    filters = {item.get("filterType"): item for item in symbol.get("filters", [])}
    try:
        price_tick = _positive(filters["PRICE_FILTER"]["tickSize"], "PRICE_TICK")
        quantity_step = _positive(filters["LOT_SIZE"]["stepSize"], "QUANTITY_STEP")
        notional_filter = filters.get("MIN_NOTIONAL") or filters["NOTIONAL"]
        min_notional = _positive(
            notional_filter.get("notional", notional_filter.get("minNotional")),
            "MIN_NOTIONAL",
        )
    except (KeyError, TypeError) as exc:
        raise ExchangeMetadataError("MISSING_REQUIRED_FILTER") from exc
    return ExecutionRules(
        quantity_step=quantity_step,
        price_tick=price_tick,
        min_notional=min_notional,
        require_liquidity=True,
        liquidation_fee_rate=_positive(symbol.get("liquidationFee", "0.000001"), "LIQUIDATION_FEE"),
    )


def parse_exchange_info(payload: dict, market: str) -> dict[str, ExecutionRules]:
    market = market.upper()
    if market not in OFFICIAL_ENDPOINTS:
        raise ExchangeMetadataError("UNSUPPORTED_MARKET")
    rules = {}
    for item in payload.get("symbols", []):
        if item.get("status") != "TRADING":
            continue
        if market == "USD_M_PERPETUAL" and (
            item.get("contractType") != "PERPETUAL" or item.get("quoteAsset") != "USDT"
        ):
            continue
        symbol = item.get("symbol")
        if isinstance(symbol, str) and symbol:
            rules[symbol] = _rules_for_symbol(item)
    if not rules:
        raise ExchangeMetadataError("NO_ELIGIBLE_SYMBOL_RULES")
    return rules


def refresh_cache(path: str | Path, market: str, timeout_seconds: float = 15) -> CachedExchangeMetadata:
    market = market.upper()
    try:
        source_url = OFFICIAL_ENDPOINTS[market]
    except KeyError as exc:
        raise ExchangeMetadataError("UNSUPPORTED_MARKET") from exc
    request = Request(source_url, headers={"User-Agent": "AGCA-PAPER-METADATA/1"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
    except Exception as exc:
        raise ExchangeMetadataError("OFFICIAL_SOURCE_UNAVAILABLE") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExchangeMetadataError("INVALID_EXCHANGE_INFO_JSON") from exc
    rules = parse_exchange_info(payload, market)
    fetched_at = datetime.now(timezone.utc).isoformat()
    canonical_payload = json.dumps(payload, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical_payload).hexdigest()
    document = {
        "schema_version": 1,
        "market": market,
        "source_url": source_url,
        "fetched_at": fetched_at,
        "payload_sha256": digest,
        "payload": payload,
    }
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    temporary.replace(destination)
    return CachedExchangeMetadata(market, source_url, fetched_at, digest, rules)


def refresh_cache_via_postgres(
    path: str | Path, market: str, database_url: str, timeout_seconds: float = 20,
) -> CachedExchangeMetadata:
    """Fetch official exchangeInfo through the existing Supabase DB egress.

    The SQL contains a fixed allowlisted URL and performs only a GET.  It does
    not expose a public proxy, accept a caller-controlled URL, or place orders.
    """
    market = market.upper()
    try:
        source_url = OFFICIAL_ENDPOINTS[market]
    except KeyError as exc:
        raise ExchangeMetadataError("UNSUPPORTED_MARKET") from exc
    if not database_url:
        raise ExchangeMetadataError("SUPABASE_DATABASE_URL_UNAVAILABLE")
    try:
        import psycopg
        timeout_ms = max(1000, int(timeout_seconds * 1000))
        with psycopg.connect(
            database_url,
            connect_timeout=max(1, int(timeout_seconds)),
            tcp_user_timeout=timeout_ms,
        ) as con:
            with con.cursor() as cursor:
                cursor.execute(f"SET LOCAL statement_timeout = '{timeout_ms}ms'")
                # pgsql-http's default connect/DNS window can be only one second
                # on managed Postgres. Keep the request bounded but large enough
                # for normal DNS resolution in the private Supabase egress path.
                cursor.execute(
                    "SELECT extensions.http_set_curlopt"
                    "('CURLOPT_CONNECTTIMEOUT_MS', %s)",
                    (str(timeout_ms),),
                )
                cursor.execute(
                    "SELECT extensions.http_set_curlopt('CURLOPT_TIMEOUT_MS', %s)",
                    (str(timeout_ms),),
                )
                cursor.execute(
                    "SELECT status, content_type, content "
                    "FROM extensions.http_get(%s)", (source_url,),
                )
                row = cursor.fetchone()
    except Exception as exc:
        raise ExchangeMetadataError("SUPABASE_EXCHANGE_INFO_UNAVAILABLE") from exc
    content_type = str(row[1] or "").split(";", 1)[0].strip().lower() if row else ""
    if not row or row[0] != 200 or content_type != "application/json":
        raise ExchangeMetadataError("OFFICIAL_SOURCE_INVALID_RESPONSE")
    raw = row[2].encode("utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExchangeMetadataError("INVALID_EXCHANGE_INFO_JSON") from exc
    rules = parse_exchange_info(payload, market)
    fetched_at = datetime.now(timezone.utc).isoformat()
    canonical_payload = json.dumps(payload, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical_payload).hexdigest()
    document = {
        "schema_version": 1, "market": market, "source_url": source_url,
        "transport": "SUPABASE_POSTGRES_HTTP", "fetched_at": fetched_at,
        "payload_sha256": digest, "payload": payload,
    }
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    temporary.replace(destination)
    return CachedExchangeMetadata(market, source_url, fetched_at, digest, rules)


def load_cache(path: str | Path, market: str, max_age_seconds: float = 3600) -> CachedExchangeMetadata:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExchangeMetadataError("METADATA_CACHE_UNAVAILABLE") from exc
    market = market.upper()
    if document.get("market") != market or document.get("source_url") != OFFICIAL_ENDPOINTS.get(market):
        raise ExchangeMetadataError("METADATA_PROVENANCE_MISMATCH")
    try:
        fetched = datetime.fromisoformat(document["fetched_at"])
        age = (datetime.now(timezone.utc) - fetched.astimezone(timezone.utc)).total_seconds()
    except (KeyError, TypeError, ValueError) as exc:
        raise ExchangeMetadataError("INVALID_METADATA_TIMESTAMP") from exc
    if age < -60 or age > max_age_seconds:
        raise ExchangeMetadataError("STALE_METADATA_CACHE")
    canonical = json.dumps(document.get("payload"), separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    stored_digest = document.get("payload_sha256")
    if not isinstance(stored_digest, str) or len(stored_digest) != 64:
        raise ExchangeMetadataError("INVALID_METADATA_DIGEST")
    if digest != stored_digest:
        raise ExchangeMetadataError("METADATA_DIGEST_MISMATCH")
    rules = parse_exchange_info(document.get("payload", {}), market)
    return CachedExchangeMetadata(market, document["source_url"], document["fetched_at"], stored_digest, rules)


def rules_for(cache: CachedExchangeMetadata, symbol: str) -> ExecutionRules:
    try:
        return cache.symbols[symbol.upper()]
    except KeyError as exc:
        raise ExchangeMetadataError("SYMBOL_RULES_UNAVAILABLE") from exc
