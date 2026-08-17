"""Run the complete live AGCA collection, forecast and validation cycle."""
from __future__ import annotations

import sqlite3
from database.runtime_store import connect as runtime_connect
from datetime import datetime, timezone

from analysis import forecast_learning_cycle, forward_shadow_validator
from decision_engine import decision_cycle, forecast_cycle
from macro import macro_cycle
from news_intelligence import news_analyzer, news_collector
from onchain import onchain_cycle
from reports import forward_shadow_product_bridge, report_cycle
from risk_manager import risk_cycle
from social_sentiment import social_sentiment_cycle
from whale_tracker import whale_cycle


def stamp():
    return datetime.now(timezone.utc).isoformat()


def main():
    print("AG Crypto Analyst - Live Product Cycle")
    print(f"Başlangıç: {stamp()}")
    print(
        "Akış: COLLECTORS -> RISK -> UNIFIED FORECAST -> LEARNING -> "
        "LEGACY FORWARD -> DECISION -> REPORT -> ALERT"
    )
    print("=" * 80)

    results = []

    print("\n[1/13] Canlı haberler toplanıyor...")
    news_collector.main()

    print("\n[2/13] Haberler analiz ediliyor...")
    news_analyzer.main()

    print("\n[3/13] Social Sentiment cadence kontrolü...")
    results.append(("social", social_sentiment_cycle.main()))

    print("\n[4/13] On-chain cadence kontrolü...")
    results.append(("onchain", onchain_cycle.main()))

    print("\n[5/13] Macro cadence kontrolü...")
    results.append(("macro", macro_cycle.main()))

    print("\n[6/13] Whale Tracker cadence kontrolü...")
    results.append(("whale", whale_cycle.main()))

    print("\n[7/13] Risk Manager cadence kontrolü...")
    results.append(("risk", risk_cycle.main()))

    print("\n[8/13] Unified Forecast Engine cadence kontrolü...")
    results.append(("forecast", forecast_cycle.main()))

    print("\n[9/13] Forecast Learning Review çalıştırılıyor...")
    results.append(("forecast_learning", forecast_learning_cycle.main()))

    print("\n[10/13] Legacy News Forward Shadow çalıştırılıyor...")
    results.append(("legacy_forward", forward_shadow_validator.main()))

    print("\n[11/13] Decision Engine cadence kontrolü...")
    results.append(("decision", decision_cycle.main()))

    print("\n[12/13] Report Generator günlük cadence kontrolü...")
    results.append(("report", report_cycle.main()))

    print("\n[13/13] Ürün alarm köprüsü çalıştırılıyor...")
    con = runtime_connect(forward_shadow_product_bridge.DB_NAME)
    try:
        sent, pending = forward_shadow_product_bridge.deliver_pending_alerts(con)
        print(f"Ürün alarm köprüsü: gönderilen={sent} | bekleyen={pending}")
    finally:
        con.close()

    errors = []
    for name, result in results:
        if isinstance(result, dict) and result.get("status") in {"ERROR", "PARTIAL", "DATA_BLOCKED"}:
            errors.append(f"{name}={result.get('status')}")
    final_status = "PARTIAL" if errors else "SUCCESS"
    print("\n" + "=" * 80)
    print(f"Cycle tamamlandı: {stamp()} | status={final_status}")
    if errors:
        print("Sorunlu adımlar: " + ", ".join(errors))
    return {"status": final_status, "errors": errors}


if __name__ == "__main__":
    main()
