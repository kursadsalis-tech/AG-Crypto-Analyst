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
    feed_live = bool(system.get("market_feed_fresh"))
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
            "değerleme_uyarısı": row.get("valuation_error"),
            "trailing": "AKTİF" if row.get("trailing_active") else "BEKLİYOR",
            "anlık_sonuç": _money(row["unrealized_pnl"]),
            "son_fiyat": _age(row["freshness_seconds"]),
            "durum": (
                "SON QUOTE ESKİ" if stale and feed_live
                else "VERİ GECİKİYOR" if stale
                else "CANLI QUOTE"
            ),
            "uyarı": (
                "SON QUOTE ESKİ — global piyasa akışı canlı; bu sembolde son quote 5 sn eşiğini aştı"
                if stale and feed_live
                else "PİYASA VERİSİ GECİKİYOR — ajan güncel fiyatı görmüyor olabilir"
                if stale else None
            ),
            "karar_gerekçesi": row["decision_reason"],
            "karar_akışı": {
                "gözlem": (
                    f'{row["decision_flow"]["observation"]["status"]} — '
                    f'{row["decision_flow"]["observation"]["source"] or "kaynak bilinmiyor"}'
                ),
                "fırsat": row["decision_flow"]["opportunity"]["reason"] or "Gerekçe kaydı yok",
                "karar": (
                    f'{row["decision_flow"]["decision"]["direction"]} — '
                    f'{row["decision_flow"]["decision"]["source"] or "kaynak bilinmiyor"}'
                ),
                "işlem": (
                    f'{row["decision_flow"]["execution"]["status"]} — '
                    f'giriş {_price(row["decision_flow"]["execution"]["entry_price"])} — '
                    f'anlık {_money(row["decision_flow"]["execution"]["unrealized_pnl"])}'
                ),
            },
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

    learning = snapshot.get("learning")
    learning_labels = {
        "COLLECTING_EVIDENCE": "Kanıt birikiyor",
        "CHALLENGER_EVIDENCE_READY": "Challenger kanıtı incelemeye hazır",
        "NO_STABLE_UNDERPERFORMANCE_PATTERN": "Kararlı düşük performans örüntüsü yok",
    }
    risk = snapshot.get("risk", {})
    brain = snapshot.get("brain") or {}
    brain_scan = brain.get("scan") or {}
    brain_reasons = brain_scan.get("reason_counts") or {}
    brain_reason_text = ", ".join(
        f"{key}: {value}" for key, value in sorted(
            brain_reasons.items(), key=lambda item: item[1], reverse=True
        )[:5]
    ) or "engel yok / kayıt yok"
    brain_decision_labels = {
        "NEW_POSITION_SELECTED": "YENİ FIRSAT SEÇİLDİ",
        "CONTINUE_SCANNING": "TARAMAYA DEVAM",
    }
    brain_view = {
        "aşama": brain.get("phase") or "BİLİNMİYOR",
        "mesaj": brain.get("message") or "Canlı beyin telemetrisi henüz yok",
        "zaman": brain.get("at") or "—",
        "gördüğü": (
            f'{brain.get("observed_symbol") or "—"} · '
            f'{brain.get("observed_market_type") or "—"} · '
            f'{_price(brain.get("observed_price"))}'
        ),
        "izlenen_piyasa": brain.get("monitored_symbols", 0),
        "aday": brain_scan.get("candidate_count", 0),
        "seçilen": brain_scan.get("selected_count", 0),
        "aktif_pozisyon": brain_scan.get("active_position_count", 0),
        "kapasite": brain_scan.get("capacity", 0),
        "nedenler": brain_reason_text,
        "karar": brain_decision_labels.get(
            brain.get("decision"), brain.get("decision") or "HENÜZ KARAR YOK"
        ),
        "yeni_fırsat": brain.get("decision") == "NEW_POSITION_SELECTED",
        "mevcut_pozisyonlar": [{
            "işlem": f'{row.get("symbol") or "—"} {row.get("direction") or "—"}',
            "giriş": _price(row.get("entry_price")),
            "ajanın_fiyatı": _price(row.get("mark_price")),
            "açık_sonuç": round(float(row["unrealized_pnl"]), 3) if row.get("unrealized_pnl") is not None else None,
            "tutar": round(float(row.get("notional") or 0), 2),
        } for row in snapshot.get("positions", [])],
        "portföy_kapasitesi": brain.get("portfolio_capacity") or {},
        "fırsat_radarı": [{
            "sıra": row.get("rank"),
            "işlem": f'{row.get("symbol") or "—"} {row.get("direction") or "—"}',
            "ufuk": f'{row.get("horizon_hours") or "—"}s',
            "güven": round(100 * float(row.get("confidence") or 0), 1),
            "beklenen_net_kenar": round(float(row.get("expected_net_edge_pct") or 0), 3),
            "kâr_risk": round(float(row.get("expected_profit_per_risk") or 0), 3),
            "beklenen_kâr": round(float(row.get("expected_profit_usdt") or 0), 3),
            "fiyat": _price(row.get("current_price")),
            "kaynak": row.get("decision_source") or "—",
            "durum": row.get("status") or "DEĞERLENDİRİLİYOR",
            "neden": row.get("reason") or "—",
            "mevcut_pozisyona_göre": (
                "DAHA GÜÇLÜ ADAY"
                if row.get("beats_open_position_on_observed_return")
                else "ROTASYON KANITI YOK"
            ),
            "açık_pozisyon_referansı": (
                round(float(row["open_position_return_reference_pct"]), 3)
                if row.get("open_position_return_reference_pct") is not None else None
            ),
        } for row in brain.get("candidates", [])],
    }
    selection = snapshot.get("selection")
    if selection:
        reasons = selection.get("reason_counts") or {}
        reason_text = ", ".join(
            f"{key}: {value}" for key, value in sorted(
                reasons.items(), key=lambda item: item[1], reverse=True
            )[:4]
        ) or "engel kaydı yok"
        selection_view = {
            "zaman": selection.get("created_at") or "—",
            "durum": selection.get("core_status") or "—",
            "aday": selection.get("candidate_count", 0),
            "seçilen": selection.get("selected_count", 0),
            "aktif_pozisyon": selection.get("active_position_count", 0),
            "kapasite": selection.get("capacity", 0),
            "kullanılabilir_maruz_kalma": _money(selection.get("available_exposure")),
            "nedenler": reason_text,
            "özet": (
                f'{selection.get("candidate_count", 0)} aday incelendi, '
                f'{selection.get("selected_count", 0)} işlem seçildi. '
                f'Başlıca nedenler: {reason_text}.'
            ),
        }
    else:
        selection_view = {
            "zaman": "—", "durum": "KAYIT YOK", "aday": 0, "seçilen": 0,
            "aktif_pozisyon": 0, "kapasite": 0,
            "kullanılabilir_maruz_kalma": "—", "nedenler": "—",
            "özet": "Henüz kanonik fırsat taraması kaydı yok.",
        }
    return {
        "visibility": snapshot.get("visibility", {}),
        "durum": system.get("pipeline_explanation") or health,
        "gerçek_çalışma_durumu": system.get("pipeline_state", "BİLİNMİYOR"),
        "çalışma_açıklaması": system.get("pipeline_explanation", "—"),
        "izlenen_piyasa": (
            f'{system.get("observed_symbols", 0)} sembol · akış canlı · '
            f'{system.get("fresh_market_symbols", 0)} son quote ≤5 sn'
            if feed_live
            else f'{system.get("observed_symbols", 0)} sembol · akış güncel değil'
        ),
        "piyasa_akış_güncelliği": _age(system.get("market_snapshot_age_seconds")),
        "son_gerçek_iş": _age(system.get("latest_work_age_seconds")),
        "kanonik_tarama_yaşı": _age(system.get("brain_scan_age_seconds")),
        "kanonik_tarama_güncel": "EVET" if system.get("brain_scan_fresh") else "HAYIR",
        "işlem_modu": "PAPER — gerçek emir kapalı",
        "sistem_sağlığı": (
            "SAĞLIKLI" if system.get("worker_healthy") else "KONTROL GEREKİYOR"
        ),
        "worker_durumu": system.get("worker_status", "UNKNOWN"),
        "son_heartbeat": _age(system.get("heartbeat_age_seconds")),
        "son_aksiyon": system.get("last_action") or "Henüz aksiyon yok",
        "açık_pozisyon": open_count,
        "toplam_kasa": _money(portfolio.get("total_equity")),
        "başlangıç_kasası": _money(portfolio.get("initial_capital")),
        "toplam_getiri": (
            "—" if portfolio.get("total_return_pct") is None
            else f'{portfolio["total_return_pct"]:+.2f}%'
        ),
        "serbest_nakit": _money(portfolio["cash"]),
        "teminat": _money(portfolio.get("reserved_margin")),
        "brüt_maruz_kalma": _money(portfolio.get("gross_exposure")),
        "açık_pozisyon_tutarı": _money(portfolio.get("open_position_notional")),
        "gerçekleşmiş_sonuç": _money(portfolio["realized_pnl"]),
        "açık_pozisyon_sonucu": _money(portfolio["unrealized_pnl"]),
        "ajan_beyni": brain_view,
        "düşünce_akışı": [{
            "zaman": row.get("at") or "—",
            "aşama": row.get("phase") or "—",
            "piyasa": f'{row.get("symbol") or "—"} · {row.get("market_type") or "—"}',
            "fiyat": _price(row.get("price")),
            "mesaj": row.get("message") or "—",
            "karar": brain_decision_labels.get(
                row.get("decision"), row.get("decision") or "—"
            ),
            "aday": (row.get("scan") or {}).get("candidate_count", 0),
            "seçilen": (row.get("scan") or {}).get("selected_count", 0),
            "yeni_fırsat": row.get("decision") == "NEW_POSITION_SELECTED",
        } for row in reversed(snapshot.get("brain_timeline", [])[-30:])],
        "son_fırsat_taraması": selection_view,
        "aktivite": [{
            "aşama": row.get("name"),
            "son_aktivite": _age(row.get("age_seconds")),
            "zaman": row.get("last_at") or "—",
        } for row in snapshot.get("activity", {}).get("stages", [])],
        "açık_pozisyonlar": positions,
        "piyasa": [{
            "symbol": row.get("symbol"), "market_type": row.get("market_type"),
            "raw_price": row.get("last_price"),
            "başlık": f'{row.get("symbol") or "—"} — {row.get("market_type") or "—"}',
            "son_fiyat": _price(row.get("last_price")),
            "bid": _price(row.get("best_bid")),
            "ask": _price(row.get("best_ask")),
            "kaynak": row.get("quote_source") or "kaynak bilinmiyor",
            "güncellik": _age(row.get("freshness_seconds")),
            "durum": (
                "CANLI QUOTE"
                if row.get("market_status") == "CANLI"
                else "SON QUOTE ESKİ" if feed_live
                else "VERİ GECİKİYOR"
            ),
            "book_imbalance": row.get("book_imbalance"),
            "taker_imbalance": row.get("taker_imbalance"),
            "görünür_likidite": row.get("visible_quote_volume"),
        } for row in snapshot.get("market", [])],
        "risk": {
            "mod": "PAPER — gerçek emir kapalı",
            "açık_pozisyon": risk.get("open_positions", 0),
            "gecikmiş_pozisyon": risk.get("stale_positions", 0),
            "pozisyonlar": risk.get("positions", []),
        },
        "öğrenme": ({
            "durum": learning_labels.get(learning["status"], learning["status"]),
            "kapanan_kanıtlı_işlem": learning["closed_count"],
            "bağımsız_hafta": learning["independent_weeks"],
            "anlık_adaptasyon": "Yeni deneyim sonraki kararları besleyebilir; çalışma durumu aşağıdaki canlı kanıttan doğrulanır",
            "kalıcı_politika_terfisi": (
                "DOĞRULANDI" if learning["promotion_allowed"]
                else "HENÜZ DOĞRULANMADI"
            ),
            "sürüm": learning["learning_version"],
        } if learning else {
            "durum": "Henüz öğrenme incelemesi yok",
            "kapanan_kanıtlı_işlem": 0,
            "bağımsız_hafta": 0,
            "anlık_adaptasyon": "Yeni deneyim sonraki kararları besleyebilir; çalışma durumu aşağıdaki canlı kanıttan doğrulanır",
            "kalıcı_politika_terfisi": "HENÜZ DOĞRULANMADI",
            "sürüm": "—",
        }),
        "olaylar": [{
            "önem": row.get("severity", "BİLGİ"),
            "tür": row.get("kind", "SYSTEM"),
            "mesaj": row.get("message") or "Ayrıntı yok",
            "durum": row.get("status") or "—",
            "yaş": _age(row.get("age_seconds")) if row.get("age_seconds") is not None else "—",
            "zaman": row.get("created_at") or "—",
            "ajan_fiyatı": _price(row.get("agent_price")),
            "kontrol_fiyatı": _price(row.get("operator_price")),
            "fark": _price(row.get("price_difference")),
            "fark_yüzde": (
                "—" if row.get("price_difference_pct") is None
                else f'{row["price_difference_pct"]:+.4f}%'
            ),
            "kaynak": row.get("agent_quote_source") or "—",
            "not": row.get("note") or "—",
        } for row in snapshot.get("incidents", [])],
        "raporlar": [{
            "tarih": row.get("report_date") or "—",
            "oluşturulma": row.get("created_at") or "—",
            "özet": row.get("report") or {},
        } for row in snapshot.get("reports", [])],
        "kapanan_işlemler": closed,
    }
