"""Run one complete live Forward Shadow cycle.

Order is intentionally fixed:
1) collect current RSS news,
2) analyze/classify all stored live news,
3) settle due forward predictions and record newly eligible ones,
4) surface newly eligible high-confidence predictions through the product bridge.

This module does not change the locked forward model. It only orchestrates the
already-defined live pipeline in the correct order.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from analysis import forward_shadow_validator
from news_intelligence import news_analyzer, news_collector
from reports import forward_shadow_product_bridge


def stamp():
    return datetime.now(timezone.utc).isoformat()


def main():
    print("AG Crypto Analyst - Forward Shadow Live Cycle")
    print(f"Başlangıç: {stamp()}")
    print("Akış: NEWS COLLECT -> NEWS ANALYZE -> FORWARD SHADOW -> PRODUCT ALERT BRIDGE")
    print("=" * 80)

    print("\n[1/4] Canlı haberler toplanıyor...")
    news_collector.main()

    print("\n[2/4] Haberler analiz ediliyor...")
    news_analyzer.main()

    print("\n[3/4] Forward Shadow çalıştırılıyor...")
    forward_shadow_validator.main()

    print("\n[4/4] Ürün alarm köprüsü çalıştırılıyor...")
    con = sqlite3.connect(forward_shadow_product_bridge.DB_NAME)
    try:
        sent, pending = forward_shadow_product_bridge.deliver_pending_alerts(con)
        print(f"Ürün alarm köprüsü: gönderilen={sent} | bekleyen={pending}")
    finally:
        con.close()

    print("\n" + "=" * 80)
    print(f"Cycle tamamlandı: {stamp()}")


if __name__ == "__main__":
    main()
