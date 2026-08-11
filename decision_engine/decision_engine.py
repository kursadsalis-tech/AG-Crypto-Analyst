"""Fail-closed, explainable Decision Engine shadow policy.

The first Decision Engine release deliberately separates a research action from
the product decision.  Technical/market agreement may create a shadow candidate
that can be measured later, but it cannot become an actionable product decision
until the Decision Engine policy itself earns production eligibility.

Evidence marked PROVISIONAL, WARMING_UP, NO_DATA, UNVERIFIED, MONITOR_ONLY or
SHADOW_ONLY is kept visible and never upgraded by score aggregation.  A Risk
Manager block is final.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from analysis import forward_shadow_validator
from onchain import onchain_analyzer
from risk_manager import risk_manager, signal_risk_gate


DB_NAME = "crypto_data.db"
ENGINE_VERSION = "1.1.0"
POLICY_VERSION = "DECISION_POLICY_V1_SHADOW"
ENGINE_MODE = "SHADOW"
MAX_RISK_AGE_MINUTES = signal_risk_gate.MAX_POLICY_AGE_MINUTES

SYMBOLS = tuple(risk_manager.SYMBOLS)
COIN_BY_SYMBOL = {
    symbol: symbol[:-4] if symbol.endswith("USDT") else symbol
    for symbol in SYMBOLS
}
NETWORK_CONTEXT = {
    "BTCUSDT": "Bitcoin",
    "ETHUSDT": "Ethereum",
}


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def table_exists(con, table_name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def latest_asset_context(con, policy_snapshot_id, symbol):
    if not table_exists(con, "asset_risk_snapshots"):
        return None
    con.row_factory = sqlite3.Row
    row = con.execute("""
        SELECT
            symbol, as_of, price, return_24h_pct, range_24h_pct,
            hourly_volatility_pct, drawdown_7d_pct, quote_volume_24h,
            risk_score, risk_tier, max_position_pct,
            volatility_stop_reference_pct, new_position_allowed, reason_json
        FROM asset_risk_snapshots
        WHERE policy_snapshot_id=? AND symbol=?
        LIMIT 1
    """, (policy_snapshot_id, symbol)).fetchone()
    return dict(row) if row else None


def latest_social_context(con, coin):
    if not table_exists(con, "social_sentiment_snapshots"):
        return None
    con.row_factory = sqlite3.Row
    row = con.execute("""
        SELECT created_at, mentions, positive, negative, neutral,
               sentiment_score, signal, confidence
        FROM social_sentiment_snapshots
        WHERE coin=?
        ORDER BY id DESC
        LIMIT 1
    """, (coin,)).fetchone()
    return dict(row) if row else None


def latest_macro_context(con):
    if not table_exists(con, "macro_regime_snapshots"):
        return None
    con.row_factory = sqlite3.Row
    row = con.execute("""
        SELECT created_at, regime, score, component_coverage, provisional
        FROM macro_regime_snapshots
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    return dict(row) if row else None


def latest_whale_context(con):
    if not table_exists(con, "whale_evidence_snapshots"):
        return None
    con.row_factory = sqlite3.Row
    row = con.execute("""
        SELECT created_at, chain_count, event_count, unique_tx_count,
               gross_usd_estimate, max_usd_estimate,
               alert_eligible_count, status
        FROM whale_evidence_snapshots
        ORDER BY id DESC
        LIMIT 1
    """).fetchone()
    return dict(row) if row else None


def latest_open_forward_candidate(con, symbol):
    if not table_exists(con, "forward_shadow_predictions"):
        return None
    con.row_factory = sqlite3.Row
    row = con.execute("""
        SELECT id, coin, symbol, model_version, direction, confidence, impact,
               multifactor_score, entry_time, due_time
        FROM forward_shadow_predictions
        WHERE model_version=?
          AND symbol=?
          AND status='OPEN'
          AND multifactor_eligible=1
        ORDER BY id DESC
        LIMIT 1
    """, (signal_risk_gate.MODEL_VERSION, symbol)).fetchone()
    return dict(row) if row else None


def load_onchain_context(con):
    if not table_exists(con, "onchain_observations"):
        return {}
    try:
        rows = onchain_analyzer.analyze(con)
    except Exception:
        return {}
    grouped = {}
    for row in rows:
        grouped.setdefault(row["chain"], []).append(row)
    return grouped


def technical_context(symbol):
    features = forward_shadow_validator.technical_features(symbol)
    components = {
        "rsi_extreme": 1 if features["rsi"] <= 30 else (
            -1 if features["rsi"] >= 70 else 0
        ),
        "macd_histogram": 1 if features["histogram"] > 0 else (
            -1 if features["histogram"] < 0 else 0
        ),
        "ema_spread": 1 if features["ema_spread_pct"] > 0 else (
            -1 if features["ema_spread_pct"] < 0 else 0
        ),
        "momentum_6h": 1 if features["ret6"] > 0 else (
            -1 if features["ret6"] < 0 else 0
        ),
    }
    score = sum(components.values())
    if score >= 2:
        direction = "BULLISH"
    elif score <= -2:
        direction = "BEARISH"
    else:
        direction = "MIXED"
    return {
        **features,
        "components": components,
        "score": score,
        "direction": direction,
        "candle_policy": "CLOSED_1H_ONLY",
        "maturity": "RESEARCH_ONLY",
        "production_usable": False,
    }


def risk_guard(policy, asset, now):
    reasons = []
    age_minutes = None
    if policy is None:
        reasons.append("RISK_POLICY_MISSING")
    else:
        policy_time = signal_risk_gate.parse_time(policy.get("created_at"))
        if policy_time is None:
            reasons.append("RISK_POLICY_TIME_INVALID")
        else:
            age_minutes = max(
                0.0,
                (now - policy_time).total_seconds() / 60.0,
            )
            if age_minutes > MAX_RISK_AGE_MINUTES:
                reasons.append("RISK_POLICY_STALE")
        if policy.get("market_state") in {"LOCKDOWN", "DATA_BLOCKED"}:
            reasons.append(f"GLOBAL_STATE_{policy['market_state']}")
        if not bool(policy.get("new_positions_allowed")):
            reasons.append("GLOBAL_NEW_POSITIONS_BLOCKED")

    if asset is None:
        reasons.append("ASSET_RISK_MISSING")
    else:
        if not bool(asset.get("new_position_allowed")):
            reasons.append("ASSET_NEW_POSITION_BLOCKED")
        if float(asset.get("max_position_pct") or 0.0) <= 0:
            reasons.append("ASSET_POSITION_CAP_ZERO")

    reasons = list(dict.fromkeys(reasons))
    allowed = not reasons
    cap = 0.0
    if allowed:
        cap = min(
            float(asset["max_position_pct"]),
            float(policy["max_single_position_pct"]),
        )
    return {
        "decision": "ALLOW" if allowed else "BLOCK",
        "age_minutes": age_minutes,
        "max_position_pct": cap,
        "reasons": reasons,
    }


def onchain_evidence_for_symbol(symbol, grouped):
    chain = NETWORK_CONTEXT.get(symbol)
    if not chain:
        return {
            "state": "NOT_APPLICABLE",
            "production_usable": False,
            "reason": "NO_DIRECT_NETWORK_LEVEL_MAPPING",
        }
    rows = grouped.get(chain, [])
    if not rows:
        return {
            "state": "MISSING",
            "production_usable": False,
            "chain": chain,
        }
    statuses = sorted({row["status"] for row in rows})
    if "NO_DATA" in statuses:
        state = "NO_DATA"
    elif "WARMING_UP" in statuses:
        state = "WARMING_UP"
    else:
        state = "OBSERVATION_ONLY"
    return {
        "state": state,
        "production_usable": False,
        "chain": chain,
        "statuses": statuses,
        "ready_metrics": sum(bool(row.get("ready")) for row in rows),
        "metric_count": len(rows),
    }


def evidence_bundle(
    asset,
    technical,
    technical_error,
    forward_evidence,
    forward_candidate,
    forward_gate,
    social,
    onchain,
    macro,
    whale,
    risk,
):
    return {
        "market": {
            "state": "CONTEXT_ONLY" if asset else "MISSING",
            "production_usable": False,
            "as_of": asset.get("as_of") if asset else None,
            "price": asset.get("price") if asset else None,
            "return_24h_pct": asset.get("return_24h_pct") if asset else None,
            "range_24h_pct": asset.get("range_24h_pct") if asset else None,
        },
        "technical": technical if technical else {
            "state": "MISSING",
            "production_usable": False,
            "error": technical_error,
        },
        "forward": {
            "state": forward_evidence["state"],
            "production_usable": bool(
                forward_candidate
                and forward_gate
                and forward_gate["decision"] == "ALLOW"
            ),
            "candidate": forward_candidate,
            "gate": forward_gate,
            "performance": forward_evidence,
        },
        "social": {
            "state": "OBSERVATION_ONLY" if social else "MISSING",
            "production_usable": False,
            "snapshot": social,
        },
        "onchain": onchain,
        "macro": {
            "state": "PROVISIONAL" if macro else "MISSING",
            "production_usable": False,
            "snapshot": macro,
        },
        "whale": {
            "state": whale["status"] if whale else "MISSING",
            "production_usable": False,
            "snapshot": whale,
        },
        "risk": {
            "state": "ENFORCED" if risk["decision"] == "ALLOW" else "BLOCK",
            "production_usable": True,
            **risk,
        },
    }


def choose_shadow_action(asset, technical, forward_candidate, forward_gate, risk):
    reasons = []
    if risk["decision"] != "ALLOW":
        return "BEKLE", ["RISK_VETO", *risk["reasons"]]

    if (
        forward_candidate
        and forward_gate
        and forward_gate["decision"] == "ALLOW"
    ):
        direction = str(forward_candidate.get("direction") or "").upper()
        if direction == "NEGATIVE":
            return "SAT", ["PRODUCTION_ELIGIBLE_FORWARD_NEGATIVE"]
        if direction == "POSITIVE":
            return "AL", ["PRODUCTION_ELIGIBLE_FORWARD_POSITIVE"]
        reasons.append("FORWARD_DIRECTION_UNSUPPORTED")

    if not asset or not technical:
        reasons.append("DIRECTIONAL_CONTEXT_MISSING")
        return "BEKLE", reasons

    market_return = float(asset.get("return_24h_pct") or 0.0)
    direction = technical.get("direction")
    if direction == "BULLISH" and market_return > 0:
        return "AL", ["RESEARCH_TECHNICAL_MARKET_BULLISH_AGREEMENT"]
    if direction == "BEARISH" and market_return < 0:
        return "SAT", ["RESEARCH_TECHNICAL_MARKET_BEARISH_AGREEMENT"]
    return "BEKLE", ["RESEARCH_DIRECTIONAL_AGREEMENT_ABSENT"]


def build_decision(
    symbol,
    created_at,
    policy,
    asset,
    technical,
    technical_error,
    forward_evidence,
    forward_candidate,
    forward_gate,
    social,
    onchain,
    macro,
    whale,
    now,
):
    risk = risk_guard(policy, asset, now)
    shadow_action, action_reasons = choose_shadow_action(
        asset,
        technical,
        forward_candidate,
        forward_gate,
        risk,
    )
    evidence = evidence_bundle(
        asset,
        technical,
        technical_error,
        forward_evidence,
        forward_candidate,
        forward_gate,
        social,
        onchain,
        macro,
        whale,
        risk,
    )
    reasons = list(dict.fromkeys([
        "ENGINE_POLICY_SHADOW_ONLY",
        "DIRECTIONAL_POLICY_NOT_VALIDATED",
        *action_reasons,
    ]))
    return {
        "created_at": created_at,
        "engine_version": ENGINE_VERSION,
        "policy_version": POLICY_VERSION,
        "engine_mode": ENGINE_MODE,
        "symbol": symbol,
        "coin": COIN_BY_SYMBOL[symbol],
        "policy_snapshot_id": int(policy["id"]) if policy else 0,
        "forward_prediction_id": (
            int(forward_candidate["id"]) if forward_candidate else None
        ),
        "risk_decision": risk["decision"],
        "risk_age_minutes": risk["age_minutes"],
        "shadow_action": shadow_action,
        "product_decision": "BEKLE",
        "max_position_pct": risk["max_position_pct"],
        "evidence": evidence,
        "reasons": reasons,
        "policy_note": (
            "Gölge adayı ürün kararı değildir. Decision Engine politikası "
            "forward doğrulama kazanana kadar ürün kararı BEKLE'dir; Risk "
            "Manager BLOCK nihai vetodur."
        ),
    }


def ensure_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS decision_engine_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            engine_mode TEXT NOT NULL,
            symbol TEXT NOT NULL,
            coin TEXT NOT NULL,
            policy_snapshot_id INTEGER NOT NULL,
            forward_prediction_id INTEGER,
            risk_decision TEXT NOT NULL,
            shadow_action TEXT NOT NULL,
            product_decision TEXT NOT NULL,
            max_position_pct REAL NOT NULL,
            evidence_json TEXT NOT NULL,
            reason_json TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            UNIQUE(run_id, symbol)
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS ix_decision_engine_symbol_time
        ON decision_engine_snapshots(symbol, created_at)
    """)
    con.commit()


def save_decisions(con, run_id, decisions):
    con.executemany("""
        INSERT OR IGNORE INTO decision_engine_snapshots (
            run_id, created_at, engine_version, policy_version, engine_mode,
            symbol, coin, policy_snapshot_id, forward_prediction_id,
            risk_decision, shadow_action, product_decision,
            max_position_pct, evidence_json, reason_json, detail_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [(
        run_id,
        item["created_at"],
        item["engine_version"],
        item["policy_version"],
        item["engine_mode"],
        item["symbol"],
        item["coin"],
        item["policy_snapshot_id"],
        item["forward_prediction_id"],
        item["risk_decision"],
        item["shadow_action"],
        item["product_decision"],
        item["max_position_pct"],
        json.dumps(item["evidence"], ensure_ascii=False),
        json.dumps(item["reasons"], ensure_ascii=False),
        json.dumps(item, ensure_ascii=False),
    ) for item in decisions])
    con.commit()


def main():
    now = utcnow()
    created_at = iso(now)
    run_id = created_at
    con = sqlite3.connect(DB_NAME)
    try:
        ensure_tables(con)
        policy = signal_risk_gate.latest_policy(con)
        forward_evidence = signal_risk_gate.forward_evidence(con)
        macro = latest_macro_context(con)
        whale = latest_whale_context(con)
        onchain_by_chain = load_onchain_context(con)

        decisions = []
        technical_errors = {}
        for symbol in SYMBOLS:
            coin = COIN_BY_SYMBOL[symbol]
            asset = latest_asset_context(
                con,
                int(policy["id"]) if policy else 0,
                symbol,
            )
            social = latest_social_context(con, coin)
            onchain = onchain_evidence_for_symbol(symbol, onchain_by_chain)
            candidate = latest_open_forward_candidate(con, symbol)
            gate = None
            if candidate:
                gate = signal_risk_gate.evaluate_forward_candidate(
                    con,
                    prediction_id=candidate["id"],
                    coin=candidate["coin"],
                    symbol=symbol,
                    model_version=candidate["model_version"],
                    now=now,
                    persist=False,
                )
            technical = None
            technical_error = None
            try:
                technical = technical_context(symbol)
            except Exception as exc:
                technical_error = str(exc)
                technical_errors[symbol] = technical_error

            decisions.append(build_decision(
                symbol=symbol,
                created_at=created_at,
                policy=policy,
                asset=asset,
                technical=technical,
                technical_error=technical_error,
                forward_evidence=forward_evidence,
                forward_candidate=candidate,
                forward_gate=gate,
                social=social,
                onchain=onchain,
                macro=macro,
                whale=whale,
                now=now,
            ))

        save_decisions(con, run_id, decisions)
    finally:
        con.close()

    print(
        f"AG Crypto Analyst - Decision Engine v{ENGINE_VERSION} | "
        f"mode={ENGINE_MODE}"
    )
    print(f"Policy: {POLICY_VERSION}")
    if policy:
        policy_time = signal_risk_gate.parse_time(policy["created_at"])
        age = (
            max(0.0, (now - policy_time).total_seconds() / 60.0)
            if policy_time else float("inf")
        )
        print(
            f"Risk: snapshot={policy['id']} | state={policy['market_state']} | "
            f"age={age:.1f}m"
        )
    else:
        print("Risk: MISSING -> tüm ürün kararları BEKLE")
    print(
        f"Forward: {forward_evidence['state']} | "
        f"settled={forward_evidence['settled']} | "
        f"hit={forward_evidence['hit_rate']:.1f}% | "
        f"wilson={forward_evidence['wilson_lower_pct']:.1f}% | "
        f"dir_return={forward_evidence['directional_return']:+.3f}%"
    )
    print("SYMBOL       TEKNİK   GÖLGE_ADAYI  ÜRÜN    RISK   CAP%")
    for item in decisions:
        tech = item["evidence"]["technical"]
        tech_direction = tech.get("direction", "MISSING")
        shadow = (
            item["shadow_action"] + "_ADAYI"
            if item["shadow_action"] in {"AL", "SAT"}
            else "BEKLE"
        )
        print(
            f"{item['symbol']:<12} {tech_direction:<9} {shadow:<12} "
            f"{item['product_decision']:<7} {item['risk_decision']:<6} "
            f"{item['max_position_pct']:>5.2f}"
        )
    print(
        f"Özet: kayıt={len(decisions)} | "
        f"gölge_AL={sum(x['shadow_action'] == 'AL' for x in decisions)} | "
        f"gölge_SAT={sum(x['shadow_action'] == 'SAT' for x in decisions)} | "
        f"gölge_BEKLE={sum(x['shadow_action'] == 'BEKLE' for x in decisions)} | "
        f"ürün_BEKLE={sum(x['product_decision'] == 'BEKLE' for x in decisions)}"
    )
    if technical_errors:
        print(f"Teknik kaynak hatası: {len(technical_errors)}; ilgili coinler BEKLE kaldı.")
    print(
        "Not: Gölge adayları alarm veya işlem kararı değildir. PROVISIONAL, "
        "WARMING_UP, NO_DATA, MONITOR_ONLY ve SHADOW_ONLY kanıt puanla "
        "güçlendirilmez. Risk BLOCK nihai vetodur."
    )
    return {
        "run_id": run_id,
        "engine_mode": ENGINE_MODE,
        "policy": policy,
        "forward_evidence": forward_evidence,
        "decisions": decisions,
        "technical_errors": technical_errors,
    }


if __name__ == "__main__":
    main()
