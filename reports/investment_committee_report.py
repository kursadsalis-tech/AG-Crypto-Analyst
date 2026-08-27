"""Generate the fail-closed AGCA daily investment committee report.

The report consumes persisted product contracts instead of creating new
signals.  Shadow candidates remain research observations; only explicit
product decisions may appear as actions.  Missing or stale risk data blocks
actionable output.
"""
from __future__ import annotations

import json
import sqlite3
from database.runtime_store import connect as runtime_connect
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from risk_manager import signal_risk_gate
from whale_tracker.ethereum_token_whale_collector import TOKEN_CONFIGS


DB_NAME = "crypto_data.db"
REPORT_VERSION = "1.4.0"
REPORT_POLICY = "INVESTMENT_COMMITTEE_REPORT_V2_FORECAST"
DECISION_ENGINE_VERSION = "1.1.0"
DECISION_POLICY_VERSION = "DECISION_POLICY_V1_SHADOW"
MAX_DECISION_AGE_MINUTES = 30.0
REPORT_FILE = Path("reports/daily_investment_committee_report.md")
ISTANBUL = ZoneInfo("Europe/Istanbul")


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def safe_json(value, fallback):
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def table_exists(con, table_name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def latest_decision_run(con):
    if not table_exists(con, "decision_engine_snapshots"):
        return None, []
    con.row_factory = sqlite3.Row
    latest = con.execute("""
        SELECT run_id, created_at, engine_version, policy_version, engine_mode,
               policy_snapshot_id
        FROM decision_engine_snapshots
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    if not latest:
        return None, []
    rows = con.execute("""
        SELECT id, run_id, created_at, engine_version, policy_version,
               engine_mode, symbol, coin, policy_snapshot_id,
               forward_prediction_id, risk_decision, shadow_action,
               product_decision, max_position_pct, evidence_json,
               reason_json
        FROM decision_engine_snapshots
        WHERE run_id=?
        ORDER BY id
    """, (latest["run_id"],)).fetchall()
    decisions = []
    for row in rows:
        item = dict(row)
        item["evidence"] = safe_json(item.pop("evidence_json"), {})
        item["reasons"] = safe_json(item.pop("reason_json"), [])
        decisions.append(item)
    return dict(latest), decisions


def latest_asset_risks(con, policy_snapshot_id):
    if not policy_snapshot_id or not table_exists(con, "asset_risk_snapshots"):
        return []
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT symbol, as_of, price, return_24h_pct, range_24h_pct,
               hourly_volatility_pct, drawdown_7d_pct, quote_volume_24h,
               risk_score, risk_tier, max_position_pct,
               new_position_allowed, reason_json
        FROM asset_risk_snapshots
        WHERE policy_snapshot_id=?
        ORDER BY id
    """, (policy_snapshot_id,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["reasons"] = safe_json(item.pop("reason_json"), [])
        result.append(item)
    return result


def model_performance(con, condition):
    if not table_exists(con, "forward_shadow_predictions"):
        return {
            "settled": 0,
            "open": 0,
            "hits": 0,
            "hit_rate": 0.0,
            "directional_return": 0.0,
        }
    settled, hits, avg_return = con.execute(f"""
        SELECT COUNT(*), COALESCE(SUM(hit), 0),
               COALESCE(AVG(directional_return), 0)
        FROM forward_shadow_predictions
        WHERE model_version=?
          AND status='SETTLED'
          AND {condition}
    """, (signal_risk_gate.MODEL_VERSION,)).fetchone()
    open_count = con.execute(f"""
        SELECT COUNT(*)
        FROM forward_shadow_predictions
        WHERE model_version=?
          AND status='OPEN'
          AND {condition}
    """, (signal_risk_gate.MODEL_VERSION,)).fetchone()[0]
    settled = int(settled or 0)
    hits = int(hits or 0)
    return {
        "settled": settled,
        "open": int(open_count or 0),
        "hits": hits,
        "hit_rate": hits / settled * 100.0 if settled else 0.0,
        "directional_return": float(avg_return or 0.0),
    }


def forward_performance(con):
    return {
        "NEWS_BASE": model_performance(con, "base_eligible=1"),
        "MULTIFACTOR": model_performance(con, "multifactor_eligible=1"),
    }


def decision_shadow_performance(con):
    empty = {
        "settled": 0,
        "open": 0,
        "hits": 0,
        "hit_rate": 0.0,
        "directional_return": 0.0,
    }
    if not table_exists(con, "decision_shadow_predictions"):
        return {label: dict(empty) for label in ("ALL", "AL", "SAT")}
    result = {}
    for label, condition in (
        ("ALL", "1=1"),
        ("AL", "action='AL'"),
        ("SAT", "action='SAT'"),
    ):
        settled, hits, avg_return = con.execute(f"""
            SELECT COUNT(*), COALESCE(SUM(hit), 0),
                   COALESCE(AVG(directional_return), 0)
            FROM decision_shadow_predictions
            WHERE engine_version=?
              AND policy_version=?
              AND status='SETTLED'
              AND {condition}
        """, (DECISION_ENGINE_VERSION, DECISION_POLICY_VERSION)).fetchone()
        open_count = con.execute(f"""
            SELECT COUNT(*)
            FROM decision_shadow_predictions
            WHERE engine_version=?
              AND policy_version=?
              AND status='OPEN'
              AND {condition}
        """, (DECISION_ENGINE_VERSION, DECISION_POLICY_VERSION)).fetchone()[0]
        settled = int(settled or 0)
        hits = int(hits or 0)
        result[label] = {
            "settled": settled,
            "open": int(open_count or 0),
            "hits": hits,
            "hit_rate": hits / settled * 100.0 if settled else 0.0,
            "directional_return": float(avg_return or 0.0),
        }
    return result



def latest_forecast_bundle(con):
    if not table_exists(con, "forecast_engine_predictions"):
        return None, []
    con.row_factory = sqlite3.Row
    latest = con.execute("""
        SELECT as_of, artifact_hash, model_version, MAX(created_at) AS created_at
        FROM forecast_engine_predictions
        GROUP BY as_of, artifact_hash, model_version
        ORDER BY as_of DESC LIMIT 1
    """).fetchone()
    if not latest:
        return None, []
    rows = con.execute("""
        SELECT symbol, horizon_hours, current_price, target_price,
               price_p10, price_p90, p_up, p_down, p_neutral,
               shadow_action, risk_adjusted_action, risk_eligible,
               risk_state, risk_cap_pct, due_time, status
        FROM forecast_engine_predictions
        WHERE as_of=? AND artifact_hash=? AND model_version=?
        ORDER BY symbol, horizon_hours
    """, (latest["as_of"], latest["artifact_hash"], latest["model_version"])).fetchall()
    return dict(latest), [dict(row) for row in rows]


def latest_settled_forecasts(con):
    if not table_exists(con, "forecast_engine_predictions"):
        return {}
    con.row_factory = sqlite3.Row
    columns = {
        row[1] for row in con.execute(
            "PRAGMA table_info(forecast_engine_predictions)"
        ).fetchall()
    }
    diagnosis_select = (
        "p.diagnosis_code" if "diagnosis_code" in columns
        else "NULL AS diagnosis_code"
    )
    rows = con.execute(f"""
        SELECT p.symbol, p.horizon_hours, p.exit_price, p.actual_return_pct,
               p.absolute_error_pct, p.interval_hit, p.direction_hit,
               {diagnosis_select}, p.due_time
        FROM forecast_engine_predictions p
        JOIN (
            SELECT symbol, horizon_hours, MAX(forecast_id) AS forecast_id
            FROM forecast_engine_predictions
            WHERE status='SETTLED'
            GROUP BY symbol, horizon_hours
        ) latest ON latest.forecast_id=p.forecast_id
    """).fetchall()
    return {
        (row["symbol"], int(row["horizon_hours"])): dict(row)
        for row in rows
    }


def unified_forecast_performance(con):
    empty = {
        "settled": 0, "open": 0, "direction_hit_rate": 0.0,
        "mae_pct": 0.0, "interval_hit_rate": 0.0,
        "actions": 0, "action_hit_rate": 0.0,
    }
    if not table_exists(con, "forecast_engine_predictions"):
        return {horizon: dict(empty) for horizon in (1, 4, 24)}
    result = {}
    for horizon in (1, 4, 24):
        settled = con.execute("""
            SELECT COUNT(*), COALESCE(AVG(direction_hit),0),
                   COALESCE(AVG(absolute_error_pct),0),
                   COALESCE(AVG(interval_hit),0)
            FROM forecast_engine_predictions
            WHERE status='SETTLED' AND horizon_hours=?
        """, (horizon,)).fetchone()
        actions = con.execute("""
            SELECT COUNT(*), COALESCE(AVG(action_hit),0)
            FROM forecast_engine_predictions
            WHERE status='SETTLED' AND horizon_hours=?
              AND action_hit IS NOT NULL
        """, (horizon,)).fetchone()
        open_count = con.execute("""
            SELECT COUNT(*) FROM forecast_engine_predictions
            WHERE status='OPEN' AND horizon_hours=?
        """, (horizon,)).fetchone()[0]
        result[horizon] = {
            "settled": int(settled[0] or 0),
            "open": int(open_count or 0),
            "direction_hit_rate": float(settled[1] or 0.0) * 100.0,
            "mae_pct": float(settled[2] or 0.0),
            "interval_hit_rate": float(settled[3] or 0.0) * 100.0,
            "actions": int(actions[0] or 0),
            "action_hit_rate": float(actions[1] or 0.0) * 100.0,
        }
    return result


def forecast_diagnosis_counts(con):
    if not table_exists(con, "forecast_engine_predictions"):
        return {}
    columns = {
        row[1] for row in con.execute(
            "PRAGMA table_info(forecast_engine_predictions)"
        ).fetchall()
    }
    if "diagnosis_code" not in columns:
        return {}
    return {
        str(code): int(count)
        for code, count in con.execute("""
            SELECT diagnosis_code, COUNT(*)
            FROM forecast_engine_predictions
            WHERE status='SETTLED' AND diagnosis_code IS NOT NULL
            GROUP BY diagnosis_code ORDER BY COUNT(*) DESC
        """).fetchall()
    }


def fmt_forecast_price(value):
    value = float(value)
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}"
    if value >= 0.01:
        return f"{value:.6f}"
    return f"{value:.8f}"


def fmt_previous_outcome(outcome):
    if not outcome:
        return "Birikiyor"
    return (
        f"{fmt_forecast_price(outcome['exit_price'])} | "
        f"hata=%{float(outcome['absolute_error_pct']):.2f} | "
        f"{outcome.get('diagnosis_code') or 'DIAGNOSIS_PENDING'}"
    )


def policy_status(policy, now):
    if not policy:
        return {
            "state": "DATA_BLOCKED",
            "age_minutes": None,
            "actionable": False,
            "reasons": ["RISK_POLICY_MISSING"],
        }
    policy_time = signal_risk_gate.parse_time(policy.get("created_at"))
    reasons = []
    age_minutes = None
    if policy_time is None:
        reasons.append("RISK_POLICY_TIME_INVALID")
    else:
        age_minutes = (now - policy_time).total_seconds() / 60.0
        if age_minutes < -1.0:
            reasons.append("RISK_POLICY_TIME_FUTURE")
        if age_minutes > signal_risk_gate.MAX_POLICY_AGE_MINUTES:
            reasons.append("RISK_POLICY_STALE")
    if policy.get("market_state") in {"LOCKDOWN", "DATA_BLOCKED"}:
        reasons.append(f"GLOBAL_STATE_{policy['market_state']}")
    if not bool(policy.get("new_positions_allowed")):
        reasons.append("GLOBAL_NEW_POSITIONS_BLOCKED")
    return {
        "state": policy.get("market_state", "MISSING"),
        "age_minutes": age_minutes,
        "actionable": not reasons,
        "reasons": list(dict.fromkeys(reasons)),
    }


def decision_run_status(latest_run, now):
    if not latest_run:
        return {
            "age_minutes": None,
            "fresh": False,
            "reasons": ["DECISION_SNAPSHOT_MISSING"],
        }
    created_at = signal_risk_gate.parse_time(latest_run.get("created_at"))
    if created_at is None:
        return {
            "age_minutes": None,
            "fresh": False,
            "reasons": ["DECISION_SNAPSHOT_TIME_INVALID"],
        }
    age_minutes = (now - created_at).total_seconds() / 60.0
    reasons = []
    if age_minutes < -1.0:
        reasons.append("DECISION_SNAPSHOT_TIME_FUTURE")
    if age_minutes > MAX_DECISION_AGE_MINUTES:
        reasons.append("DECISION_SNAPSHOT_STALE")
    return {
        "age_minutes": age_minutes,
        "fresh": not reasons,
        "reasons": reasons,
    }


def report_state(latest_run, decisions, risk_state, decision_state):
    reasons = []
    if not latest_run or not decisions:
        reasons.append("DECISION_SNAPSHOT_MISSING")
    reasons.extend(decision_state["reasons"])
    if not risk_state["actionable"]:
        reasons.extend(risk_state["reasons"])
    if reasons:
        return "DATA_BLOCKED", list(dict.fromkeys(reasons))
    if latest_run["engine_mode"] == "SHADOW":
        return "SHADOW_ONLY", ["DECISION_ENGINE_SHADOW_MODE"]
    return "PRODUCTION", []


def evidence_states(decisions):
    sources = {
        "forward": set(),
        "social": set(),
        "onchain": set(),
        "macro": set(),
        "whale": set(),
        "risk": set(),
    }
    for item in decisions:
        evidence = item.get("evidence") or {}
        for source in sources:
            state = (evidence.get(source) or {}).get("state")
            if state:
                sources[source].add(str(state))
    return {
        source: ", ".join(sorted(states)) if states else "MISSING"
        for source, states in sources.items()
    }


def _latest_source_audit(con, source):
    if not table_exists(con, "data_validation_audit"):
        return None
    con.row_factory = sqlite3.Row
    row = con.execute("""
        SELECT status,checked_at,detail_json
        FROM data_validation_audit
        WHERE gate_name='SOURCE_CYCLE' AND source=?
        ORDER BY id DESC LIMIT 1
    """, (source,)).fetchone()
    return dict(row) if row else None


def _audit_health(audit, now, max_age_minutes):
    if not audit:
        return "VERI_EKSIK", None
    checked = signal_risk_gate.parse_time(audit.get("checked_at"))
    if checked is None:
        return "VERI_EKSIK", None
    age = (now - checked).total_seconds() / 60.0
    if age < -1.0 or age > max_age_minutes or audit.get("status") != "PASS":
        return "VERI_EKSIK", age
    return "SAGLIKLI", age


def supporting_evidence_coverage(con, decisions, now):
    """Separate source failure, unsupported coverage, no event and unknown direction."""
    onchain_audit = _latest_source_audit(con, "onchain")
    whale_audit = _latest_source_audit(con, "whale_tracker")
    onchain_health, onchain_age = _audit_health(onchain_audit, now, 30.0)
    whale_health, whale_age = _audit_health(whale_audit, now, 20.0)

    cutoff = iso(now - timedelta(hours=24))
    whale_rows = []
    if table_exists(con, "whale_events"):
        con.row_factory = sqlite3.Row
        whale_rows = [dict(row) for row in con.execute("""
            SELECT asset,direction,exchange_flow,COUNT(*) AS event_count,
                   COALESCE(SUM(usd_estimate),0) AS gross_usd_estimate
            FROM whale_events WHERE block_time>=?
            GROUP BY asset,direction,exchange_flow
        """, (cutoff,)).fetchall()]
    by_asset = {}
    for row in whale_rows:
        by_asset.setdefault(str(row["asset"]), []).append(row)

    whale_supported = {"BTC", "ETH"} | {
        str(config["symbol"]) for config in TOKEN_CONFIGS.values()
    }
    network_by_symbol = {
        "BTCUSDT": "Bitcoin", "ETHUSDT": "Ethereum", "TIAUSDT": "Celestia",
    }
    rows = []
    for decision in decisions:
        symbol = str(decision["symbol"])
        coin = str(decision["coin"])
        onchain = (decision.get("evidence") or {}).get("onchain") or {}
        chain = network_by_symbol.get(symbol)
        if onchain_health != "SAGLIKLI":
            onchain_state = "VERI_EKSIK"
        elif chain is None:
            onchain_state = "KAPSAM_DISI_TOKEN_SEVIYESI"
        else:
            onchain_state = str(onchain.get("state") or "KAPSAMDA_KAYIT_YOK")

        events = by_asset.get(coin, [])
        if whale_health != "SAGLIKLI":
            whale_state = "VERI_EKSIK"
            whale_direction = "BELIRLENEMEDI"
            whale_event_count = 0
        elif coin not in whale_supported:
            whale_state = "KAPSAM_DISI"
            whale_direction = "UYGULANAMAZ"
            whale_event_count = 0
        elif not events:
            whale_state = "KAPSAMDA_OLAY_YOK"
            whale_direction = "YOK"
            whale_event_count = 0
        else:
            whale_event_count = sum(int(row["event_count"]) for row in events)
            verified = {
                row["direction"] for row in events
                if row["exchange_flow"] == "VERIFIED"
                and row["direction"] in {"EXCHANGE_INFLOW", "EXCHANGE_OUTFLOW"}
            }
            if verified:
                whale_state = "DOGRULANMIS_BORSA_AKISI"
                whale_direction = ",".join(sorted(verified))
            else:
                whale_state = "OLAY_VAR_YON_DOGRULANMADI"
                whale_direction = "UNKNOWN"
        rows.append({
            "symbol": symbol,
            "onchain_state": onchain_state,
            "onchain_chain": chain,
            "whale_state": whale_state,
            "whale_direction": whale_direction,
            "whale_event_count": whale_event_count,
        })
    return {
        "onchain_health": onchain_health,
        "onchain_age_minutes": onchain_age,
        "whale_health": whale_health,
        "whale_age_minutes": whale_age,
        "rows": rows,
    }


def riskiest_asset(assets):
    if not assets:
        return None
    return max(
        assets,
        key=lambda row: (
            int(row.get("risk_score") or 0),
            -float(row.get("quote_volume_24h") or 0.0),
        ),
    )


def strongest_product_opportunity(decisions):
    allowed = [
        item for item in decisions
        if item["product_decision"] == "AL"
        and item["risk_decision"] == "ALLOW"
        and float(item["max_position_pct"]) > 0
    ]
    return max(
        allowed,
        key=lambda row: float(row["max_position_pct"]),
        default=None,
    )


def effective_product_decision(item, report_state_value):
    if report_state_value != "PRODUCTION":
        return "BEKLE"
    if item.get("risk_decision") != "ALLOW":
        return "BEKLE"
    return item.get("product_decision") or "BEKLE"


def fmt_age(value):
    return "n/a" if value is None else f"{value:.1f} dakika"


def fmt_shadow(action):
    return f"{action}_ADAYI" if action in {"AL", "SAT"} else "BEKLE"


def build_report(
    now,
    latest_run,
    decisions,
    policy,
    risk_state,
    assets,
    forward,
    shadow,
    forecast_bundle,
    forecasts,
    forecast_scores,
    latest_outcomes,
    diagnosis_counts,
    supporting_coverage,
):
    local_now = now.astimezone(ISTANBUL)
    decision_state = decision_run_status(latest_run, now)
    state, state_reasons = report_state(
        latest_run,
        decisions,
        risk_state,
        decision_state,
    )
    maturity = evidence_states(decisions)
    opportunity = (
        strongest_product_opportunity(decisions)
        if state == "PRODUCTION"
        else None
    )
    riskiest = riskiest_asset(assets)
    product_actions = sorted({
        effective_product_decision(item, state) for item in decisions
    }) if decisions else ["BEKLE"]
    blocked = [
        item["symbol"] for item in decisions
        if item["risk_decision"] == "BLOCK"
    ]

    lines = [
        "# AG Crypto Analyst — Günlük Yatırım Komitesi Brifingi",
        "",
        f"- Rapor sürümü: v{REPORT_VERSION}",
        f"- Politika: {REPORT_POLICY}",
        f"- Rapor zamanı: {local_now.strftime('%Y-%m-%d %H:%M:%S')} Europe/Istanbul",
        f"- Üretim durumu: **{state}**",
        f"- Decision Engine modu: **{latest_run['engine_mode'] if latest_run else 'MISSING'}**",
        "",
        "## Yönetici Özeti",
        "",
        f"- Ürün kararı: **{', '.join(product_actions)}**",
        (
            f"- Bugünün en güçlü fırsatı: **{opportunity['symbol']}** "
            f"(ürün AL, risk tavanı %{opportunity['max_position_pct']:.2f})"
            if opportunity else
            "- Bugünün en güçlü fırsatı: **YOK — üretim-uygun AL kararı bulunmuyor.**"
        ),
        (
            f"- Bugünün en riskli coini: **{riskiest['symbol']}** "
            f"(tier={riskiest['risk_tier']}, risk_score={riskiest['risk_score']}, "
            f"yeni pozisyon={'EVET' if riskiest['new_position_allowed'] else 'HAYIR'})"
            if riskiest else
            "- Bugünün en riskli coini: **BELİRLENEMEDİ — risk verisi yok.**"
        ),
        f"- Global risk: **{risk_state['state']}** | yaş={fmt_age(risk_state['age_minutes'])}",
        f"- Decision snapshot yaşı: **{fmt_age(decision_state['age_minutes'])}**",
        f"- Risk tarafından yeni pozisyona kapalı coin: **{len(blocked)}**",
    ]
    if state_reasons:
        lines.append(f"- Üretim kısıtı: {', '.join(state_reasons)}")


    lines.extend([
        "",
        "## Birleşik Fiyat Tahminleri — Forward Shadow",
        "",
        (
            f"- Forecast zamanı: {forecast_bundle['as_of']} | "
            f"model={forecast_bundle['model_version']} | satır={len(forecasts)}/39"
            if forecast_bundle else
            "- Forecast durumu: **NO_FORECAST — güncel tahmin paketi bulunamadı.**"
        ),
        "",
        "| Coin | Ufuk | Şimdi | q50 hedef | %80 aralık | UP | DOWN | Gölge | Risk sonrası | Önceki sonuç / hata |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|---|",
    ])
    for row in forecasts:
        outcome = latest_outcomes.get(
            (row["symbol"], int(row["horizon_hours"]))
        )
        lines.append(
            f"| {row['symbol']} | {int(row['horizon_hours'])}h | "
            f"{fmt_forecast_price(row['current_price'])} | "
            f"{fmt_forecast_price(row['target_price'])} | "
            f"{fmt_forecast_price(row['price_p10'])}–"
            f"{fmt_forecast_price(row['price_p90'])} | "
            f"%{float(row['p_up']) * 100:.1f} | "
            f"%{float(row['p_down']) * 100:.1f} | "
            f"{row['shadow_action']} | **{row['risk_adjusted_action']}** | "
            f"{fmt_previous_outcome(outcome)} |"
        )
    if not forecasts:
        lines.append("| Veri yok | — | — | — | — | — | — | BEKLE | **BEKLE** | Birikiyor |")

    lines.extend([
        "",
        "Hedef q50 medyandır; aralık q10–q90'dır. UP/DOWN olasılığı 10 bps "
        "maliyet ve 5 bps anlamlı hareket eşiği sonrasını ifade eder. "
        "Risk yalnız aksiyonu veto eder; hedefi veya olasılığı değiştirmez.",
        "",
        "### Forecast Performansı",
        "",
        "| Ufuk | Sonuçlanan | Açık | Sınıf başarısı | MAE | Aralık başarısı | Aksiyon | Aksiyon başarısı |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for horizon in (1, 4, 24):
        score = forecast_scores[horizon]
        lines.append(
            f"| {horizon}h | {score['settled']} | {score['open']} | "
            f"%{score['direction_hit_rate']:.1f} | "
            f"%{score['mae_pct']:.3f} | "
            f"%{score['interval_hit_rate']:.1f} | "
            f"{score['actions']} | %{score['action_hit_rate']:.1f} |"
        )
    lines.extend(["", "Sınıf başarısı, **UP/DOWN/NEUTRAL** üç sınıfının toplam isabetidir; yalnız yönlü işlem başarısı değildir."])
    if diagnosis_counts:
        lines.extend(["", "### Biriken Hata Nedenleri", ""])
        for code, count in diagnosis_counts.items():
            lines.append(f"- {code}: {count}")
    else:
        lines.extend(["", "Hata teşhisi: İlk tahminlerin sonuçlanması bekleniyor."])


    lines.extend([
        "",
        "## Coin Karar Tablosu",
        "",
        "| Coin | 24h | Teknik | Gölge araştırma | Ürün kararı | Risk | Tavan |",
        "|---|---:|---|---|---|---|---:|",
    ])
    for item in decisions:
        evidence = item.get("evidence") or {}
        market = evidence.get("market") or {}
        technical = evidence.get("technical") or {}
        return_24h = market.get("return_24h_pct")
        return_text = "n/a" if return_24h is None else f"{float(return_24h):+.2f}%"
        product_decision = effective_product_decision(item, state)
        lines.append(
            f"| {item['symbol']} | {return_text} | "
            f"{technical.get('direction', 'MISSING')} | "
            f"{fmt_shadow(item['shadow_action'])} | "
            f"**{product_decision}** | {item['risk_decision']} | "
            f"%{float(item['max_position_pct']):.2f} |"
        )
    if not decisions:
        lines.append("| Veri yok | n/a | MISSING | BEKLE | **BEKLE** | BLOCK | %0.00 |")

    lines.extend([
        "",
        "Gölge araştırma sütunu alarm, işlem talimatı veya ürün kararı değildir.",
        "",
        "## Risk Çerçevesi",
        "",
    ])
    if policy:
        lines.extend([
            f"- Snapshot: {policy['id']} | state={policy['market_state']} | "
            f"kapsam={policy['coverage_count']}/{policy['coverage_total']}",
            f"- Toplam maruziyet tavanı: %{policy['max_total_exposure_pct']:.1f}",
            f"- Altcoin maruziyet tavanı: %{policy['max_alt_exposure_pct']:.1f}",
            f"- Tek pozisyon tavanı: %{policy['max_single_position_pct']:.1f}",
            f"- İşlem başı risk tavanı: %{policy['risk_per_trade_pct']:.2f}",
            f"- Günlük kayıp tavanı: %{policy['daily_loss_limit_pct']:.2f}",
            f"- Yeni pozisyon adedi tavanı: {policy['max_new_positions']}",
        ])
    else:
        lines.append("- Risk politikası eksik; yeni pozisyon değerlendirmesi kapalı.")

    lines.extend([
        "",
        "## Forward Performans — Üretim Kanıtı Değildir",
        "",
        "| Model | Sonuçlanan | Açık | Hit | Ortalama yönlü getiri |",
        "|---|---:|---:|---:|---:|",
    ])
    for label in ("NEWS_BASE", "MULTIFACTOR"):
        row = forward[label]
        lines.append(
            f"| {label} | {row['settled']} | {row['open']} | "
            f"%{row['hit_rate']:.1f} | {row['directional_return']:+.3f}% |"
        )
    lines.extend([
        "",
        "Forward multifactor; settled>=30, hit>=%60, Wilson alt>=%50, "
        "pozitif directional return ve güncel Risk izni birlikte oluşmadan "
        "ürün alarmı veya karar girdisi olamaz.",
        "",
        "## Decision Engine Gölge Doğrulama",
        "",
        "| Yön | Sonuçlanan | Açık | Hit | Ortalama yönlü getiri |",
        "|---|---:|---:|---:|---:|",
    ])
    for label in ("ALL", "AL", "SAT"):
        row = shadow[label]
        lines.append(
            f"| {label} | {row['settled']} | {row['open']} | "
            f"%{row['hit_rate']:.1f} | {row['directional_return']:+.3f}% |"
        )
    lines.extend([
        "",
        "Aynı coin için bir açık 1h tahmin sonuçlanmadan yenisi açılmaz. "
        "Küçük örneklem başarı iddiası değildir ve politika otomatik açılmaz.",
        "",
        "## Kanıt Olgunluğu",
        "",
        "| Kaynak | Durum | Ürün kararındaki rol |",
        "|---|---|---|",
        f"| Forward | {maturity['forward']} | Yalnız PRODUCTION_ELIGIBLE ise aday |",
        f"| Social | {maturity['social']} | Şimdilik gözlem |",
        f"| On-chain | {maturity['onchain']} | Şimdilik gözlem |",
        f"| Macro | {maturity['macro']} | PROVISIONAL bağlam |",
        f"| Whale | {maturity['whale']} | Doğrulanmış yön yoksa gözlem |",
        f"| Risk | {maturity['risk']} | Nihai veto ve tavan |",
        "",
        "### On-chain ve Balina Kapsam Tanısı",
        "",
        f"- On-chain collector: **{supporting_coverage['onchain_health']}** | "
        f"yaş={fmt_age(supporting_coverage['onchain_age_minutes'])}",
        f"- Whale collector: **{supporting_coverage['whale_health']}** | "
        f"yaş={fmt_age(supporting_coverage['whale_age_minutes'])}",
        "- `VERI_EKSIK` yalnız kaynak başarısız, bayat veya doğrulanamazsa kullanılır. "
        "Kapsam dışı coin, kapsamda olay yok ve yönü doğrulanmamış olay ayrı durumlardır.",
        "",
        "| Coin | On-chain kapsamı | Whale kapsamı | 24s olay | Doğrulanmış yön |",
        "|---|---|---|---:|---|",
    ])
    for row in supporting_coverage["rows"]:
        chain = f" ({row['onchain_chain']})" if row["onchain_chain"] else ""
        lines.append(
            f"| {row['symbol']} | {row['onchain_state']}{chain} | "
            f"{row['whale_state']} | {row['whale_event_count']} | "
            f"{row['whale_direction']} |"
        )
    lines.extend([
        "",
        "## Komite Sonucu",
        "",
    ])
    if state == "PRODUCTION":
        lines.append(
            "Yalnız yukarıdaki ürün kararları ve Risk Manager tavanları "
            "çerçevesinde değerlendirme yapılabilir."
        )
    else:
        lines.append(
            "**Yeni yönlü pozisyon önerisi yok.** Gölge ve olgunlaşmamış "
            "kanıtlar izlenir; kullanıcıya güçlü fırsat olarak sunulmaz."
        )
    lines.extend([
        "",
        "_Bu rapor otomatik karar destek çıktısıdır; kesin getiri vaadi değildir._",
    ])

    summary = {
        "report_state": state,
        "state_reasons": state_reasons,
        "product_actions": product_actions,
        "strongest_opportunity": opportunity["symbol"] if opportunity else None,
        "riskiest_asset": riskiest["symbol"] if riskiest else None,
        "risk_blocked_count": len(blocked),
        "decision_count": len(decisions),
        "decision_run_id": latest_run["run_id"] if latest_run else None,
        "decision_age_minutes": decision_state["age_minutes"],
        "forecast_count": len(forecasts),
        "forecast_as_of": forecast_bundle["as_of"] if forecast_bundle else None,
        "forecast_settled": sum(
            row["settled"] for row in forecast_scores.values()
        ),
        "forecast_diagnoses": diagnosis_counts,
        "supporting_evidence_coverage": supporting_coverage,
    }
    return "\n".join(lines) + "\n", summary


def ensure_report_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS committee_report_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            report_version TEXT NOT NULL,
            report_policy TEXT NOT NULL,
            report_state TEXT NOT NULL,
            decision_run_id TEXT,
            risk_policy_snapshot_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            UNIQUE(report_date, report_version)
        )
    """)
    con.commit()


def save_report(con, now, content, summary, policy):
    ensure_report_table(con)
    report_date = now.astimezone(ISTANBUL).date().isoformat()
    con.execute("""
        INSERT INTO committee_report_snapshots (
            report_date, created_at, report_version, report_policy,
            report_state, decision_run_id, risk_policy_snapshot_id,
            content, detail_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_date, report_version) DO UPDATE SET
            created_at=excluded.created_at,
            report_policy=excluded.report_policy,
            report_state=excluded.report_state,
            decision_run_id=excluded.decision_run_id,
            risk_policy_snapshot_id=excluded.risk_policy_snapshot_id,
            content=excluded.content,
            detail_json=excluded.detail_json
    """, (
        report_date,
        iso(now),
        REPORT_VERSION,
        REPORT_POLICY,
        summary["report_state"],
        summary["decision_run_id"],
        int(policy["id"]) if policy else 0,
        content,
        json.dumps(summary, ensure_ascii=False),
    ))
    con.commit()


def generate(now=None, persist=True, write_file=True):
    now = now or utcnow()
    con = runtime_connect(DB_NAME)
    try:
        latest_run, decisions = latest_decision_run(con)
        policy = signal_risk_gate.latest_policy(con)
        risk_state = policy_status(policy, now)
        assets = latest_asset_risks(
            con,
            int(policy["id"]) if policy else 0,
        )
        forward = forward_performance(con)
        shadow = decision_shadow_performance(con)
        forecast_bundle, forecasts = latest_forecast_bundle(con)
        latest_outcomes = latest_settled_forecasts(con)
        forecast_scores = unified_forecast_performance(con)
        diagnosis_counts = forecast_diagnosis_counts(con)
        supporting_coverage = supporting_evidence_coverage(con, decisions, now)
        content, summary = build_report(
            now,
            latest_run,
            decisions,
            policy,
            risk_state,
            assets,
            forward,
            shadow,
            forecast_bundle,
            forecasts,
            forecast_scores,
            latest_outcomes,
            diagnosis_counts,
            supporting_coverage,
        )
        if persist:
            save_report(con, now, content, summary, policy)
    finally:
        con.close()

    if write_file:
        REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        REPORT_FILE.write_text(content, encoding="utf-8")
    return {
        "content": content,
        "summary": summary,
        "report_file": str(REPORT_FILE),
    }


def main():
    result = generate()
    summary = result["summary"]
    print(f"AG Crypto Analyst - Report Generator v{REPORT_VERSION}")
    print(f"Politika: {REPORT_POLICY} | durum: {summary['report_state']}")
    print(
        f"Decision run: {summary['decision_run_id'] or 'MISSING'} | "
        f"coin={summary['decision_count']} | "
        f"risk_blocked={summary['risk_blocked_count']} | "
        f"forecast={summary['forecast_count']}/39 | "
        f"forecast_settled={summary['forecast_settled']}"
    )
    print(
        "Ürün aksiyonu: "
        + ", ".join(summary["product_actions"])
        + " | en güçlü fırsat: "
        + (summary["strongest_opportunity"] or "YOK")
        + " | en riskli: "
        + (summary["riskiest_asset"] or "BELİRLENEMEDİ")
    )
    print(f"Rapor oluşturuldu: {result['report_file']}")
    print(
        "Not: Gölge adayları ürün kararı olarak sunulmadı; "
        "Risk BLOCK bulunan coinler öneriye dönüştürülmedi."
    )
    return result


if __name__ == "__main__":
    main()
