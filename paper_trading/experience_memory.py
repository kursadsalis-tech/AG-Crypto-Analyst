"""Immutable PAPER decision theses and outcome-linked experience reviews.

This is one agent's memory, not a second portfolio or an alternate trader.  A
thesis is frozen before the entry outcome exists.  Reviews may describe what a
trade tested, but a single episode can never establish skill or causation.
"""
from __future__ import annotations

import hashlib
import json
import math
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
        technical_context = row.get("technical_context")
        observed_capabilities = ["UNIFIED_FORECAST_BASELINE"]
        if technical_context is not None:
            observed_capabilities.append("TECHNICAL_CONTEXT_SHADOW")
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
            "selection_economics": {
                "expected_net_edge_pct": float(row.get("expected_net_edge_pct") or 0.0),
                "confidence": float(row.get("confidence") or 0.0),
                "expected_profit_per_risk": float(row.get("expected_profit_per_risk") or 0.0),
                "expected_profit_usdt": float(row.get("expected_profit_usdt") or 0.0),
            },
            "strategy": {"id": strategy_id, "virtual_weight": float(strategy_weight)},
            "execution_risk": row.get("execution_risk", {
                "risk_tier": "PAPER_RISK_TIER_1", "leverage": 1.0,
            }),
            "evidence_used": row["evidence"],
            "technical_context_observed": technical_context,
            "capabilities_under_test": row["evidence"]["capabilities_used"],
            "capabilities_observed_but_not_decision_inputs": observed_capabilities,
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
    """Review every closed forward experience; evidence quality controls authority."""
    now = now or datetime.now(timezone.utc)
    position_columns = {r[1] for r in con.execute("PRAGMA table_info(paper_agent_positions)")}
    manager_exists = bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_position_management'"
    ).fetchone())
    events_exist = bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_position_events'"
    ).fetchone())
    def pcol(name, fallback):
        return "p." + name if name in position_columns else fallback
    margin_sql = pcol("margin_allocated", "p.notional")
    leverage_sql = pcol("leverage", "1")
    tier_sql = pcol("risk_tier", "'PAPER_RISK_TIER_1'")
    funding_sql = pcol("funding_source", "''")
    liquidation_sql = pcol("liquidation_price", "NULL")
    cohort_clause = (
        "AND p.validation_cohort=t.validation_cohort"
        if "validation_cohort" in position_columns else ""
    )
    manager_select = (
        "m.stop_distance_pct,m.current_stop_price,m.best_price"
        if manager_exists else "NULL,NULL,NULL"
    )
    manager_join = (
        "LEFT JOIN paper_position_management m ON m.position_id=p.position_id"
        if manager_exists else ""
    )
    rows = con.execute(f"""
        SELECT p.position_id,p.net_pnl,p.notional,p.gross_pnl,p.entry_fee,p.exit_fee,
               p.entry_spread_cost,p.entry_slippage_cost,p.exit_spread_cost,
               p.exit_slippage_cost,p.funding_cost,p.liquidated,p.close_reason,
               t.thesis_json,t.thesis_sha256,COALESCE({margin_sql},p.notional),
               COALESCE({leverage_sql},1),COALESCE({tier_sql},'PAPER_RISK_TIER_1'),
               COALESCE({funding_sql},''),{liquidation_sql},
               {manager_select}
        FROM paper_agent_positions p
        JOIN paper_agent_decision_theses t ON t.position_id=p.position_id
        {manager_join}
        LEFT JOIN paper_agent_experience_reviews r ON r.position_id=p.position_id
        WHERE p.status='CLOSED' AND p.net_pnl IS NOT NULL
          {cohort_clause}
          AND (
              r.position_id IS NULL
              OR (
                  COALESCE({funding_sql},'')!='PENDING_RECONCILIATION'
                  AND json_valid(r.review_json)
                  AND json_extract(r.review_json,'$.evidence_assessment.economics_final')=0
                  AND json_extract(r.review_json,'$.evidence_assessment.limitations') LIKE '%FUNDING_PENDING%'
              )
          )
        ORDER BY p.exit_time,p.position_id
    """).fetchall()
    created = 0
    for row in rows:
        (position_id, net_pnl, notional, gross_pnl, entry_fee, exit_fee,
         entry_spread, entry_slippage, exit_spread, exit_slippage, funding,
         liquidated, close_reason, thesis_json, thesis_sha, margin_allocated,
         leverage, risk_tier, funding_source, liquidation_price,
         stop_distance_pct, final_stop_price, best_price) = row
        events = (con.execute("""
            SELECT event_type,evidence_json,fill_price,fill_quantity
            FROM paper_position_events
            WHERE position_id=? AND event_type IN ('ENTRY_FILLED','POSITION_CLOSED')
        """, (position_id,)).fetchall() if events_exist else [])
        event_map = {event[0]: event for event in events}
        limitations = []
        for event_type in ("ENTRY_FILLED", "POSITION_CLOSED"):
            event = event_map.get(event_type)
            if not event:
                limitations.append("MISSING_" + event_type)
                continue
            try:
                evidence = json.loads(event[1] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                evidence = {}
            missing = [key for key in (
                "best_bid","best_ask","quote_timestamp_ms","executable_reference_price"
            ) if evidence.get(key) is None]
            if event[2] is None or float(event[3] or 0) <= 0:
                missing.append("FILL")
            if missing:
                limitations.append(event_type + "_INCOMPLETE:" + ",".join(sorted(set(missing))))
        if funding_source == "PENDING_RECONCILIATION":
            limitations.append("FUNDING_PENDING")
        execution_complete = not any(
            item.startswith("MISSING_") or "_INCOMPLETE:" in item for item in limitations
        )
        economics_final = funding_source != "PENDING_RECONCILIATION"
        evidence_confidence = (
            "HIGH" if execution_complete and economics_final
            else "MEDIUM" if execution_complete or economics_final else "LOW"
        )
        net_pnl, notional = float(net_pnl), float(notional)
        margin = float(margin_allocated or 0)
        # Retain anomalous economics as a learnable observation; do not crash
        # the entire review batch or silently promote invalid denominators.
        if not math.isfinite(notional) or notional <= 0:
            limitations.append("INVALID_NOTIONAL")
        if not math.isfinite(margin) or margin <= 0:
            limitations.append("INVALID_MARGIN")
        economics_valid = notional > 0 and margin > 0 and all(
            math.isfinite(v) for v in (net_pnl, notional, margin)
        )
        if not economics_valid:
            economics_final = False
            evidence_confidence = "LOW"
        net_return_pct = (
            100.0 * net_pnl / notional
            if notional > 0 and math.isfinite(notional) and math.isfinite(net_pnl)
            else None
        )
        margin_return_pct = (
            100.0 * net_pnl / margin
            if margin > 0 and math.isfinite(margin) and math.isfinite(net_pnl)
            else None
        )
        # The SQL review column is NOT NULL. Keep the exact unknown value in
        # review_json; use a finite sentinel only for this legacy index column.
        indexed_return_pct = net_return_pct if net_return_pct is not None else 0.0
        outcome = "WIN" if net_pnl > 0 else "LOSS" if net_pnl < 0 else "BREAKEVEN"
        costs = {"fees": float(entry_fee or 0)+float(exit_fee or 0),
                 "spread": float(entry_spread or 0)+float(exit_spread or 0),
                 "slippage": float(entry_slippage or 0)+float(exit_slippage or 0),
                 "funding": float(funding or 0)}
        review = {
            "frozen_thesis": json.loads(thesis_json),
            "evidence_assessment": {
                "execution_complete": execution_complete,
                "economics_final": economics_final,
                "confidence": evidence_confidence,
                "limitations": limitations,
            },
            "economic_result": {
                "class": outcome, "gross_pnl": float(gross_pnl or 0),
                "net_pnl": net_pnl, "net_return_pct": net_return_pct,
                "return_on_margin_pct": margin_return_pct,
                "gross_exposure": notional, "margin_allocated": float(margin_allocated),
                "leverage": float(leverage), "risk_tier": str(risk_tier),
                "costs": costs, "liquidated": bool(liquidated),
                "close_reason": close_reason,
            },
            "risk_review": {
                "planned_stop_distance_pct": stop_distance_pct,
                "final_stop_price": final_stop_price,
                "best_price_seen": best_price,
                "liquidation_price": liquidation_price,
                "leverage": float(leverage),
                "margin_allocated": float(margin_allocated),
                "return_on_margin_pct": margin_return_pct,
                "close_reason": close_reason,
                "questions": [
                    "DID_STOP_GEOMETRY_MATCH_THE_TRADE_THESIS",
                    "DID_SIZING_KEEP_PLANNED_LOSS_WITHIN_RISK_BUDGET",
                    "DID_LEVERAGE_ADD_CAPITAL_EFFICIENCY_OR_AMPLIFY_LOSS",
                    "DID_STOP_OR_TIMEOUT_EXIT_TOO_EARLY_OR_TOO_LATE",
                ],
                "claim_level": "EPISODE_OBSERVATION_NOT_CAUSATION",
            },
            "learning": {
                "experience_retained": True,
                "lesson_generation_allowed": True,
                "validated_performance_eligible": bool(execution_complete and economics_final and economics_valid),
                "policy_mutation_allowed": False,
                "principle": "EVERY_EXPERIENCE_CAN_TEACH; AUTHORITY_MUST_BE_EARNED",
            },
            "attribution": {"skill": "UNRESOLVED_SINGLE_EPISODE",
                            "causal_claim": False},
            "next_test": "RETEST_DISCOVERED_LESSON_OUT_OF_SAMPLE_WITH_STRONGER_EVIDENCE",
        }
        con.execute("""
            INSERT INTO paper_agent_experience_reviews(
                position_id,reviewed_at,memory_version,thesis_sha256,outcome_class,
                net_pnl,net_return_pct,review_json,causal_claim,skill_proven,
                policy_mutation_allowed
            ) VALUES(?,?,?,?,?,?,?,?,0,0,0)
            ON CONFLICT(position_id) DO UPDATE SET
                reviewed_at=excluded.reviewed_at,
                memory_version=excluded.memory_version,
                thesis_sha256=excluded.thesis_sha256,
                outcome_class=excluded.outcome_class,
                net_pnl=excluded.net_pnl,
                net_return_pct=excluded.net_return_pct,
                review_json=excluded.review_json,
                causal_claim=0,skill_proven=0,policy_mutation_allowed=0
        """, (position_id, iso(now), MEMORY_VERSION, thesis_sha, outcome, net_pnl,
              indexed_return_pct, _canonical(review)))
        created += 1
    con.commit()
    return created


def record_legacy_summary(con, now=None):
    """Retain old experience without inventing outcome-blind historical theses."""
    now = now or datetime.now(timezone.utc)
    position_columns = {row[1] for row in con.execute(
        "PRAGMA table_info(paper_agent_positions)"
    )}
    funding_clause = (
        "AND COALESCE(p.funding_source,'')!='PENDING_RECONCILIATION'"
        if "funding_source" in position_columns else ""
    )
    rows = con.execute(f"""
        SELECT position_id,direction,horizon_hours,net_pnl,notional,selected_at
        FROM paper_agent_positions p
        WHERE status='CLOSED' AND net_pnl IS NOT NULL
          AND evidence_eligible=1
          {funding_clause}
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
