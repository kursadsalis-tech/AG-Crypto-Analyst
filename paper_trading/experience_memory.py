"""Immutable PAPER decision theses and outcome-linked experience reviews.

This is one agent's memory, not a second portfolio or an alternate trader.  A
thesis is frozen before the entry outcome exists.  Reviews may describe what a
trade tested, but a single episode can never establish skill or causation.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone


MEMORY_VERSION = "PAPER_EXPERIENCE_MEMORY_V1"
LEGACY_SUMMARY_VERSION = "PAPER_LEGACY_EXPERIENCE_AUDIT_V2_CLEAN_ONLY"


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def _canonical(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(payload):
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def ensure_tables(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS paper_agent_decision_theses (
            position_id INTEGER PRIMARY KEY,
            created_at TEXT NOT NULL,
            memory_version TEXT NOT NULL,
            agent_version TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            validation_cohort TEXT NOT NULL,
            decision_source TEXT NOT NULL,
            context_cutoff_at TEXT NOT NULL,
            thesis_json TEXT NOT NULL,
            outcome_known_at_capture INTEGER NOT NULL DEFAULT 0,
            thesis_sha256 TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS paper_agent_experience_reviews (
            position_id INTEGER PRIMARY KEY,
            reviewed_at TEXT NOT NULL,
            memory_version TEXT NOT NULL,
            thesis_sha256 TEXT NOT NULL,
            outcome_class TEXT NOT NULL,
            net_pnl REAL NOT NULL,
            net_return_pct REAL NOT NULL,
            review_json TEXT NOT NULL,
            causal_claim INTEGER NOT NULL DEFAULT 0,
            skill_proven INTEGER NOT NULL DEFAULT 0,
            policy_mutation_allowed INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS paper_agent_experience_summaries (
            summary_version TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            cutoff_position_id INTEGER NOT NULL,
            trade_count INTEGER NOT NULL,
            summary_json TEXT NOT NULL,
            skill_proven INTEGER NOT NULL DEFAULT 0,
            promotion_allowed INTEGER NOT NULL DEFAULT 0
        );
    """)


def capture_decision_thesis(con, *, position_id, row, agent_version,
                            policy_version, validation_cohort, strategy_id,
                            strategy_weight, selected_at, planned_entry_time):
    """Freeze the evidence actually used by the current decision path."""
    status = con.execute(
        "SELECT status FROM paper_agent_positions WHERE position_id=?", (position_id,)
    ).fetchone()
    if not status or status[0] != "PENDING_ENTRY":
        raise ValueError("THESIS_MUST_BE_CAPTURED_WHILE_OUTCOME_UNKNOWN")
    if row.get("decision_source") == "MARKET_BEHAVIOR_CORE":
        thesis = {
            "position_id": int(position_id),
            "decision_source": "MARKET_BEHAVIOR_CORE",
            "selected_at": iso(selected_at),
            "context_cutoff_at": datetime.fromtimestamp(
                row["evidence"]["evidence_minute_end_ms"] / 1000, timezone.utc
            ).isoformat(),
            "planned_entry_time": iso(planned_entry_time),
            "symbol": str(row["symbol"]), "horizon_hours": int(row["horizon"]),
            "direction": str(row["direction"]),
            "strategy": {"id": strategy_id, "virtual_weight": float(strategy_weight)},
            "evidence_used": row["evidence"],
            "capabilities_under_test": row["evidence"]["capabilities_used"],
            "capabilities_observed_but_not_decision_inputs": ["UNIFIED_FORECAST_BASELINE"],
            "claims": {"outcome_used": False, "causal_claim": False,
                       "skill_already_proven": False},
        }
    else:
        thesis = {
        "position_id": int(position_id),
        "decision_source": "UNIFIED_FORECAST_BASELINE_INPUT",
        "selected_at": iso(selected_at),
        "context_cutoff_at": str(row["as_of"]),
        "planned_entry_time": iso(planned_entry_time),
        "symbol": str(row["symbol"]),
        "horizon_hours": int(row["horizon"]),
        "direction": str(row["direction"]),
        "strategy": {"id": strategy_id, "virtual_weight": float(strategy_weight)},
        "evidence_used": {
            "p_up": float(row["p_up"]), "p_down": float(row["p_down"]),
            "p_neutral": float(row["p_neutral"]),
            "direction_probability": float(row["confidence"]),
            "probability_gap": float(row["gap"]),
            "current_price": float(row["current_price"]),
            "target_price": float(row["target_price"]),
            "predicted_move_pct": float(row["predicted_move_pct"]),
            "risk_eligible": bool(row["risk_eligible"]),
            "risk_cap_pct": float(row["risk_cap_pct"]),
            "model_version": str(row["model_version"]),
            "artifact_hash": str(row["artifact_hash"]),
        },
        "capabilities_under_test": [
            "FORECAST_DIRECTION", "TARGET_DIRECTION_ALIGNMENT",
            "RISK_GATE", "COST_AFTER_EXECUTION",
        ],
        "capabilities_observed_but_not_decision_inputs": [
            "MARKET_BEHAVIOR_OBSERVER", "ORDER_BOOK", "EXTERNAL_TRADERS",
        ],
        "claims": {
            "outcome_used": False, "causal_claim": False,
            "skill_already_proven": False,
        },
        }
    digest = _sha(thesis)
    con.execute("""
        INSERT INTO paper_agent_decision_theses(
            position_id,created_at,memory_version,agent_version,policy_version,
            validation_cohort,decision_source,context_cutoff_at,thesis_json,
            outcome_known_at_capture,thesis_sha256
        ) VALUES(?,?,?,?,?,?,?,?,?,0,?)
    """, (position_id, iso(selected_at), MEMORY_VERSION, agent_version,
          policy_version, validation_cohort, thesis["decision_source"],
          thesis["context_cutoff_at"], _canonical(thesis), digest))
    return digest


def review_closed_positions(con, now=None):
    now = now or datetime.now(timezone.utc)
    rows = con.execute("""
        SELECT p.position_id,p.net_pnl,p.notional,p.gross_pnl,p.entry_fee,p.exit_fee,
               p.entry_spread_cost,p.entry_slippage_cost,p.exit_spread_cost,
               p.exit_slippage_cost,p.funding_cost,p.liquidated,p.close_reason,
               t.thesis_json,t.thesis_sha256
        FROM paper_agent_positions p
        JOIN paper_agent_decision_theses t ON t.position_id=p.position_id
        LEFT JOIN paper_agent_experience_reviews r ON r.position_id=p.position_id
        WHERE p.status='CLOSED' AND p.net_pnl IS NOT NULL AND r.position_id IS NULL
        ORDER BY p.exit_time,p.position_id
    """).fetchall()
    created = 0
    for row in rows:
        (position_id, net_pnl, notional, gross_pnl, entry_fee, exit_fee,
         entry_spread, entry_slippage, exit_spread, exit_slippage, funding,
         liquidated, close_reason, thesis_json, thesis_sha) = row
        net_pnl, notional = float(net_pnl), float(notional)
        outcome = "WIN" if net_pnl > 0 else "LOSS" if net_pnl < 0 else "BREAKEVEN"
        costs = {
            "fees": float(entry_fee or 0) + float(exit_fee or 0),
            "spread": float(entry_spread or 0) + float(exit_spread or 0),
            "slippage": float(entry_slippage or 0) + float(exit_slippage or 0),
            "funding": float(funding or 0),
        }
        review = {
            "frozen_thesis": json.loads(thesis_json),
            "economic_result": {
                "class": outcome, "gross_pnl": float(gross_pnl or 0),
                "net_pnl": net_pnl, "net_return_pct": 100.0 * net_pnl / notional,
                "costs": costs, "liquidated": bool(liquidated),
                "close_reason": close_reason,
            },
            "capability_assessment": {
                "FORECAST_DIRECTION": "SUPPORTED_THIS_EPISODE" if net_pnl > 0 else "NOT_SUPPORTED_THIS_EPISODE",
                "COST_AFTER_EXECUTION": "POSITIVE_AFTER_COSTS" if net_pnl > 0 else "NOT_POSITIVE_AFTER_COSTS",
                "RISK_GATE": "NO_LIQUIDATION" if not liquidated else "FAILED_LIQUIDATION_TEST",
            },
            "attribution": {
                "skill": "UNRESOLVED_SINGLE_EPISODE",
                "regime": "REQUIRES_COHORT_EVIDENCE",
                "luck": "CANNOT_BE_SEPARATED_FROM_SKILL_BY_ONE_TRADE",
                "causal_claim": False,
            },
            "lesson": "EXPERIENCE_RECORDED_NOT_A_SKILL_OR_POLICY_PROMOTION",
            "next_test": "REPEAT_OUT_OF_SAMPLE_ACROSS_INDEPENDENT_MARKET_REGIMES",
        }
        con.execute("""
            INSERT INTO paper_agent_experience_reviews(
                position_id,reviewed_at,memory_version,thesis_sha256,outcome_class,
                net_pnl,net_return_pct,review_json,causal_claim,skill_proven,
                policy_mutation_allowed
            ) VALUES(?,?,?,?,?,?,?,?,0,0,0)
        """, (position_id, iso(now), MEMORY_VERSION, thesis_sha, outcome, net_pnl,
              100.0 * net_pnl / notional, _canonical(review)))
        created += 1
    con.commit()
    return created


def record_legacy_summary(con, now=None):
    """Retain old experience without inventing outcome-blind historical theses."""
    now = now or datetime.now(timezone.utc)
    rows = con.execute("""
        SELECT position_id,direction,horizon_hours,net_pnl,notional,selected_at
        FROM paper_agent_positions p
        WHERE status='CLOSED' AND net_pnl IS NOT NULL
          AND evidence_eligible=1
          AND NOT EXISTS (SELECT 1 FROM paper_agent_decision_theses t
                          WHERE t.position_id=p.position_id)
        ORDER BY position_id
    """).fetchall()
    if not rows:
        return None
    pnl = [float(row[3]) for row in rows]
    directions = {str(row[1]): sum(1 for item in rows if item[1] == row[1]) for row in rows}
    ranked = sorted((abs(value) for value in pnl), reverse=True)
    summary = {
        "basis": "RETROSPECTIVE_AUDIT_NO_FROZEN_PRETRADE_THESIS",
        "supersedes": "PAPER_LEGACY_EXPERIENCE_AUDIT_V1",
        "eligibility": "EVIDENCE_ELIGIBLE_ONLY_LOOKAHEAD_QUARANTINE_EXCLUDED",
        "trade_count": len(rows), "net_pnl_usdt": sum(pnl),
        "wins": sum(value > 0 for value in pnl), "losses": sum(value < 0 for value in pnl),
        "directions": directions,
        "largest_three_abs_pnl_share": (sum(ranked[:3]) / sum(ranked) if sum(ranked) else 0.0),
        "assessment": "ECONOMIC_RESULT_RETAINED_SKILL_UNPROVEN",
        "limitations": [
            "NO_FROZEN_PRETRADE_THESIS", "REGIME_AND_DIRECTION_CONCENTRATION_NOT_INDEPENDENCE",
            "PROFIT_DOES_NOT_SEPARATE_SKILL_FROM_LUCK",
        ],
        "may_train_policy_automatically": False,
    }
    con.execute("""
        INSERT OR IGNORE INTO paper_agent_experience_summaries(
            summary_version,created_at,cutoff_position_id,trade_count,summary_json,
            skill_proven,promotion_allowed
        ) VALUES(?,?,?,?,?,0,0)
    """, (LEGACY_SUMMARY_VERSION, iso(now), max(int(row[0]) for row in rows),
          len(rows), _canonical(summary)))
    con.commit()
    return summary
