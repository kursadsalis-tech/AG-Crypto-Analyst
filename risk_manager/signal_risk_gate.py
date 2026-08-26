"""Fail-closed production gate for actionable Forward Shadow alerts.

The frozen research model is not changed here. This module only decides whether
its output has enough forward evidence and a fresh enough market/asset risk
snapshot to be surfaced as an actionable product alert.
"""
from __future__ import annotations

import json
import math
import sqlite3
from database.runtime_store import connect as runtime_connect
from datetime import datetime, timezone


DB_NAME = "crypto_data.db"
GATE_VERSION = "SIGNAL_RISK_GATE_V1"
MODEL_VERSION = "REGIME_AWARE_FORWARD_V2"
MAX_POLICY_AGE_MINUTES = 30.0
MIN_FORWARD_SETTLED = 30
MIN_FORWARD_HIT_RATE = 60.0
MIN_WILSON_LOWER = 50.0
MIN_DIRECTIONAL_RETURN = 0.0


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def table_exists(con, table_name):
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def wilson_lower_pct(hits, total, z=1.96):
    if total <= 0:
        return 0.0
    proportion = hits / total
    denominator = 1.0 + z * z / total
    centre = proportion + z * z / (2.0 * total)
    margin = z * math.sqrt(
        proportion * (1.0 - proportion) / total
        + z * z / (4.0 * total * total)
    )
    return max(0.0, (centre - margin) / denominator * 100.0)


def forward_evidence(con):
    if not table_exists(con, "forward_shadow_predictions"):
        return {
            "state": "SHADOW_ONLY",
            "settled": 0,
            "open": 0,
            "hits": 0,
            "hit_rate": 0.0,
            "wilson_lower_pct": 0.0,
            "directional_return": 0.0,
            "reasons": ["FORWARD_TABLE_MISSING"],
        }

    settled, hits, avg_return = con.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(hit), 0),
            COALESCE(AVG(directional_return), 0)
        FROM forward_shadow_predictions
        WHERE model_version=?
          AND status='SETTLED'
          AND multifactor_eligible=1
    """, (MODEL_VERSION,)).fetchone()
    open_count = con.execute("""
        SELECT COUNT(*)
        FROM forward_shadow_predictions
        WHERE model_version=?
          AND status='OPEN'
          AND multifactor_eligible=1
    """, (MODEL_VERSION,)).fetchone()[0]

    settled = int(settled or 0)
    hits = int(hits or 0)
    hit_rate = hits / settled * 100.0 if settled else 0.0
    lower = wilson_lower_pct(hits, settled)
    directional_return = float(avg_return or 0.0)
    reasons = []
    if settled < MIN_FORWARD_SETTLED:
        reasons.append(f"FORWARD_SETTLED_BELOW_{MIN_FORWARD_SETTLED}")
    if hit_rate < MIN_FORWARD_HIT_RATE:
        reasons.append(f"FORWARD_HIT_RATE_BELOW_{MIN_FORWARD_HIT_RATE:.0f}")
    if lower < MIN_WILSON_LOWER:
        reasons.append(f"FORWARD_WILSON_LOWER_BELOW_{MIN_WILSON_LOWER:.0f}")
    if directional_return <= MIN_DIRECTIONAL_RETURN:
        reasons.append("FORWARD_DIRECTIONAL_RETURN_NONPOSITIVE")

    return {
        "state": "SHADOW_ONLY" if reasons else "PRODUCTION_ELIGIBLE",
        "settled": settled,
        "open": int(open_count or 0),
        "hits": hits,
        "hit_rate": hit_rate,
        "wilson_lower_pct": lower,
        "directional_return": directional_return,
        "reasons": reasons,
    }


def latest_policy(con):
    if not table_exists(con, "risk_policy_snapshots"):
        return None
    con.row_factory = sqlite3.Row
    row = con.execute("""
        SELECT
            id, created_at, policy_version, market_state, coverage_count,
            coverage_total, max_total_exposure_pct, max_alt_exposure_pct,
            max_single_position_pct, risk_per_trade_pct,
            daily_loss_limit_pct, max_new_positions, new_positions_allowed
        FROM risk_policy_snapshots
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    return dict(row) if row else None


def latest_asset_risk(con, policy_snapshot_id, symbol):
    if not table_exists(con, "asset_risk_snapshots"):
        return None
    con.row_factory = sqlite3.Row
    row = con.execute("""
        SELECT
            symbol, as_of, risk_score, risk_tier, max_position_pct,
            volatility_stop_reference_pct, new_position_allowed, reason_json
        FROM asset_risk_snapshots
        WHERE policy_snapshot_id=? AND symbol=?
        LIMIT 1
    """, (policy_snapshot_id, symbol)).fetchone()
    return dict(row) if row else None


def ensure_gate_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS risk_gate_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            gate_version TEXT NOT NULL,
            candidate_type TEXT NOT NULL,
            candidate_id INTEGER NOT NULL,
            model_version TEXT NOT NULL,
            symbol TEXT NOT NULL,
            policy_snapshot_id INTEGER NOT NULL,
            evidence_state TEXT NOT NULL,
            decision TEXT NOT NULL,
            max_position_pct REAL NOT NULL,
            reason_json TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            UNIQUE(
                gate_version,
                candidate_type,
                candidate_id,
                policy_snapshot_id
            )
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS ix_risk_gate_candidate
        ON risk_gate_decisions(candidate_type, candidate_id, created_at)
    """)
    con.commit()


def save_decision(con, result):
    ensure_gate_table(con)
    before = con.total_changes
    con.execute("""
        INSERT OR IGNORE INTO risk_gate_decisions (
            created_at, gate_version, candidate_type, candidate_id,
            model_version, symbol, policy_snapshot_id, evidence_state,
            decision, max_position_pct, reason_json, detail_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        result["created_at"],
        result["gate_version"],
        result["candidate_type"],
        result["candidate_id"],
        result["model_version"],
        result["symbol"],
        result["policy_snapshot_id"],
        result["evidence"]["state"],
        result["decision"],
        result["max_position_pct"],
        json.dumps(result["reasons"], ensure_ascii=False),
        json.dumps(result, ensure_ascii=False),
    ))
    inserted = con.total_changes > before
    con.commit()
    return inserted


def evaluate_forward_candidate(
    con,
    prediction_id,
    symbol,
    coin=None,
    model_version=MODEL_VERSION,
    now=None,
    persist=True,
):
    now = now or utcnow()
    evidence = forward_evidence(con)
    policy = latest_policy(con)
    reasons = list(evidence["reasons"])
    policy_age_minutes = None
    asset = None

    if model_version != MODEL_VERSION:
        reasons.append("MODEL_VERSION_NOT_APPROVED")

    if policy is None:
        policy_snapshot_id = 0
        reasons.append("RISK_POLICY_MISSING")
    else:
        policy_snapshot_id = int(policy["id"])
        policy_time = parse_time(policy["created_at"])
        if policy_time is None:
            reasons.append("RISK_POLICY_TIME_INVALID")
        else:
            policy_age_minutes = (now - policy_time).total_seconds() / 60.0
            if policy_age_minutes < -1.0:
                reasons.append("RISK_POLICY_TIME_FUTURE")
            if policy_age_minutes > MAX_POLICY_AGE_MINUTES:
                reasons.append("RISK_POLICY_STALE")

        if not bool(policy["new_positions_allowed"]):
            reasons.append("GLOBAL_NEW_POSITIONS_BLOCKED")
        if policy["market_state"] in {"LOCKDOWN", "DATA_BLOCKED"}:
            reasons.append(f"GLOBAL_STATE_{policy['market_state']}")

        asset = latest_asset_risk(con, policy_snapshot_id, symbol)
        if asset is None:
            reasons.append("ASSET_RISK_MISSING")
        else:
            if not bool(asset["new_position_allowed"]):
                reasons.append("ASSET_NEW_POSITION_BLOCKED")
            if float(asset["max_position_pct"]) <= 0:
                reasons.append("ASSET_POSITION_CAP_ZERO")

    reasons = list(dict.fromkeys(reasons))
    decision = "ALLOW" if not reasons else "BLOCK"
    asset_cap = 0.0
    if asset and policy:
        asset_cap = min(
            float(asset["max_position_pct"]),
            float(policy["max_single_position_pct"]),
        )
    result = {
        "created_at": iso(now),
        "gate_version": GATE_VERSION,
        "candidate_type": "FORWARD_MULTIFACTOR_ALERT",
        "candidate_id": int(prediction_id),
        "model_version": model_version,
        "coin": coin,
        "symbol": symbol,
        "policy_snapshot_id": policy_snapshot_id,
        "policy_age_minutes": policy_age_minutes,
        "market_state": policy["market_state"] if policy else "MISSING",
        "asset_risk_tier": asset["risk_tier"] if asset else "MISSING",
        "asset_cap_pct": asset_cap,
        "evidence": evidence,
        "decision": decision,
        "max_position_pct": asset_cap if decision == "ALLOW" else 0.0,
        "reasons": reasons,
        "policy_note": (
            "Gate approval permits an alert, not a trade. Position and portfolio "
            "limits remain mandatory."
        ),
    }
    result["recorded"] = save_decision(con, result) if persist else False
    return result


def pending_candidates(con):
    if not table_exists(con, "forward_shadow_predictions"):
        return []
    return con.execute("""
        SELECT id, coin, symbol, model_version
        FROM forward_shadow_predictions
        WHERE model_version=?
          AND status='OPEN'
          AND multifactor_eligible=1
        ORDER BY id
    """, (MODEL_VERSION,)).fetchall()


def main():
    now = utcnow()
    con = runtime_connect(DB_NAME)
    try:
        ensure_gate_table(con)
        policy = latest_policy(con)
        evidence = forward_evidence(con)
        candidates = pending_candidates(con)
        decisions = [
            evaluate_forward_candidate(
                con,
                prediction_id=row[0],
                coin=row[1],
                symbol=row[2],
                model_version=row[3],
                now=now,
            )
            for row in candidates
        ]
    finally:
        con.close()

    print("AG Crypto Analyst - Signal Risk Gate v1.0.0")
    if policy:
        policy_time = parse_time(policy["created_at"])
        age = (
            max(0.0, (now - policy_time).total_seconds() / 60.0)
            if policy_time else float("inf")
        )
        print(
            f"Risk policy: snapshot={policy['id']} | "
            f"state={policy['market_state']} | age={age:.1f}m"
        )
    else:
        print("Risk policy: MISSING")
    print(
        f"Forward evidence: {evidence['state']} | "
        f"settled={evidence['settled']} | open={evidence['open']} | "
        f"hit={evidence['hit_rate']:.1f}% | "
        f"wilson_lower={evidence['wilson_lower_pct']:.1f}% | "
        f"dir_return={evidence['directional_return']:+.3f}%"
    )
    print(
        f"Açık aday: {len(decisions)} | "
        f"izinli={sum(item['decision'] == 'ALLOW' for item in decisions)} | "
        f"engelli={sum(item['decision'] == 'BLOCK' for item in decisions)}"
    )
    for item in decisions:
        print(
            f"- #{item['candidate_id']} {item['symbol']} | "
            f"{item['decision']} | reasons={','.join(item['reasons']) or 'NONE'}"
        )
    if not decisions:
        print("Değerlendirilecek açık multifactor alarm adayı yok.")
    print(
        "Not: Forward model parametreleri değişmedi. Bu kapı yalnızca yeterli "
        "gerçek performans kanıtı ve güncel risk limiti varsa ürün alarmına izin verir."
    )
    return {
        "policy": policy,
        "evidence": evidence,
        "candidate_count": len(decisions),
        "allowed_count": sum(item["decision"] == "ALLOW" for item in decisions),
        "blocked_count": sum(item["decision"] == "BLOCK" for item in decisions),
        "decisions": decisions,
    }


if __name__ == "__main__":
    main()
