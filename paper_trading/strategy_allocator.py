"""Adaptive virtual-capital allocation for PAPER-only strategy sleeves."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone


VERSION = "PAPER_STRATEGY_ALLOCATOR_V1"
BASELINE = "CHAMPION_BASELINE"
MIN_CLOSED_FOR_REALLOCATION = 10
MIN_WEIGHT = 0.05
MAX_WEIGHT = 0.70


def _iso(value):
    return value.astimezone(timezone.utc).isoformat()


def ensure_tables(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS paper_agent_strategy_allocations (
            strategy_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            strategy_type TEXT NOT NULL,
            rule_json TEXT NOT NULL,
            allocation_weight REAL NOT NULL,
            closed_trades INTEGER NOT NULL DEFAULT 0,
            net_pnl REAL NOT NULL DEFAULT 0,
            net_return_pct REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            real_order_allowed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS paper_agent_strategy_allocation_history (
            allocation_history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            allocator_version TEXT NOT NULL,
            weights_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            real_order_allowed INTEGER NOT NULL DEFAULT 0
        );
    """)


def sync_strategies(con, now):
    ensure_tables(con)
    stamp = _iso(now)
    con.execute("""
        INSERT OR IGNORE INTO paper_agent_strategy_allocations(
          strategy_id,created_at,updated_at,strategy_type,rule_json,
          allocation_weight,status,real_order_allowed
        ) VALUES(?,?,?,?,?,1.0,'ACTIVE',0)
    """, (BASELINE, stamp, stamp, "BASELINE_FORECAST", "{}"))
    rows = con.execute("""
        SELECT pattern_key,rule_json FROM paper_agent_research_candidates
        WHERE status IN ('FORWARD_COLLECTING','READY_FOR_ORCHESTRATOR_REVIEW')
          AND source_kind='EXTERNAL_RETROSPECTIVE'
    """).fetchall()
    for pattern, rule in rows:
        strategy_id = "CHALLENGER_" + str(pattern).replace(":", "_")
        con.execute("""
            INSERT OR IGNORE INTO paper_agent_strategy_allocations(
              strategy_id,created_at,updated_at,strategy_type,rule_json,
              allocation_weight,status,real_order_allowed
            ) VALUES(?,?,?,?,?,0,'EXPLORING',0)
        """, (strategy_id, stamp, stamp, "LEARNED_DIRECTION_FILTER", rule))


def rebalance(con, now):
    """Reward net-after-cost performance while preserving bounded exploration."""
    sync_strategies(con, now)
    con.commit()
    strategies = con.execute("""
        SELECT strategy_id,rule_json FROM paper_agent_strategy_allocations
        WHERE status IN ('ACTIVE','EXPLORING') ORDER BY strategy_id
    """).fetchall()
    scores = []
    has_execution_ledger = bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_position_events'"
    ).fetchone())
    for strategy_id, _ in strategies:
        # No ledger means no performance-grade evidence. Keep exploration
        # neutral rather than reallocating virtual capital from legacy PnL.
        count, pnl, notional = con.execute("""
            SELECT count(*),coalesce(sum(net_pnl),0),coalesce(sum(notional),0)
            FROM paper_agent_positions p
            WHERE p.strategy_id=? AND p.status='CLOSED' AND p.evidence_eligible=1
              AND COALESCE(p.funding_source,'')!='PENDING_RECONCILIATION'
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
        """, (strategy_id,)).fetchone() if has_execution_ledger else (0, 0.0, 0.0)
        return_pct = 100.0 * float(pnl) / float(notional) if notional else 0.0
        # Until a sleeve has enough closed trades, equal exploration beats noise.
        score = max(-3.0, min(3.0, return_pct / 2.0)) if count >= MIN_CLOSED_FOR_REALLOCATION else 0.0
        scores.append((strategy_id, int(count), float(pnl), return_pct, score))
    raw = [math.exp(item[4]) for item in scores]
    weights = [value / sum(raw) for value in raw]
    if len(weights) > 1:
        weights = [max(MIN_WEIGHT, min(MAX_WEIGHT, value)) for value in weights]
        weights = [value / sum(weights) for value in weights]
    evidence = {"basis": "VALIDATED_EXECUTABLE_ROUND_TRIP_FINAL_NET_AFTER_ALL_COSTS",
                "minimum_closed_before_reallocation": MIN_CLOSED_FOR_REALLOCATION,
                "single_trade_reallocation": False, "real_orders": False}
    for (strategy_id, count, pnl, return_pct, _), weight in zip(scores, weights):
        con.execute("""
            UPDATE paper_agent_strategy_allocations SET updated_at=?,allocation_weight=?,
              closed_trades=?,net_pnl=?,net_return_pct=? WHERE strategy_id=?
        """, (_iso(now), weight, count, pnl, return_pct, strategy_id))
    con.execute("""
        INSERT INTO paper_agent_strategy_allocation_history(
          created_at,allocator_version,weights_json,evidence_json,real_order_allowed
        ) VALUES(?,?,?,?,0)
    """, (_iso(now), VERSION,
          json.dumps(dict(zip((x[0] for x in scores), weights)), sort_keys=True),
          json.dumps(evidence, sort_keys=True)))
    con.commit()
    return {item[0]: weight for item, weight in zip(scores, weights)}


def matching_strategies(con, direction):
    rows = con.execute("""
        SELECT strategy_id,rule_json,allocation_weight
        FROM paper_agent_strategy_allocations
        WHERE status IN ('ACTIVE','EXPLORING') ORDER BY strategy_id
    """).fetchall()
    output = []
    for strategy_id, rule_json, weight in rows:
        rule = json.loads(rule_json)
        if strategy_id == BASELINE or rule.get("direction") == direction:
            output.append((strategy_id, float(weight)))
    return output
