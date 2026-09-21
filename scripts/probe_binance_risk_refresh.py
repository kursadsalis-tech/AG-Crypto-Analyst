#!/usr/bin/env python3
"""Read-only full risk source/coverage acceptance: NO SQLite writes or PAPER actions."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

from paper_trading.exchange_metadata import load_cache
from risk_manager import risk_manager as risk

MAX_DYNAMIC = 6


def shadow_risk(*, db_path=None, metadata_path=None, database_url=None):
    if os.getenv("AGCA_EXECUTION_MODE", "PAPER_ONLY") != "PAPER_ONLY":
        raise RuntimeError("PAPER_ONLY_REQUIRED")
    database_url = (os.getenv("AGCA_REMOTE_DATABASE_URL", "")
                    if database_url is None else database_url)
    if not database_url:
        raise RuntimeError("PRIVATE_RISK_EGRESS_NOT_CONFIGURED")
    db_path = db_path or os.getenv("AGCA_DATABASE_PATH", "/var/lib/agca/crypto_data.db")
    metadata_path = metadata_path or os.getenv(
        "AGCA_EXCHANGE_METADATA_CACHE", "/var/lib/agca/exchange-info-usdm.json")
    cache = load_cache(
        metadata_path, "USD_M_PERPETUAL", max_age_seconds=float(
            os.getenv("AGCA_EXCHANGE_METADATA_MAX_AGE_SECONDS", "7200")
        ),
    )
    with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.execute("PRAGMA query_only=ON")
        names = risk.behavior_risk_symbols(con)
    dynamic = tuple(symbol for symbol in names if symbol in cache.symbols)
    missing = sorted(set(names) - set(dynamic))
    if missing or len(dynamic) > MAX_DYNAMIC:
        return {"mode": "READ_ONLY_NO_SQLITE_WRITES",
                "status": "BLOCKED_DYNAMIC_UNIVERSE",
                "native_dynamic_count": len(dynamic),
                "unrecognised_count": len(missing),
                "reason": ("NON_NATIVE_OR_DELISTED_SYMBOL" if missing
                           else "UNBOUNDED_DYNAMIC_RISK_BATCH"),
                "snapshot_written": False}
    errors = {}
    try:
        deviation = risk.btc_independent_validation(database_url)["deviation_pct"]
        independent_btc_ok = True
    except Exception as exc:
        independent_btc_ok, deviation = False, None
        errors["CROSS_SOURCE_BTC"] = type(exc).__name__
    assets = []
    for symbol in dict.fromkeys((*risk.SYMBOLS, *dynamic)):
        try:
            candles = (risk.fetch_dynamic_closed_1h_klines(symbol)
                       if symbol in dynamic
                       else risk.spot_closed_hourly(database_url, symbol))
            assets.append(risk.classify_asset_risk(risk.candle_metrics(symbol, candles)))
        except Exception as exc:
            errors[symbol] = type(exc).__name__
    policy = risk.build_global_policy(
        assets, errors, datetime.now(timezone.utc).isoformat())
    return {
        "mode": "READ_ONLY_NO_SQLITE_WRITES",
        "status": "COMPLETE" if not errors else "PARTIAL_OR_BLOCKED",
        "source_exchange": "BINANCE",
        "reference_market": "SPOT",
        "dynamic_market": "USD_M_PERPETUAL",
        "independent_btc_ok": independent_btc_ok,
        "independent_btc_deviation_pct": deviation,
        "native_dynamic_count": len(dynamic),
        "reference_count": len(risk.SYMBOLS),
        "risk_assets_computed": len(assets),
        "market_state": policy["market_state"],
        "risk_coverage": f"{policy['coverage_count']}/{policy['coverage_total']}",
        "new_positions_allowed_if_written": policy["new_positions_allowed"],
        "source_error_types": errors,
        "snapshot_written": False,
        "real_orders": False,
    }


if __name__ == "__main__":
    print(json.dumps(shadow_risk(), sort_keys=True))
