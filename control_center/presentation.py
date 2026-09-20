"""Human-readable presentation helpers for AGCA Live Control Center."""
from __future__ import annotations


def _money(value):
    return "—" if value is None else f"{value:,.2f} USDT"


def _price(value):
    return "—" if value is None else f"{value:,.8f}".rstrip("0").rstrip(".")


def _age(seconds):
    if seconds is None:
        return "fiyat zamanı bilinmiyor"
    if seconds < 1:
        return "1 saniyeden kısa süre önce"
    if seconds < 60:
        return f"{seconds:.1f} saniye önce"
    return f"{seconds / 60:.1f} dakika önce"


def operator_view(snapshot):
    """Translate the read model into concise Turkish operator language."""
    system = snapshot["system"]
    portfolio = snapshot["portfolio"]
    positions = []
    for row in snapshot["positions"]:
        stale = row["market_status"] != "CANLI"
        positions.append({
            "başlık": f'{row["symbol"]} — {row["direction"]}',
            "piyasa": (
                "Binance USD-M"
                if row["market_type"] == "USD_M_PERPETUAL"
                else "Binance Spot"
            ),
            "ajanın_gördüğü_fiyat": _price(row["last_price"]),
            "alış_bid": _price(row["best_bid"]),
            "satış_ask": _price(row["best_ask"]),
            "durum_etiketi": "AÇIK",
            "pozisyona_giriş": _price(row["entry_price"]),
            "tp1": _price(row.get("tp1_price")),
            "tp1_durumu": "GERÇEKLEŞTİ" if row.get("tp1_done") else "BEKLİYOR",
            "tp2": _price(row.get("tp2_price")),
            "tp2_durumu": "GERÇEKLEŞTİ" if row.get("tp2_done") else "BEKLİYOR",
            "güncel_stop": _price(row.get("current_stop_price")),
            "anlık_sonuç": _money(row["unrealized_pnl"]),
            "son_fiyat": _age(row["freshness_seconds"]),
            "durum": "GECİKMİŞ" if stale else "CANLI",
            "uyarı": (
                "PİYASA VERİSİ GECİKMİŞ — ajan güncel fiyatı görmüyor olabilir"
                if stale else None
            ),
            "karar_gerekçesi": row["decision_reason"],
        })

    open_count = system["open_positions"]
    if system["market_data_status"] == "CANLI":
        health = "Sistem çalışıyor — canlı piyasa verisi geliyor"
    elif open_count:
        health = "Dikkat — en az bir açık pozisyonun piyasa verisi gecikmiş"
    else:
        health = "Sistem çalışıyor — açık PAPER pozisyon yok"

    close_labels = {
        "PROTECTIVE_STOP": "Koruyucu stop çalıştı",
        "THESIS_INVALIDATED": "İşlem tezi geçerliliğini kaybetti",
        "MAXIMUM_HOLD_TIMEOUT": "Azami bekleme süresi doldu",
    }
    closed = [{
        "başlık": f'{row["symbol"]} — {row["direction"]}',
        "durum_etiketi": "KAPANDI",
        "giriş_fiyatı": _price(row["entry_price"]),
        "kapanış_fiyatı": _price(row["exit_price"]),
        "net_sonuç": _money(row["net_pnl"]),
        "ücretler": _money(row["total_fees"]),
        "kapanış_nedeni": close_labels.get(
            row["close_reason"], row["close_reason"] or "Belirtilmedi"
        ),
        "giriş_zamanı": row["entry_time"],
        "kapanış_zamanı": row["exit_time"],
    } for row in snapshot.get("closed_positions", [])]

    return {
        "durum": health,
        "işlem_modu": "PAPER — gerçek emir kapalı",
        "sistem_sağlığı": (
            "SAĞLIKLI" if system.get("worker_healthy") else "KONTROL GEREKİYOR"
        ),
        "worker_durumu": system.get("worker_status", "UNKNOWN"),
        "son_heartbeat": _age(system.get("heartbeat_age_seconds")),
        "son_aksiyon": system.get("last_action") or "Henüz aksiyon yok",
        "açık_pozisyon": open_count,
        "serbest_nakit": _money(portfolio["cash"]),
        "gerçekleşmiş_sonuç": _money(portfolio["realized_pnl"]),
        "açık_pozisyon_sonucu": _money(portfolio["unrealized_pnl"]),
        "açık_pozisyonlar": positions,
        "kapanan_işlemler": closed,
    }
