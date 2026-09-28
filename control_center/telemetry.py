"""Bounded read-only projections; never starts workers or makes decisions."""
import json
import os
from pathlib import Path


def health_file(filename, now):
    path = Path(os.getenv("AGCA_STATE_DIR", "/var/lib/agca")) / filename
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("not an object")
        from datetime import datetime
        stamp = data.get("at") or data.get("completed_at")
        age = now.timestamp() - datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
        return {"snapshot_age_seconds": age, "snapshot_status": "CURRENT" if 0 <= age <= 120 else "STALE", **data}
    except (OSError, ValueError, TypeError, AttributeError):
        return {"snapshot_status": "UNKNOWN"}


def rows(con, table, fields, order, where="1", limit=50):
    columns = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    if not set(fields).issubset(columns) or order not in columns:
        return None
    return [dict(zip(fields, r)) for r in con.execute(
        f"SELECT {','.join(fields)} FROM {table} WHERE {where} ORDER BY {order} DESC LIMIT ?", (limit,))]


def runtime_visibility(con, health, brain, now):
    technical = health_file("technical-kline-health.json", now)
    observer = health_file("market-behavior-health.json", now)
    learning = health_file("trade-learning-health.json", now)
    contract = observer.get("collection_contract") or {}
    radar_stamp = technical.get("broad_radar_snapshot_timestamp_ms")
    radar_age = now.timestamp() * 1000 - radar_stamp if isinstance(radar_stamp, (float, int)) else None
    return {
        "Geniş radar": {
            "Sembol sayısı": technical.get("broad_radar_instrument_count"),
            "Radar yaşı (ms)": radar_age,
            "Üreticinin freshness beyanı": technical.get("broad_radar_fresh"),
            "Sağlık kaydı": technical.get("snapshot_status"),
            "Sağlık yaşı (sn)": technical.get("snapshot_age_seconds"),
            "Kaynak": technical.get("broad_radar_owner"),
        },
        "Derin analiz": {
            "Sağlık kaydı": observer.get("snapshot_status"),
            "Tamamlanma": observer.get("completed_at"),
            "Deep sayısı": observer.get("deep_stream_instrument_count"),
            "Outside-deep sayısı": contract.get("outside_deep_analysis_count"),
            "Mevcut rotasyon": (contract.get("routing_contract") or {}).get("rotation_instruments"),
            "Shortlist trade universe mü": contract.get("deep_shortlist_is_trade_universe"),
        },
        "Orchestrator kapasitesi": brain.get("deep_analysis"),
        "Tarama süresi ve hataları": health.get("scan_runtime"),
        "Quote alım gecikmesi": health.get("quote_ingest"),
        "Öğrenme / challenger": {key: learning.get(key) for key in (
            "snapshot_status", "snapshot_age_seconds", "at", "generated_at", "status", "learning_status",
            "closed_trade_count", "challenger_count", "reviewable_challenger_count",
            "challenger_statuses", "promotion_allowed", "policy_mutation_allowed")},
        "Yetki ve çalışan kaynak": {
            "Panel yetkisi": "READ_ONLY + DIAGNOSTIC_FEEDBACK",
            "Beyan edilen çalışma modu": health.get("execution_mode"),
            "Beyan edilen gerçek emir izni": health.get("real_order_allowed"),
            "Çalışan kaynak": health.get("process_runtime_source"),
            "Tek yürütücü sözleşmesi": "runtime.paper_realtime_worker (repo contract; not process census)",
            "Gerçek emir eventleri": (con.execute("SELECT COUNT(*) FROM paper_position_events WHERE real_order_allowed != 0").fetchone()[0]
                if "real_order_allowed" in {r[1] for r in con.execute("PRAGMA table_info(paper_position_events)")} else None),
        },
        "Bekleyen pozisyonlar": rows(con, "paper_agent_positions",
            ["position_id", "symbol", "direction", "entry_price", "selection_reason"], "position_id", "status='PENDING_ENTRY'"),
        "Son 50 yönetim aksiyonu": rows(con, "paper_position_events",
            ["event_id", "position_id", "observed_at", "event_type", "reason", "stop_before", "stop_after"], "event_id"),
        "Adayların tam eleme gerekçeleri": brain.get("rejection_reasons"),
    }
