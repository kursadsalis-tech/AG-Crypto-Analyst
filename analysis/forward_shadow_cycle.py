"""Run the complete live AGCA validation and collection cycle."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from analysis import forward_shadow_validator
from macro import macro_cycle
from news_intelligence import news_analyzer, news_collector
from onchain import onchain_cycle
from reports import forward_shadow_product_bridge
from social_sentiment import social_sentiment_cycle
from whale_tracker import whale_cycle


def stamp():
    return datetime.now(timezone.utc).isoformat()


def main():
    print("AG Crypto Analyst - Live Product Cycle")
    print(f"Başlangıç: {stamp()}")
    print(
        "Akış: NEWS -> FORWARD SHADOW -> ALERT BRIDGE -> "
        "SOCIAL SENTIMENT -> ON-CHAIN -> MACRO -> WHALE TRACKER"
    )
    print("=" * 80)

    print("\n[1/8] Canlı haberler toplanıyor...")
    news_collector.main()

    print("\n[2/8] Haberler analiz ediliyor...")
    news_analyzer.main()

    print("\n[3/8] Forward Shadow çalıştırılıyor...")
    forward_shadow_validator.main()

    print("\n[4/8] Ürün alarm köprüsü çalıştırılıyor...")
    con = sqlite3.connect(forward_shadow_product_bridge.DB_NAME)
    try:
        sent, pending = forward_shadow_product_bridge.deliver_pending_alerts(con)
        print(f"Ürün alarm köprüsü: gönderilen={sent} | bekleyen={pending}")
    finally:
        con.close()

    print("\n[5/8] Social Sentiment cadence kontrolü...")
    social_sentiment_cycle.main()

    print("\n[6/8] On-chain cadence kontrolü...")
    onchain_cycle.main()

    print("\n[7/8] Macro cadence kontrolü...")
    macro_cycle.main()

    print("\n[8/8] Whale Tracker cadence kontrolü...")
    whale_cycle.main()

    print("\n" + "=" * 80)
    print(f"Cycle tamamlandı: {stamp()}")


if __name__ == "__main__":
    main()
