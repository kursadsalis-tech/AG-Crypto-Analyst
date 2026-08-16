"""Audit Deribit public option data for a forward-only forecast source.

This module does not create a forecast or read a price outcome.  It checks
whether the current BTC/ETH option surface is sufficiently observable to
support a later, frozen 24-hour market-implied distribution collector.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


VERSION = "OPTIONS_IMPLIED_FEASIBILITY_V1_1"
BASE_URL = "https://www.deribit.com/api/v2"
BYBIT_URL = "https://api.bybit.com/v5/market"
CURRENCIES = ("BTC", "ETH")
AUDIT_PATH = Path("reports/options_implied_feasibility.json")
DETAIL_PATH = Path("reports/options_implied_surface.csv")
MIN_ACTIVE_OPTIONS = 40
MIN_EXPIRIES = 3
MIN_TWO_SIDED_TOTAL = 20
MIN_LIQUID_STRIKES_NEAR_24H = 6
TARGET_MIN_HOURS = 8.0
TARGET_MAX_HOURS = 72.0


def finite(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def rpc(session, method, **params):
    url = f"{BASE_URL}/public/{method}?{urlencode(params)}"
    if session is None:
        with urlopen(Request(url, headers={"User-Agent": "AG-Crypto-Analyst/1.0"}),
                     timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    else:
        response = session.get(url, timeout=12)
        response.raise_for_status()
        payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"Deribit {method}: {payload['error']}")
    return payload.get("result")


def bybit_rpc(session, method, **params):
    url = f"{BYBIT_URL}/{method}?{urlencode(params)}"
    if session is None:
        with urlopen(Request(url, headers={"User-Agent": "AG-Crypto-Analyst/1.0"}),
                     timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    else:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        payload = response.json()
    if payload.get("retCode") != 0:
        raise RuntimeError(
            f"Bybit {method}: {payload.get('retCode')} {payload.get('retMsg')}"
        )
    return payload.get("result") or {}


def load_deribit_currency(session, currency, now_ms):
    instruments = rpc(
        session, "get_instruments", currency=currency,
        kind="option", expired="false"
    )
    summaries = rpc(
        session, "get_book_summary_by_currency",
        currency=currency, kind="option"
    )
    return parse_surface(currency, instruments, summaries, now_ms)


def load_bybit_currency(session, currency, now_ms):
    instruments = []
    cursor = ""
    while True:
        params = {"category": "option", "baseCoin": currency, "limit": 500}
        if cursor:
            params["cursor"] = cursor
        result = bybit_rpc(session, "instruments-info", **params)
        instruments.extend(result.get("list") or [])
        cursor = result.get("nextPageCursor") or ""
        if not cursor:
            break
    ticker_result = bybit_rpc(
        session, "tickers", category="option", baseCoin=currency
    )
    metadata = []
    summaries = []
    for row in instruments:
        name = row.get("symbol")
        metadata.append({
            "instrument_name": name,
            "expiration_timestamp": row.get("deliveryTime"),
            "strike": row.get("strike"),
            "option_type": str(row.get("optionsType") or "").lower(),
        })
    for row in ticker_result.get("list") or []:
        summaries.append({
            "instrument_name": row.get("symbol"),
            "bid_price": row.get("bid1Price"),
            "ask_price": row.get("ask1Price"),
            "mark_iv": row.get("markIv"),
            "open_interest": row.get("openInterest"),
            "volume": row.get("volume24h"),
        })
    return parse_surface(currency, metadata, summaries, now_ms)


def parse_surface(currency, instruments, summaries, now_ms):
    metadata = {row["instrument_name"]: row for row in instruments}
    rows = []
    for quote in summaries:
        name = quote.get("instrument_name")
        instrument = metadata.get(name)
        if not instrument:
            continue
        expiry_ms = instrument.get("expiration_timestamp")
        strike = instrument.get("strike")
        if not finite(expiry_ms) or not finite(strike):
            continue
        hours = (float(expiry_ms) - now_ms) / 3_600_000.0
        if hours <= 0:
            continue
        bid = quote.get("bid_price")
        ask = quote.get("ask_price")
        mark_iv = quote.get("mark_iv")
        open_interest = quote.get("open_interest")
        rows.append({
            "version": VERSION,
            "captured_at": datetime.fromtimestamp(
                now_ms / 1000.0, tz=timezone.utc
            ).isoformat(),
            "currency": currency,
            "instrument_name": name,
            "expiration_timestamp": int(float(expiry_ms)),
            "hours_to_expiry": hours,
            "strike": float(strike),
            "option_type": instrument.get("option_type"),
            "bid_price": float(bid) if finite(bid) else None,
            "ask_price": float(ask) if finite(ask) else None,
            "mark_iv": float(mark_iv) if finite(mark_iv) else None,
            "open_interest": float(open_interest) if finite(open_interest) else 0.0,
            "volume_24h": float(quote.get("volume"))
            if finite(quote.get("volume")) else 0.0,
            "two_sided": bool(finite(bid) and finite(ask)
                              and float(bid) > 0 and float(ask) > 0
                              and float(ask) >= float(bid)),
        })
    return rows


def audit_currency(currency, rows):
    expiries = defaultdict(list)
    for row in rows:
        expiries[row["expiration_timestamp"]].append(row)
    candidates = [
        (expiry, items) for expiry, items in expiries.items()
        if TARGET_MIN_HOURS <= items[0]["hours_to_expiry"] <= TARGET_MAX_HOURS
    ]
    candidates.sort(key=lambda item: abs(item[1][0]["hours_to_expiry"] - 24.0))
    target = candidates[0] if candidates else None
    target_rows = target[1] if target else []
    liquid_strikes = {
        row["strike"] for row in target_rows
        if row["two_sided"] and row["mark_iv"] is not None
        and row["open_interest"] > 0
    }
    checks = {
        "active_options_gte_40": len(rows) >= MIN_ACTIVE_OPTIONS,
        "expiries_gte_3": len(expiries) >= MIN_EXPIRIES,
        "two_sided_total_gte_20": sum(row["two_sided"] for row in rows)
        >= MIN_TWO_SIDED_TOTAL,
        "near_24h_expiry_available": target is not None,
        "near_24h_liquid_strikes_gte_6": len(liquid_strikes)
        >= MIN_LIQUID_STRIKES_NEAR_24H,
    }
    return {
        "currency": currency,
        "active_options": len(rows),
        "expiries": len(expiries),
        "two_sided": sum(row["two_sided"] for row in rows),
        "mark_iv": sum(row["mark_iv"] is not None for row in rows),
        "positive_open_interest": sum(row["open_interest"] > 0 for row in rows),
        "target_expiry_hours": target_rows[0]["hours_to_expiry"]
        if target_rows else None,
        "target_contracts": len(target_rows),
        "target_liquid_strikes": len(liquid_strikes),
        "option_types": dict(Counter(row["option_type"] for row in rows)),
        "checks": checks,
        "ready": all(checks.values()),
    }


def write_csv(rows):
    DETAIL_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with DETAIL_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if fields:
            writer.writeheader()
            writer.writerows(rows)


def run(session=None, now_ms=None):
    now_ms = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    selected_provider = None
    all_rows, audits, errors = [], [], []
    attempts = []
    providers = (
        ("Deribit public JSON-RPC", load_deribit_currency),
        ("Bybit public V5 REST", load_bybit_currency),
    )
    for provider_name, loader in providers:
        provider_rows, provider_audits, provider_errors = [], [], []
        for currency in CURRENCIES:
            try:
                rows = loader(session, currency, now_ms)
                provider_rows.extend(rows)
                provider_audits.append(audit_currency(currency, rows))
            except Exception as exc:  # source isolation is part of the audit
                provider_errors.append({
                    "provider": provider_name,
                    "currency": currency,
                    "error": str(exc),
                })
                # Both currencies are mandatory. A provider-level transport or
                # schema failure cannot yield a usable partial surface.
                break
        attempts.append({
            "provider": provider_name,
            "currencies_loaded": len(provider_audits),
            "errors": provider_errors,
        })
        if not provider_errors and len(provider_audits) == len(CURRENCIES):
            selected_provider = provider_name
            all_rows, audits, errors = (
                provider_rows, provider_audits, provider_errors
            )
            break
        errors.extend(provider_errors)
    audit = {
        "version": VERSION,
        "captured_at": datetime.fromtimestamp(
            now_ms / 1000.0, tz=timezone.utc
        ).isoformat(),
        "source": selected_provider,
        "source_attempts": attempts,
        "scope": "BTC/ETH, forward-only, 24h primary",
        "currencies": audits,
        "source_errors": errors,
        "ready": selected_provider is not None and len(audits) == len(CURRENCIES)
        and all(row["ready"] for row in audits),
        "historical_claim": False,
        "forecast_computed": False,
        "price_outcome_read": False,
    }
    write_csv(all_rows)
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIT_PATH.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return audit


def render(audit):
    status = "SOURCE_FEASIBLE" if audit["ready"] else "SOURCE_BLOCKED"
    print("AG Crypto Analyst - Options-Implied Source Feasibility v1.1.0")
    print(f"Source: {audit['source'] or 'NONE'} | status={status}")
    print("Scope: BTC/ETH | horizon=24h PRIMARY | history=FORWARD_ONLY")
    for row in audit["currencies"]:
        hours = row["target_expiry_hours"]
        hours_text = "N/A" if hours is None else f"{hours:.1f}h"
        print(
            f"{row['currency']}: options={row['active_options']} | "
            f"expiries={row['expiries']} | two_sided={row['two_sided']} | "
            f"target={hours_text} | liquid_strikes={row['target_liquid_strikes']} | "
            f"ready={'YES' if row['ready'] else 'NO'}"
        )
        failed = [key for key, value in row["checks"].items() if not value]
        if failed:
            print("failed: " + ",".join(failed))
    for attempt in audit["source_attempts"]:
        if attempt["errors"]:
            reason = "; ".join(
                f"{row['currency']}={row['error']}" for row in attempt["errors"]
            )
            print(f"Fallback: {attempt['provider']} unavailable -> {reason}")
    print("No probability, target price, return or backtest result was computed.")
    print(f"Audit: {AUDIT_PATH}")
    print(f"Surface: {DETAIL_PATH}")


def self_test():
    now = 1_700_000_000_000
    instruments = []
    summaries = []
    for expiry_hours in (24, 48, 168):
        for strike in range(80, 121, 5):
            for option_type, suffix in (("call", "C"), ("put", "P")):
                name = f"BTC-X-{strike}-{suffix}-{expiry_hours}"
                instruments.append({
                    "instrument_name": name,
                    "expiration_timestamp": now + expiry_hours * 3_600_000,
                    "strike": strike,
                    "option_type": option_type,
                })
                summaries.append({
                    "instrument_name": name, "bid_price": 0.01,
                    "ask_price": 0.02, "mark_iv": 60.0,
                    "open_interest": 1.0, "volume": 1.0,
                })
    rows = parse_surface("BTC", instruments, summaries, now)
    result = audit_currency("BTC", rows)
    assert result["ready"] and result["target_expiry_hours"] == 24.0
    print("Options-implied feasibility self-test: PASS")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        render(run())


if __name__ == "__main__":
    main()
