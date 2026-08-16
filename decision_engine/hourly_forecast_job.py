"""Run the complete hourly forecast/settlement/learning job."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from analysis import forecast_learning_cycle
from decision_engine import forecast_cycle
from paper_trading import crypto_profit_agent
from risk_manager import risk_cycle


def utcnow():
    return datetime.now(timezone.utc)


def forecast_succeeded_this_hour(now):
    con = sqlite3.connect(forecast_cycle.DB_NAME, timeout=30)
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


def main():
    now = utcnow()
    print("AG Crypto Analyst - Hourly Forecast Job")
    print(f"Başlangıç: {now.isoformat()}")

    risk_result = risk_cycle.main(force=True)
    if forecast_succeeded_this_hour(now):
        print("Unified Forecast: bu UTC saatinde 39 tahmin zaten açıldı; tekrar üretilmedi.")
        forecast_result = {"status": "SKIPPED", "reason": "ALREADY_OPENED_THIS_HOUR"}
    else:
        forecast_result = forecast_cycle.main(force=True)

    paper_result = crypto_profit_agent.main()
    learning_result = forecast_learning_cycle.main()
    blocking = [
        name for name, result in (
            ("risk", risk_result),
            ("forecast", forecast_result),
            ("paper_agent", paper_result),
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
        "paper_agent": paper_result,
        "learning": learning_result,
    }


if __name__ == "__main__":
    main()
