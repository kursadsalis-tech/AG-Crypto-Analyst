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


LEARNING_VERSION = "PAPER_TRADE_LEARNING_REVIEW_V2_TECHNICAL_SHADOW"
MIN_TOTAL_CLOSED = 100
MIN_PATTERN_TRADES = 30
MIN_PATTERN_WEEKS = 3
MIN_TECHNICAL_SHADOW_TRADES = 30
MIN_TECHNICAL_SHADOW_DAYS = 3


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


def _execution_evidence_clause():
    # This gate is deliberately for validated performance/promotion metrics,
    # not for memory or discovery.  Every closed experience is retained by
    # experience_memory; only economically final, executable round trips may
    # claim calibrated performance authority.
    return """
      AND EXISTS (
        SELECT 1 FROM paper_position_events ee
        WHERE ee.position_id=p.position_id AND ee.event_type='ENTRY_FILLED'
          AND ee.fill_price IS NOT NULL AND ee.fill_quantity>0
          AND json_extract(ee.evidence_json,'$.best_bid') IS NOT NULL
          AND json_extract(ee.evidence_json,'$.best_ask') IS NOT NULL
          AND json_extract(ee.evidence_json,'$.quote_timestamp_ms') IS NOT NULL
          AND json_extract(ee.evidence_json,'$.executable_reference_price') IS NOT NULL
      )
      AND EXISTS (
        SELECT 1 FROM paper_position_events xe
        WHERE xe.position_id=p.position_id AND xe.event_type='POSITION_CLOSED'
          AND xe.fill_price IS NOT NULL AND xe.fill_quantity>0
          AND json_extract(xe.evidence_json,'$.best_bid') IS NOT NULL
          AND json_extract(xe.evidence_json,'$.best_ask') IS NOT NULL
          AND json_extract(xe.evidence_json,'$.quote_timestamp_ms') IS NOT NULL
          AND json_extract(xe.evidence_json,'$.executable_reference_price') IS NOT NULL
      )
    """


def load_closed(con, validation_cohort):
    # A legacy or freshly initialized database may not have the executable
    # position-event ledger yet.  Without both event sides there is no
    # performance-grade round trip: retain observations elsewhere, but do
    # not award learning/promotion evidence.
    if not con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_position_events'"
    ).fetchone():
        return []
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
          {_execution_evidence_clause()}
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


def _day_key(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date().isoformat()


def _technical_relation(direction, alignment):
    direction = str(direction or "").upper()
    alignment = str(alignment or "").upper()
    if alignment == "ALIGNED_BULLISH":
        return "SUPPORTS_DIRECTION" if direction == "LONG" else "OPPOSES_DIRECTION"
    if alignment == "ALIGNED_BEARISH":
        return "SUPPORTS_DIRECTION" if direction == "SHORT" else "OPPOSES_DIRECTION"
    return "MIXED_OR_UNALIGNED"


def _technical_metrics(rows):
    if not rows:
        return {
            "trades": 0,
            "independent_days": 0,
            "net_pnl_usdt": 0.0,
            "mean_net_return_pct": 0.0,
            "median_net_return_pct": 0.0,
            "win_rate_pct": 0.0,
            "largest_three_abs_pnl_share": 0.0,
        }
    returns = [float(row["net_return_pct"]) for row in rows]
    absolute_pnl = sorted((abs(float(row["net_pnl"])) for row in rows), reverse=True)
    total_abs = sum(absolute_pnl)
    return {
        "trades": len(rows),
        "independent_days": len({_day_key(row["exit_time"]) for row in rows}),
        "net_pnl_usdt": sum(float(row["net_pnl"]) for row in rows),
        "mean_net_return_pct": statistics.fmean(returns),
        "median_net_return_pct": statistics.median(returns),
        "win_rate_pct": 100.0 * statistics.fmean(
            float(row["net_pnl"]) > 0.0 for row in rows
        ),
        "largest_three_abs_pnl_share": (
            sum(absolute_pnl[:3]) / total_abs if total_abs else 0.0
        ),
    }


def technical_shadow_review(con, validation_cohort):
    """Measure frozen technical context against later net outcomes, read-only.

    This review cannot open challengers, mutate policy, size trades, or grant
    promotion authority. It only summarizes forward associations after a
    technical context was frozen into an already-selected PAPER thesis.
    """
    required = {
        "paper_agent_decision_theses",
        "paper_agent_experience_reviews",
        "paper_agent_positions",
    }
    existing = {
        row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if not required.issubset(existing):
        return {
            "status": "NO_TECHNICAL_SHADOW_EVIDENCE",
            "basis": "FROZEN_TECHNICAL_CONTEXT_VS_REALIZED_NET_AFTER_COST",
            "overall": _technical_metrics([]),
            "groups": {},
            "authority": {
                "direction": False, "ranking": False, "confidence": False,
                "sizing": False, "stop": False, "promotion_allowed": False,
                "policy_mutation_allowed": False,
            },
        }

    rows = con.execute("""
        SELECT p.position_id,p.direction,p.exit_time,r.net_pnl,r.review_json
        FROM paper_agent_positions p
        JOIN paper_agent_decision_theses t USING(position_id)
        JOIN paper_agent_experience_reviews r USING(position_id)
        WHERE p.status='CLOSED'
          AND p.evidence_eligible=1
          AND p.validation_cohort=?
          AND t.validation_cohort=?
        ORDER BY p.exit_time,p.position_id
    """, (validation_cohort, validation_cohort)).fetchall()

    usable = []
    for position_id, direction, exit_time, net_pnl, review_json in rows:
        try:
            review = json.loads(review_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        assessment = review.get("evidence_assessment") or {}
        if (
            assessment.get("execution_complete") is not True
            or assessment.get("economics_final") is not True
        ):
            continue
        economic = review.get("economic_result") or {}
        net_return = economic.get("net_return_pct")
        if net_return is None:
            continue
        thesis = review.get("frozen_thesis") or {}
        # Once a technical trade plan is an active execution input, this trade
        # no longer belongs in the earlier shadow-only cohort.
        if thesis.get("technical_trade_plan") is not None:
            continue
        technical = thesis.get("technical_context_observed")
        if not isinstance(technical, dict):
            continue
        if technical.get("observation_only") is not True:
            continue
        authority = technical.get("authority") or {}
        if any(
            bool(authority.get(key))
            for key in (
                "direction_input","ranking_input","confidence_input",
                "sizing_input","stop_input",
            )
        ):
            continue
        summary = technical.get("summary") or {}
        alignment = str(summary.get("alignment") or "UNKNOWN")
        usable.append({
            "position_id": int(position_id),
            "direction": str(direction),
            "exit_time": str(exit_time),
            "net_pnl": float(net_pnl),
            "net_return_pct": float(net_return),
            "alignment": alignment,
            "relation": _technical_relation(direction, alignment),
            "trend_1h": str(summary.get("trend_1h") or "UNKNOWN"),
            "trend_4h": str(summary.get("trend_4h") or "UNKNOWN"),
        })

    groups = defaultdict(list)
    for row in usable:
        groups[f"RELATION:{row['relation']}"].append(row)
        groups[f"ALIGNMENT:{row['alignment']}"].append(row)
        groups[f"DIRECTION:{row['direction']}|RELATION:{row['relation']}"].append(row)

    overall = _technical_metrics(usable)
    grouped = {key: _technical_metrics(value) for key, value in sorted(groups.items())}
    if not usable:
        status = "NO_TECHNICAL_SHADOW_EVIDENCE"
    elif (
        overall["trades"] < MIN_TECHNICAL_SHADOW_TRADES
        or overall["independent_days"] < MIN_TECHNICAL_SHADOW_DAYS
    ):
        status = "COLLECTING_FORWARD_EVIDENCE"
    else:
        status = "ASSOCIATION_REVIEWABLE_NO_AUTHORITY"

    supports = grouped.get("RELATION:SUPPORTS_DIRECTION")
    opposes = grouped.get("RELATION:OPPOSES_DIRECTION")
    comparison = None
    if supports and opposes:
        comparison = {
            "supports_minus_opposes_mean_net_return_pct": (
                supports["mean_net_return_pct"] - opposes["mean_net_return_pct"]
            ),
            "supports_minus_opposes_win_rate_pct": (
                supports["win_rate_pct"] - opposes["win_rate_pct"]
            ),
            "claim_level": "FORWARD_ASSOCIATION_NOT_CAUSATION",
        }

    return {
        "status": status,
        "basis": "FROZEN_TECHNICAL_CONTEXT_VS_REALIZED_NET_AFTER_COST",
        "overall": overall,
        "groups": grouped,
        "comparison": comparison,
        "minimum_reviewable_trades": MIN_TECHNICAL_SHADOW_TRADES,
        "minimum_reviewable_days": MIN_TECHNICAL_SHADOW_DAYS,
        "claim_level": "FORWARD_ASSOCIATION_NOT_CAUSATION",
        "authority": {
            "direction": False, "ranking": False, "confidence": False,
            "sizing": False, "stop": False, "promotion_allowed": False,
            "policy_mutation_allowed": False,
        },
    }


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
    # Trade count is an evidence sufficiency gate, not an objective. Rank
    # harmful challengers by economic damage first so the learner investigates
    # what loses the most money after costs instead of rewarding high activity.
    return sorted(
        patterns,
        key=lambda item: (
            item["metrics"]["mean_net_return_pct"],
            item["metrics"]["net_pnl_usdt"],
            -item["metrics"]["trades"],
            item["pattern_key"],
        ),
    )


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
        "technical_shadow": technical_shadow_review(con, validation_cohort),
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
    technical_shadow = evidence["technical_shadow"]
    return {
        "status": status, "closed": len(rows), "weeks": weeks,
        "patterns": patterns, "promotion_allowed": False,
        "technical_shadow": technical_shadow,
        "external_research": external_research,
        "continuous_improvement": improvement,
    }
