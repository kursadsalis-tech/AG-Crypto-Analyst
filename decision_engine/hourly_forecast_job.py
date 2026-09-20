"""Run the complete hourly forecast/settlement/learning job."""
from __future__ import annotations

import sqlite3
from database.runtime_store import connect as runtime_connect
from datetime import datetime, timezone
import os
from pathlib import Path

from analysis import forecast_learning_cycle
from decision_engine import forecast_cycle
from paper_trading.exchange_metadata import (
    ExchangeMetadataError,
    load_cache,
    refresh_cache_via_postgres,
)
from risk_manager import risk_cycle


def utcnow():
    return datetime.now(timezone.utc)


def forecast_succeeded_this_hour(now):
    con = runtime_connect(forecast_cycle.DB_NAME, timeout=30)
    try:
        forecast_cycle.ensure_state_table(con)
        row = con.execute(
            "SELECT last_success_at FROM agent_run_state WHERE agent_name=?",
            (forecast_cycle.AGENT_NAME,),
        ).fetchone()
        last = forecast_cycle.parse_time(row[0]) if row else None
        if last is None:
            return False
        return (
            last.year, last.month, last.day, last.hour
        ) == (
            now.year, now.month, now.day, now.hour
        )
    finally:
        con.close()


def _refresh_exchange_metadata(cache_path):
    """Prefer live official metadata; accept only a still-fresh verified cache."""
    max_age = float(os.getenv("AGCA_EXCHANGE_METADATA_MAX_AGE_SECONDS", "7200"))
    try:
        return refresh_cache_via_postgres(
            cache_path, "USD_M_PERPETUAL",
            os.getenv("AGCA_REMOTE_DATABASE_URL", ""),
        ), "LIVE_REFRESH"
    except ExchangeMetadataError:
        return load_cache(
            cache_path, "USD_M_PERPETUAL", max_age_seconds=max_age,
        ), "FRESH_VERIFIED_CACHE"


def main():
    now = utcnow()
    print("AG Crypto Analyst - Hourly Forecast Job")
    print(f"Başlangıç: {now.isoformat()}")

    # Forecast/risk maintenance is not a PAPER execution authority.
    # The independent minute selection service and realtime position manager
    # own PAPER entry/exit; never replay their lifecycle on the hourly clock.
    try:
        risk_result = risk_cycle.main(force=True)
    except Exception as exc:
        risk_result = {"status": "ERROR", "message": str(exc)[:300]}

    try:
        if forecast_succeeded_this_hour(now):
            print("Unified Forecast: bu UTC saatinde 39 tahmin zaten açıldı; tekrar üretilmedi.")
            forecast_result = {"status": "SKIPPED", "reason": "ALREADY_OPENED_THIS_HOUR"}
        else:
            forecast_result = forecast_cycle.main(force=True)
    except Exception as exc:
        forecast_result = {"status": "ERROR", "message": str(exc)[:300]}

    metadata_result = None
    if os.getenv("AGCA_REQUIRE_LIVE_EXCHANGE_RULES", "1") == "1":
        cache_path = Path(os.getenv(
            "AGCA_EXCHANGE_METADATA_CACHE",
            "/var/lib/agca/exchange-info-usdm.json",
        ))
        try:
            metadata, metadata_transport = _refresh_exchange_metadata(cache_path)
            metadata_result = {
                "status": "SUCCESS", "market": metadata.market,
                "symbols": len(metadata.symbols), "fetched_at": metadata.fetched_at,
                "source_url": metadata.source_url,
                "transport": metadata_transport,
            }
        except Exception as exc:
            metadata_result = {"status": "ERROR", "message": str(exc)[:300]}
    try:
        learning_result = forecast_learning_cycle.main()
    except Exception as exc:
        learning_result = {"status": "ERROR", "message": str(exc)[:300]}
    blocking = [
        name for name, result in (
            ("risk", risk_result),
            ("forecast", forecast_result),
            ("exchange_metadata", metadata_result),
            ("learning", learning_result),
        )
        if isinstance(result, dict)
        and result.get("status") in {"ERROR", "PARTIAL", "DATA_BLOCKED"}
    ]
    status = "PARTIAL" if blocking else "SUCCESS"
    print(f"Hourly Forecast Job: {status}")
    if blocking:
        print("Sorunlu adımlar: " + ", ".join(blocking))
    return {
        "status": status,
        "risk": risk_result,
        "forecast": forecast_result,
        "exchange_metadata": metadata_result,
        "learning": learning_result,
    }


if __name__ == "__main__":
    main()
