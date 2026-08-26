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


def run_step(name, function, *, blocked_status="ERROR"):
    try:
        result = function()
        if isinstance(result, dict):
            return result
        return {"status": "SUCCESS", "result": result}
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        print(f"{name}: {blocked_status} -> {message}")
        return {"status": blocked_status, "message": message}


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
    news_result = run_step("News Collector", news_collector.main, blocked_status="DATA_BLOCKED")
    results.append(("news", news_result))

    print("\n[2/13] Haberler analiz ediliyor...")
    if news_result.get("status") in {"SUCCESS", "PARTIAL"}:
        results.append(("news_analysis", run_step("News Analyzer", news_analyzer.main)))
    else:
        print("News Analyzer: doğrulanmış yeni haber yok; güvenli biçimde atlandı.")
        results.append(("news_analysis", {"status": "DATA_BLOCKED"}))

    print("\n[3/13] Social Sentiment cadence kontrolü...")
    results.append(("social", run_step("Social Sentiment", social_sentiment_cycle.main)))

    print("\n[4/13] On-chain cadence kontrolü...")
    results.append(("onchain", run_step("On-chain", onchain_cycle.main)))

    print("\n[5/13] Macro cadence kontrolü...")
    results.append(("macro", run_step("Macro", macro_cycle.main)))

    print("\n[6/13] Whale Tracker cadence kontrolü...")
    results.append(("whale", run_step("Whale Tracker", whale_cycle.main)))

    print("\n[7/13] Risk Manager cadence kontrolü...")
    risk_result = run_step("Risk Manager", risk_cycle.main, blocked_status="DATA_BLOCKED")
    results.append(("risk", risk_result))

    print("\n[8/13] Unified Forecast Engine cadence kontrolü...")
    results.append(("forecast", run_step("Unified Forecast", forecast_cycle.main)))

    print("\n[9/13] Forecast Learning Review çalıştırılıyor...")
    results.append(("forecast_learning", run_step("Forecast Learning", forecast_learning_cycle.main)))

    print("\n[10/13] Legacy News Forward Shadow çalıştırılıyor...")
    results.append(("legacy_forward", run_step(
        "Legacy Forward Shadow", forward_shadow_validator.main,
        blocked_status="DATA_BLOCKED",
    )))

    print("\n[11/13] Decision Engine cadence kontrolü...")
    if risk_result.get("status") in {"ERROR", "DATA_BLOCKED"}:
        print("Decision Engine: Risk Manager başarısız; güvenli biçimde atlandı.")
        results.append(("decision", {"status": "DATA_BLOCKED"}))
    else:
        results.append(("decision", run_step("Decision Engine", decision_cycle.main)))

    print("\n[12/13] Report Generator günlük cadence kontrolü...")
    results.append(("report", run_step("Report Generator", report_cycle.main)))

    print("\n[13/13] Ürün alarm köprüsü çalıştırılıyor...")
    if risk_result.get("status") in {"ERROR", "DATA_BLOCKED"}:
        print("Ürün alarm köprüsü: risk verisi bloke; gönderim yapılmadı.")
        results.append(("alert", {"status": "DATA_BLOCKED"}))
    else:
        def deliver_alerts():
            con = runtime_connect(forward_shadow_product_bridge.DB_NAME)
            try:
                sent, pending = forward_shadow_product_bridge.deliver_pending_alerts(con)
                print(f"Ürün alarm köprüsü: gönderilen={sent} | bekleyen={pending}")
                return {"status": "SUCCESS", "sent": sent, "pending": pending}
            finally:
                con.close()
        results.append(("alert", run_step("Product Alert Bridge", deliver_alerts)))

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
