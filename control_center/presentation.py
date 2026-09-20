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
            "pozisyona_giriş": _price(row["entry_price"]),
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

    return {
        "durum": health,
        "işlem_modu": "PAPER — gerçek emir kapalı",
        "açık_pozisyon": open_count,
        "serbest_nakit": _money(portfolio["cash"]),
        "gerçekleşmiş_sonuç": _money(portfolio["realized_pnl"]),
        "açık_pozisyon_sonucu": _money(portfolio["unrealized_pnl"]),
        "pozisyonlar": positions,
    }
