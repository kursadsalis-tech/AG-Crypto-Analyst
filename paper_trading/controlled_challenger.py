"""Controlled forward challengers for PAPER learning.

A challenger is a shadow counterfactual over future natural incumbent trades.
It never creates, blocks, resizes, fills, or closes a position.  Promotion and
policy mutation remain explicitly disabled; the module only measures whether a
bounded rule would have improved final net-after-cost PAPER economics.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone


VERSION = "PAPER_CONTROLLED_CHALLENGER_V1"
RULE_KIND = "SUPPRESS_MATCHING_PATTERN"
MIN_FORWARD_TRADES = 30
MIN_FORWARD_WEEKS = 3
ELIGIBLE_CANDIDATE_TYPES = frozenset({
    "DIRECTION_HORIZON_POLICY_CHALLENGER",
    "DIRECTION_SIZING_CHALLENGER",
    "HORIZON_SELECTION_CHALLENGER",
    "PAPER_RISK_SIZING_CHALLENGER",
    "REGIME_CONTEXT_CHALLENGER",
})


def _iso(value):
    return value.astimezone(timezone.utc).isoformat()


def _parse(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _week_key(value):
    year, week, _ = _parse(value).isocalendar()
    return f"{year}-W{week:02d}"


def ensure_tables(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS paper_agent_controlled_challengers (
            challenger_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            challenger_version TEXT NOT NULL,
            validation_cohort TEXT NOT NULL,
            source_hypothesis_id INTEGER NOT NULL,
            pattern_key TEXT NOT NULL,
            candidate_type TEXT NOT NULL,
            rule_json TEXT NOT NULL,
            forward_start_at TEXT NOT NULL,
            status TEXT NOT NULL,
            forward_trade_count INTEGER NOT NULL DEFAULT 0,
            independent_weeks INTEGER NOT NULL DEFAULT 0,
            incumbent_net_pnl REAL NOT NULL DEFAULT 0,
            challenger_net_pnl REAL NOT NULL DEFAULT 0,
            improvement_net_pnl REAL NOT NULL DEFAULT 0,
            evidence_json TEXT NOT NULL,
            promotion_allowed INTEGER NOT NULL DEFAULT 0,
            policy_mutation_allowed INTEGER NOT NULL DEFAULT 0,
            real_order_allowed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(challenger_version, source_hypothesis_id)
        );

        CREATE TABLE IF NOT EXISTS paper_agent_controlled_challenger_evaluations (
            evaluation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            challenger_id INTEGER NOT NULL,
            forward_trade_count INTEGER NOT NULL,
            independent_weeks INTEGER NOT NULL,
            incumbent_net_pnl REAL NOT NULL,
            challenger_net_pnl REAL NOT NULL,
            improvement_net_pnl REAL NOT NULL,
            status TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            promotion_allowed INTEGER NOT NULL DEFAULT 0,
            policy_mutation_allowed INTEGER NOT NULL DEFAULT 0,
            real_order_allowed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(challenger_id, forward_trade_count)
        );
    """)


def pattern_keys(row):
    """Rebuild the same predeclared grouping keys used by trade learning."""
    return {
        f"DIRECTION:{row['direction']}",
        f"HORIZON:{row['horizon']}h",
        f"REGIME:{row['regime']}",
        f"DIRECTION_HORIZON:{row['direction']}:{row['horizon']}h",
        f"POLICY:{row['policy_version']}",
        f"DECISION_SOURCE:{row['decision_source']}",
        f"LEVERAGE:{row['leverage']:g}X",
        f"RISK_TIER:{row['risk_tier']}",
    }


def sync_from_hypotheses(con, validation_cohort, now, *, ensure=True):
    """Open bounded forward challengers without backfilling prior outcomes."""
    if ensure:
        ensure_tables(con)
    hypotheses = con.execute("""
        SELECT hypothesis_id,pattern_key,candidate_type
        FROM paper_agent_trade_learning_hypotheses
        WHERE validation_cohort=?
          AND status='EVIDENCE_READY'
          AND promotion_allowed=0
        ORDER BY hypothesis_id
    """, (validation_cohort,)).fetchall()
    created = 0
    for hypothesis_id, pattern_key, candidate_type in hypotheses:
        if str(candidate_type) not in ELIGIBLE_CANDIDATE_TYPES:
            continue
        rule = {
            "kind": RULE_KIND,
            "pattern_key": str(pattern_key),
            "live_trade_effect": False,
            "counterfactual_position_notional": 0.0,
            "real_order_authority": False,
        }
        evidence = {
            "basis": "OWN_CLEAN_FORWARD_PAPER_UNDERPERFORMANCE_HYPOTHESIS",
            "source_hypothesis_id": int(hypothesis_id),
            "forward_only_from_created_at": True,
            "backfill_allowed": False,
            "automatic_promotion": False,
            "policy_mutation_allowed": False,
        }
        cursor = con.execute("""
            INSERT OR IGNORE INTO paper_agent_controlled_challengers(
                created_at,challenger_version,validation_cohort,
                source_hypothesis_id,pattern_key,candidate_type,rule_json,
                forward_start_at,status,evidence_json,promotion_allowed,
                policy_mutation_allowed,real_order_allowed
            ) VALUES(?,?,?,?,?,?,?,?, 'FORWARD_COLLECTING', ?,0,0,0)
        """, (
            _iso(now), VERSION, validation_cohort, int(hypothesis_id),
            str(pattern_key), str(candidate_type),
            json.dumps(rule, sort_keys=True), _iso(now),
            json.dumps(evidence, sort_keys=True),
        ))
        created += int(cursor.rowcount == 1)
        con.commit()
    return created


def _evaluation(pattern_key, forward_start_at, rows):
    matched = [
        row for row in rows
        if _parse(row["selected_at"]) >= _parse(forward_start_at)
        and pattern_key in pattern_keys(row)
    ]
    incumbent_net_pnl = sum(float(row["net_pnl"]) for row in matched)
    challenger_net_pnl = 0.0
    improvement = challenger_net_pnl - incumbent_net_pnl
    weeks = len({_week_key(row["exit_time"]) for row in matched})
    returns = [
        100.0 * float(row["net_pnl"]) / float(row["notional"])
        for row in matched if float(row["notional"]) > 0
    ]
    incumbent_mean_return = (
        sum(returns) / len(returns) if returns else 0.0
    )
    incumbent_win_rate = (
        100.0 * sum(float(row["net_pnl"]) > 0 for row in matched) / len(matched)
        if matched else 0.0
    )

    if len(matched) < MIN_FORWARD_TRADES or weeks < MIN_FORWARD_WEEKS:
        status = "FORWARD_COLLECTING"
    elif improvement > 0.0:
        status = "FORWARD_BETTER_REVIEWABLE"
    else:
        status = "FORWARD_NOT_BETTER"

    evidence = {
        "basis": "POST_REGISTRATION_NATURAL_INCUMBENT_TRADES_NET_AFTER_ALL_COSTS",
        "rule_kind": RULE_KIND,
        "pattern_key": pattern_key,
        "forward_trade_count": len(matched),
        "independent_weeks": weeks,
        "incumbent_net_pnl": incumbent_net_pnl,
        "challenger_net_pnl": challenger_net_pnl,
        "improvement_net_pnl": improvement,
        "incumbent_mean_net_return_pct": incumbent_mean_return,
        "incumbent_win_rate_pct": incumbent_win_rate,
        "minimum_forward_trades": MIN_FORWARD_TRADES,
        "minimum_forward_weeks": MIN_FORWARD_WEEKS,
        "counterfactual_assumption": (
            "SUPPRESSED_MATCHING_TRADE_HAS_ZERO_PNL_AND_ZERO_CAPITAL_USAGE"
        ),
        "claim_level": "FORWARD_COUNTERFACTUAL_NOT_LIVE_POLICY",
        "automatic_promotion": False,
        "policy_mutation_allowed": False,
        "real_order_authority": False,
    }
    return {
        "status": status,
        "trade_count": len(matched),
        "weeks": weeks,
        "incumbent_net_pnl": incumbent_net_pnl,
        "challenger_net_pnl": challenger_net_pnl,
        "improvement_net_pnl": improvement,
        "evidence": evidence,
    }


def evaluate(con, validation_cohort, rows, now, *, ensure=True):
    if ensure:
        ensure_tables(con)
    challengers = con.execute("""
        SELECT challenger_id,pattern_key,forward_start_at
        FROM paper_agent_controlled_challengers
        WHERE challenger_version=? AND validation_cohort=?
        ORDER BY challenger_id
    """, (VERSION, validation_cohort)).fetchall()

    statuses = {}
    reviewable = 0
    for challenger_id, pattern_key, forward_start_at in challengers:
        result = _evaluation(str(pattern_key), str(forward_start_at), rows)
        statuses[result["status"]] = statuses.get(result["status"], 0) + 1
        reviewable += int(result["status"] == "FORWARD_BETTER_REVIEWABLE")
        evidence_json = json.dumps(result["evidence"], sort_keys=True)
        con.execute("""
            UPDATE paper_agent_controlled_challengers
            SET status=?,forward_trade_count=?,independent_weeks=?,
                incumbent_net_pnl=?,challenger_net_pnl=?,
                improvement_net_pnl=?,evidence_json=?,
                promotion_allowed=0,policy_mutation_allowed=0,
                real_order_allowed=0
            WHERE challenger_id=?
        """, (
            result["status"], result["trade_count"], result["weeks"],
            result["incumbent_net_pnl"], result["challenger_net_pnl"],
            result["improvement_net_pnl"], evidence_json,
            int(challenger_id),
        ))
        con.execute("""
            INSERT OR IGNORE INTO paper_agent_controlled_challenger_evaluations(
                created_at,challenger_id,forward_trade_count,independent_weeks,
                incumbent_net_pnl,challenger_net_pnl,improvement_net_pnl,
                status,evidence_json,promotion_allowed,
                policy_mutation_allowed,real_order_allowed
            ) VALUES(?,?,?,?,?,?,?,?,?,0,0,0)
        """, (
            _iso(now), int(challenger_id), result["trade_count"],
            result["weeks"], result["incumbent_net_pnl"],
            result["challenger_net_pnl"], result["improvement_net_pnl"],
            result["status"], evidence_json,
        ))
        # One evaluation and its summary commit together; CPU work for the
        # next challenger starts only after releasing the writer slot.
        con.commit()

    return {
        "challenger_count": len(challengers),
        "reviewable_count": reviewable,
        "statuses": statuses,
        "promotion_allowed": False,
        "policy_mutation_allowed": False,
        "real_orders_allowed": False,
    }


def run(con, validation_cohort, rows, now, *, ensure=True):
    if ensure:
        ensure_tables(con)
    created = sync_from_hypotheses(
        con, validation_cohort, now, ensure=False
    )
    con.commit()
    result = evaluate(
        con, validation_cohort, rows, now, ensure=False
    )
    return {"created": created, **result}
