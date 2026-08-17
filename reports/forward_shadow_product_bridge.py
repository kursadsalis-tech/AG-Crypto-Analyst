"""Product bridge for Forward Shadow Validation.

Responsibilities:
- risk-gate newly created multifactor predictions before any product alert,
- surface only evidence-ready, risk-permitted predictions as one-time alerts,
- expose forward-validation performance for the daily committee report,
- never alter the locked research model or its predictions.

Alert delivery is idempotent through ``forward_shadow_alert_log``. Risk-gate
decisions are independently idempotent. Missing/stale risk data or insufficient
forward evidence fails closed and is never presented as a high-confidence alert.
"""
from __future__ import annotations

import json
import sqlite3
from database.runtime_store import connect as runtime_connect
from datetime import datetime, timezone

from notifications.telegram_notifier import is_configured, send_message
from risk_manager import signal_risk_gate

DB_NAME = "crypto_data.db"
MODEL_VERSION = "REGIME_AWARE_FORWARD_V2"


def utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


def ensure_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS forward_shadow_alert_log (
            prediction_id INTEGER PRIMARY KEY,
            sent_at TEXT NOT NULL,
            channel TEXT NOT NULL,
            FOREIGN KEY(prediction_id) REFERENCES forward_shadow_predictions(id)
        )
    """)
    con.commit()


def pending_multifactor_alerts(con):
    return con.execute("""
        SELECT p.id,p.news_id,p.coin,p.symbol,p.confidence,p.impact,p.entry_price,
               p.multifactor_score,p.btc_regime,p.feature_json,p.entry_time,p.due_time
        FROM forward_shadow_predictions p
        LEFT JOIN forward_shadow_alert_log a ON a.prediction_id=p.id
        WHERE p.model_version=?
          AND p.status='OPEN'
          AND p.multifactor_eligible=1
          AND a.prediction_id IS NULL
        ORDER BY p.id
    """, (MODEL_VERSION,)).fetchall()


def alert_text(row, gate_result=None):
    pred_id, news_id, coin, symbol, confidence, impact, entry_price, score, regime, feature_json, entry_time, due_time = row
    try:
        feature = json.loads(feature_json or "{}")
        title = feature.get("title", "")
    except Exception:
        title = ""
    title_line = f"\nHaber: {title}" if title else ""
    risk_line = ""
    if gate_result:
        risk_line = (
            f"\nRisk policy: #{gate_result['policy_snapshot_id']} | "
            f"max pozisyon: %{gate_result['max_position_pct']:.2f}"
        )
    return (
        "🚨 AGCA DOĞRULANMIŞ 1H SİNYALİ\n"
        f"Coin: {coin} ({symbol})\n"
        "Yön: AŞAĞI / NEGATIVE\n"
        f"Multifactor skor: {score:.3f} (cutoff 0.249)\n"
        f"Haber confidence: {confidence} | impact: {impact}\n"
        f"BTC rejimi: {regime}\n"
        f"Giriş fiyatı: {entry_price:.8g}\n"
        f"Tahmin ID: {pred_id} | Haber #{news_id}\n"
        f"Başlangıç: {entry_time}\n"
        f"1h değerlendirme: {due_time}"
        f"{risk_line}"
        f"{title_line}"
    )


def risk_permitted_alerts(con, rows):
    permitted = []
    blocked = 0
    gate_errors = 0
    for row in rows:
        try:
            result = signal_risk_gate.evaluate_forward_candidate(
                con,
                prediction_id=row[0],
                coin=row[2],
                symbol=row[3],
                model_version=MODEL_VERSION,
            )
        except Exception as exc:
            gate_errors += 1
            blocked += 1
            print(
                f"Risk gate HATA: prediction_id={row[0]} alarm engellendi -> {exc}"
            )
            continue

        if result["decision"] == "ALLOW":
            permitted.append((row, result))
        else:
            blocked += 1
            if result["recorded"]:
                print(
                    f"Risk gate BLOCK: prediction_id={row[0]} {row[2]} | "
                    f"reasons={','.join(result['reasons'])}"
                )
    return permitted, blocked, gate_errors


def deliver_pending_alerts(con):
    ensure_tables(con)
    rows = pending_multifactor_alerts(con)
    if not rows:
        return 0, 0

    permitted, blocked, gate_errors = risk_permitted_alerts(con, rows)
    if blocked:
        print(
            f"Ürün alarm risk kapısı: aday={len(rows)} | izinli={len(permitted)} | "
            f"engellenen={blocked} | kapı_hatası={gate_errors}"
        )
    if not permitted:
        return 0, 0

    if not is_configured():
        print(
            f"Ürün alarmı: {len(permitted)} risk-izinli sinyal var; "
            "Telegram env ayarları bulunamadı."
        )
        return 0, len(permitted)

    sent = 0
    for row, gate_result in permitted:
        if send_message(alert_text(row, gate_result)):
            con.execute(
                "INSERT OR IGNORE INTO forward_shadow_alert_log(prediction_id,sent_at,channel) VALUES (?,?,?)",
                (row[0], utcnow_iso(), "telegram"),
            )
            con.commit()
            sent += 1
            print(f"Telegram alarm gönderildi: prediction_id={row[0]} {row[2]}")
    return sent, len(permitted) - sent


def performance_snapshot(con):
    result = {}
    for label, condition in (
        ("base", "base_eligible=1"),
        ("multifactor", "multifactor_eligible=1"),
    ):
        settled, hits, avg = con.execute(f"""
            SELECT COUNT(*),COALESCE(SUM(hit),0),COALESCE(AVG(directional_return),0)
            FROM forward_shadow_predictions
            WHERE model_version=? AND status='SETTLED' AND {condition}
        """, (MODEL_VERSION,)).fetchone()
        open_n = con.execute(f"""
            SELECT COUNT(*) FROM forward_shadow_predictions
            WHERE model_version=? AND status='OPEN' AND {condition}
        """, (MODEL_VERSION,)).fetchone()[0]
        result[label] = {
            "settled": settled,
            "open": open_n,
            "hit_rate": (hits / settled * 100.0) if settled else 0.0,
            "directional_return": float(avg or 0.0),
        }
    return result


def recent_settled(con, limit=10):
    return con.execute("""
        SELECT coin,entry_time,exit_time,multifactor_score,multifactor_eligible,
               directional_return,hit
        FROM forward_shadow_predictions
        WHERE model_version=? AND status='SETTLED'
        ORDER BY settled_at DESC LIMIT ?
    """, (MODEL_VERSION, limit)).fetchall()


def render_committee_section(con):
    snap = performance_snapshot(con)
    base = snap["base"]
    multi = snap["multifactor"]
    lines = [
        "FORWARD SHADOW VALIDATION",
        f"NEWS BASE     : settled={base['settled']} | open={base['open']} | hit={base['hit_rate']:.1f}% | dir_return={base['directional_return']:+.3f}%",
        f"MULTIFACTOR   : settled={multi['settled']} | open={multi['open']} | hit={multi['hit_rate']:.1f}% | dir_return={multi['directional_return']:+.3f}%",
    ]
    recent = recent_settled(con, 5)
    if recent:
        lines.append("Son sonuçlar:")
        for coin, entry_time, exit_time, mf_score, mf_eligible, directional_return, hit in recent:
            tag = "MF" if mf_eligible else "BASE"
            outcome = "DOĞRU" if hit else "YANLIŞ"
            lines.append(f"- {coin} [{tag}] {outcome} | score={mf_score:.3f} | dir_return={directional_return:+.3f}%")
    else:
        lines.append("Henüz sonuçlanmış gerçek forward tahmin yok.")
    return "\n".join(lines)


def main():
    con = runtime_connect(DB_NAME)
    try:
        sent, pending = deliver_pending_alerts(con)
        print(f"Ürün alarm köprüsü: gönderilen={sent} | bekleyen={pending}")
        print()
        print(render_committee_section(con))
    finally:
        con.close()


if __name__ == "__main__":
    main()
