"""Evidence-gated learning review for clean forward PAPER trades.

The review observes net results after all simulated execution costs.  It may
open bounded challenger hypotheses, but it never mutates or promotes the live
paper policy automatically.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone

from paper_trading import external_trader_learning, external_trader_rationale
from paper_trading import continuous_improvement


LEARNING_VERSION = "PAPER_TRADE_LEARNING_REVIEW_V1"
MIN_TOTAL_CLOSED = 100
MIN_PATTERN_TRADES = 30
MIN_PATTERN_WEEKS = 3


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def week_key(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    year, week, _ = parsed.isocalendar()
    return f"{year}-W{week:02d}"


def ensure_tables(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS paper_agent_trade_learning_reviews (
            review_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            learning_version TEXT NOT NULL,
            validation_cohort TEXT NOT NULL,
            closed_count INTEGER NOT NULL,
            independent_weeks INTEGER NOT NULL,
            status TEXT NOT NULL,
            promotion_allowed INTEGER NOT NULL DEFAULT 0,
            evidence_json TEXT NOT NULL,
            UNIQUE(learning_version, validation_cohort, closed_count)
        );

        CREATE TABLE IF NOT EXISTS paper_agent_trade_learning_hypotheses (
            hypothesis_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            learning_version TEXT NOT NULL,
            validation_cohort TEXT NOT NULL,
            pattern_key TEXT NOT NULL,
            candidate_type TEXT NOT NULL,
            trade_count INTEGER NOT NULL,
            independent_weeks INTEGER NOT NULL,
            evidence_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'EVIDENCE_READY',
            promotion_allowed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(learning_version, validation_cohort, pattern_key, candidate_type)
        );
    """)


def load_closed(con, validation_cohort):
    position_columns = {row[1] for row in con.execute(
        "PRAGMA table_info(paper_agent_positions)"
    )}
    funding_clause = (
        "AND COALESCE(p.funding_source,'')!='PENDING_RECONCILIATION'"
        if "funding_source" in position_columns else ""
    )
    leverage_expr = "COALESCE(p.leverage,1)" if "leverage" in position_columns else "1"
    margin_expr = ("COALESCE(p.margin_allocated,p.notional)"
                   if "margin_allocated" in position_columns else "p.notional")
    tier_expr = ("COALESCE(p.risk_tier,'PAPER_RISK_TIER_1')"
                 if "risk_tier" in position_columns else "'PAPER_RISK_TIER_1'")
    liquidated_expr = ("COALESCE(p.liquidated,0)"
                       if "liquidated" in position_columns else "0")
    rows = con.execute(f"""
        SELECT p.position_id,p.symbol,p.horizon_hours,p.direction,p.exit_time,
               p.notional,p.net_pnl,COALESCE(f.regime,'UNKNOWN'),
               COALESCE(p.policy_version,'UNKNOWN'),
               COALESCE(p.decision_source,'UNIFIED_FORECAST_BASELINE'),
               {leverage_expr},{margin_expr},{tier_expr},{liquidated_expr}
        FROM paper_agent_positions p
        LEFT JOIN forecast_engine_predictions f ON f.forecast_id=p.forecast_id
        WHERE p.status='CLOSED' AND p.evidence_eligible=1
          AND p.validation_cohort=? AND p.net_pnl IS NOT NULL
          {funding_clause}
        ORDER BY p.exit_time,p.position_id
    """, (validation_cohort,)).fetchall()
    return [{
        "position_id": int(row[0]), "symbol": str(row[1]),
        "horizon": int(row[2]), "direction": str(row[3]),
        "exit_time": str(row[4]), "notional": float(row[5]),
        "net_pnl": float(row[6]), "regime": str(row[7]),
        "policy_version": str(row[8]), "decision_source": str(row[9]),
        "leverage": float(row[10]), "margin": float(row[11]),
        "risk_tier": str(row[12]), "liquidated": bool(row[13]),
    } for row in rows]


def metrics(rows):
    if not rows:
        return {
            "trades": 0, "weeks": 0, "net_pnl_usdt": 0.0,
            "mean_net_return_pct": 0.0, "win_rate_pct": 0.0,
            "largest_three_abs_pnl_share": 0.0,
        }
    returns = [100.0 * row["net_pnl"] / row["notional"] for row in rows]
    absolute_pnl = sorted((abs(row["net_pnl"]) for row in rows), reverse=True)
    return {
        "trades": len(rows),
        "weeks": len({week_key(row["exit_time"]) for row in rows}),
        "net_pnl_usdt": sum(row["net_pnl"] for row in rows),
        "mean_net_return_pct": statistics.fmean(returns),
        "win_rate_pct": 100.0 * statistics.fmean(row["net_pnl"] > 0 for row in rows),
        "largest_three_abs_pnl_share": (
            sum(absolute_pnl[:3]) / sum(absolute_pnl) if sum(absolute_pnl) else 0.0
        ),
    }


def grouped_metrics(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[f"DIRECTION:{row['direction']}"] .append(row)
        groups[f"HORIZON:{row['horizon']}h"].append(row)
        groups[f"REGIME:{row['regime']}"].append(row)
        groups[f"DIRECTION_HORIZON:{row['direction']}:{row['horizon']}h"].append(row)
        groups[f"POLICY:{row['policy_version']}"] .append(row)
        groups[f"DECISION_SOURCE:{row['decision_source']}"] .append(row)
        groups[f"LEVERAGE:{row['leverage']:g}X"].append(row)
        groups[f"RISK_TIER:{row['risk_tier']}"].append(row)
    return {key: metrics(value) for key, value in sorted(groups.items())}


def candidate_type(pattern_key):
    if pattern_key.startswith("DIRECTION_HORIZON:"):
        return "DIRECTION_HORIZON_POLICY_CHALLENGER"
    if pattern_key.startswith("DIRECTION:"):
        return "DIRECTION_SIZING_CHALLENGER"
    if pattern_key.startswith("HORIZON:"):
        return "HORIZON_SELECTION_CHALLENGER"
    if pattern_key.startswith("POLICY:") or pattern_key.startswith("DECISION_SOURCE:"):
        return "POLICY_SOURCE_REVIEW_ONLY"
    if pattern_key.startswith("LEVERAGE:") or pattern_key.startswith("RISK_TIER:"):
        return "PAPER_RISK_SIZING_CHALLENGER"
    return "REGIME_CONTEXT_CHALLENGER"


def discover_patterns(groups):
    patterns = []
    for key, score in groups.items():
        if score["trades"] < MIN_PATTERN_TRADES or score["weeks"] < MIN_PATTERN_WEEKS:
            continue
        if score["mean_net_return_pct"] >= 0 or score["win_rate_pct"] >= 50:
            continue
        patterns.append({
            "pattern_key": key,
            "candidate_type": candidate_type(key),
            "metrics": score,
            "claim_level": "STATISTICAL_ASSOCIATION_NOT_CAUSATION",
        })
    return sorted(patterns, key=lambda item: (-item["metrics"]["trades"], item["pattern_key"]))


def run(con, validation_cohort, now=None):
    now = now or datetime.now(timezone.utc)
    ensure_tables(con)
    rows = load_closed(con, validation_cohort)
    groups = grouped_metrics(rows)
    patterns = discover_patterns(groups)
    weeks = len({week_key(row["exit_time"]) for row in rows})
    if len(rows) < MIN_TOTAL_CLOSED:
        status = "COLLECTING_EVIDENCE"
    elif patterns:
        status = "CHALLENGER_EVIDENCE_READY"
    else:
        status = "NO_STABLE_UNDERPERFORMANCE_PATTERN"
    evidence = {
        "basis": "CLEAN_FORWARD_CLOSED_TRADES_NET_AFTER_ALL_COSTS",
        "overall": metrics(rows),
        "groups": groups,
        "patterns": patterns,
        "skill_claim": {
            "status": "UNPROVEN_PENDING_INDEPENDENT_REGIME_EVIDENCE",
            "positive_pnl_alone_is_skill_proof": False,
            "single_trade_attribution_allowed": False,
            "concentration_must_be_reported": True,
        },
        "policy": {
            "legacy_trades_included": False,
            "single_trade_update": False,
            "coin_blacklist": False,
            "automatic_policy_mutation": False,
            "automatic_champion_promotion": False,
            "minimum_total_closed": MIN_TOTAL_CLOSED,
            "minimum_pattern_trades": MIN_PATTERN_TRADES,
            "minimum_pattern_weeks": MIN_PATTERN_WEEKS,
        },
    }
    con.execute("""
        INSERT OR IGNORE INTO paper_agent_trade_learning_reviews(
            created_at,learning_version,validation_cohort,closed_count,
            independent_weeks,status,promotion_allowed,evidence_json
        ) VALUES(?,?,?,?,?,?,0,?)
    """, (iso(now), LEARNING_VERSION, validation_cohort, len(rows), weeks,
          status, json.dumps(evidence, ensure_ascii=False, sort_keys=True)))
    if status == "CHALLENGER_EVIDENCE_READY":
        for pattern in patterns:
            score = pattern["metrics"]
            con.execute("""
                INSERT OR IGNORE INTO paper_agent_trade_learning_hypotheses(
                    created_at,learning_version,validation_cohort,pattern_key,
                    candidate_type,trade_count,independent_weeks,evidence_json,
                    status,promotion_allowed
                ) VALUES(?,?,?,?,?,?,?,?, 'EVIDENCE_READY',0)
            """, (iso(now), LEARNING_VERSION, validation_cohort,
                  pattern["pattern_key"], pattern["candidate_type"],
                  score["trades"], score["weeks"],
                  json.dumps(pattern, ensure_ascii=False, sort_keys=True)))
    con.commit()
    external_research = external_trader_learning.run_review(con, now)
    external_research["rationale_review"] = (
        external_trader_rationale.review_available_closed_contexts(con, now)
    )
    improvement = continuous_improvement.run(con, now)
    return {
        "status": status, "closed": len(rows), "weeks": weeks,
        "patterns": patterns, "promotion_allowed": False,
        "external_research": external_research,
        "continuous_improvement": improvement,
    }
