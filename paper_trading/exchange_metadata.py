"""Validated exchange-rule cache for PAPER execution.

The cache is deliberately fail-closed: rules are usable only when they came
from the configured market's official exchangeInfo endpoint and are fresh.
This module contains no credentials or order-placement capability.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
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
    max_age_seconds: float = 7200


def _positive(value: object, label: str) -> float:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExchangeMetadataError(f"INVALID_{label}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ExchangeMetadataError(f"INVALID_{label}")
    result = float(parsed)
    if not math.isfinite(result) or result <= 0:
        raise ExchangeMetadataError(f"INVALID_{label}")
    return result


def _rules_for_symbol(symbol: dict) -> ExecutionRules:
    filter_rows = symbol.get("filters")
    if not isinstance(filter_rows, list) or not all(isinstance(item, dict) for item in filter_rows):
        raise ExchangeMetadataError("INVALID_EXCHANGE_INFO_FILTERS")
    filters = {item.get("filterType"): item for item in filter_rows}
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
    if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
        raise ExchangeMetadataError("INVALID_EXCHANGE_INFO_SCHEMA")
    rules = {}
    for item in payload["symbols"]:
        if not isinstance(item, dict):
            raise ExchangeMetadataError("INVALID_EXCHANGE_INFO_SYMBOL")
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
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ExchangeMetadataError("INVALID_METADATA_SCHEMA")
    if document.get("market") != market or document.get("source_url") != OFFICIAL_ENDPOINTS.get(market):
        raise ExchangeMetadataError("METADATA_PROVENANCE_MISMATCH")
    try:
        fetched = datetime.fromisoformat(document["fetched_at"])
        if fetched.tzinfo is None:
            raise ValueError("timezone required")
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
    return CachedExchangeMetadata(
        market, document["source_url"], document["fetched_at"], stored_digest, rules,
        max_age_seconds=max_age_seconds,
    )


def rules_for(cache: CachedExchangeMetadata, symbol: str) -> ExecutionRules:
    # A process may outlive the refresh interval. An old in-memory cache must
    # never keep authorizing new exposure after its validated freshness expires.
    if isinstance(cache, CachedExchangeMetadata):
        fetched = datetime.fromisoformat(cache.fetched_at)
        if fetched.tzinfo is None:
            raise ExchangeMetadataError("INVALID_METADATA_TIMESTAMP")
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
        if age < -60 or age > cache.max_age_seconds:
            raise ExchangeMetadataError("STALE_METADATA_CACHE")
    try:
        return cache.symbols[symbol.upper()]
    except KeyError as exc:
        raise ExchangeMetadataError("SYMBOL_RULES_UNAVAILABLE") from exc


FROZEN_RULES_CONTRACT_VERSION = "PAPER_EXECUTION_RULES_V1"


def load_market_caches(paths, max_age_seconds=7200):
    """Load routes independently; absence disables entries on that route only."""
    available, health = {}, {}
    for market, path in paths.items():
        try:
            cache = load_cache(path, market, max_age_seconds=max_age_seconds)
        except ExchangeMetadataError as exc:
            health[market] = {"status": "UNAVAILABLE", "reason": str(exc)}
            continue
        available[market] = cache
        health[market] = {
            "status": "READY", "reason": None, "symbols": len(cache.symbols),
            "fetched_at": cache.fetched_at, "payload_sha256": cache.payload_sha256,
            "source_url": cache.source_url,
        }
    return available, health


def refresh_cache_bounded(path, market, database_url="", timeout_seconds=10):
    """One direct official GET, then one optional existing DB-egress fallback.

    Run only in the support refresh process. No retry loop, dependency on the
    mirror, unvalidated cached fallback, or network work enters the PAPER loop.
    An unsuccessful refresh leaves the previously validated cache untouched.
    """
    try:
        return refresh_cache(path, market, timeout_seconds=timeout_seconds)
    except ExchangeMetadataError as direct_error:
        if not database_url:
            raise ExchangeMetadataError("DIRECT_METADATA_REFRESH_UNAVAILABLE") from direct_error
        try:
            return refresh_cache_via_postgres(
                path, market, database_url, timeout_seconds=timeout_seconds,
            )
        except ExchangeMetadataError as fallback_error:
            raise ExchangeMetadataError("ALL_METADATA_TRANSPORTS_UNAVAILABLE") from fallback_error


def freeze_execution_rules(rules, symbol, market, metadata=None,
                           maintenance_margin_rate=None):
    """Validated actual-fill contract; persist inside immutable ENTRY_FILLED."""
    if not isinstance(rules, ExecutionRules):
        raise ExchangeMetadataError("INVALID_FROZEN_EXECUTION_RULES")
    payload = {
        "contract_version": FROZEN_RULES_CONTRACT_VERSION,
        "symbol": symbol.upper(), "market": market,
        "rules": asdict(rules),
    }
    if metadata is not None:
        if not isinstance(metadata, CachedExchangeMetadata) or (
            metadata.market != market or rules_for(metadata, symbol) != rules
        ):
            raise ExchangeMetadataError("FROZEN_RULES_METADATA_MISMATCH")
        payload["metadata"] = {
            "source_url": metadata.source_url, "fetched_at": metadata.fetched_at,
            "payload_sha256": metadata.payload_sha256,
        }
    if maintenance_margin_rate is not None:
        rate = float(maintenance_margin_rate)
        if not 0 <= rate < 1:
            raise ExchangeMetadataError("INVALID_FROZEN_MAINTENANCE_MARGIN")
        payload["maintenance_margin_rate"] = rate
    rules_from_frozen(payload, symbol, market)
    return payload


def rules_from_frozen(payload, symbol, market):
    """Validate a frozen fill contract without requiring live helper metadata."""
    if not isinstance(payload, dict) or (
        payload.get("contract_version") != FROZEN_RULES_CONTRACT_VERSION
        or payload.get("symbol") != symbol.upper()
        or payload.get("market") != market
        or market not in OFFICIAL_ENDPOINTS
    ):
        raise ExchangeMetadataError("FROZEN_RULES_CONTRACT_MISMATCH")
    if "maintenance_margin_rate" in payload:
        try:
            rate = float(payload["maintenance_margin_rate"])
        except (TypeError, ValueError) as exc:
            raise ExchangeMetadataError("INVALID_FROZEN_MAINTENANCE_MARGIN") from exc
        if not 0 <= rate < 1:
            raise ExchangeMetadataError("INVALID_FROZEN_MAINTENANCE_MARGIN")
    values = payload.get("rules")
    if not isinstance(values, dict) or set(values) != {
        "quantity_step", "price_tick", "min_notional", "require_liquidity",
        "liquidation_fee_rate",
    } or values.get("require_liquidity") is not True:
        raise ExchangeMetadataError("INVALID_FROZEN_EXECUTION_RULES")
    fields = {key: _positive(values[key], "FROZEN_" + key.upper())
              for key in ("quantity_step", "price_tick", "min_notional")}
    try:
        fee = Decimal(str(values["liquidation_fee_rate"]))
    except (InvalidOperation, ValueError) as exc:
        raise ExchangeMetadataError("INVALID_FROZEN_LIQUIDATION_FEE") from exc
    if not fee.is_finite() or fee < 0 or fee >= 1:
        raise ExchangeMetadataError("INVALID_FROZEN_LIQUIDATION_FEE")
    return ExecutionRules(**fields, require_liquidity=True, liquidation_fee_rate=float(fee))
