"""Autonomous, evidence-gated research loop for the PAPER agent.

The loop may discover and measure challenger ideas.  It has deliberately no
execution or policy-writing capability: promotion is an orchestrator decision.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone


VERSION = "PAPER_CONTINUOUS_IMPROVEMENT_V1"
MIN_RETROSPECTIVE_TRADES = 50
MIN_RETROSPECTIVE_TRADERS = 10
MIN_FORWARD_TRADES = 30
MIN_FORWARD_TRADERS = 10
MIN_CONSENSUS_TRADERS = 5
MIN_POSITIVE_FORWARD_NET_PNL = 0.0
MIN_FORWARD_WIN_RATE_PCT = 50.0


def _iso(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def ensure_tables(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS paper_agent_research_candidates (
            candidate_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            learning_version TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            pattern_key TEXT NOT NULL,
            rule_json TEXT NOT NULL,
            discovery_evidence_json TEXT NOT NULL,
            forward_start_at TEXT NOT NULL,
            status TEXT NOT NULL,
            policy_mutation_allowed INTEGER NOT NULL DEFAULT 0,
            promotion_allowed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(learning_version,source_kind,pattern_key)
        );

        CREATE TABLE IF NOT EXISTS paper_agent_forward_evaluations (
            evaluation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            candidate_id INTEGER NOT NULL,
            forward_trade_count INTEGER NOT NULL,
            forward_trader_count INTEGER NOT NULL,
            net_pnl REAL NOT NULL,
            win_rate_pct REAL NOT NULL,
            status TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            promotion_allowed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(candidate_id,forward_trade_count,forward_trader_count)
        );

        CREATE TABLE IF NOT EXISTS external_trader_consensus_snapshots (
            consensus_id INTEGER PRIMARY KEY AUTOINCREMENT,
            observed_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            long_traders INTEGER NOT NULL,
            short_traders INTEGER NOT NULL,
            total_traders INTEGER NOT NULL,
            long_share REAL NOT NULL,
            status TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            execution_allowed INTEGER NOT NULL DEFAULT 0,
            UNIQUE(observed_at,symbol)
        );
    """)


def _latest_eligible_open(con):
    rows = con.execute("""
        SELECT o.source_trader_id,o.symbol,o.direction,o.source_trade_id
        FROM external_trader_trade_observations o
        JOIN (
          SELECT exchange,source_trader_id,source_trade_id,max(observation_id) AS latest_id
          FROM external_trader_trade_observations
          GROUP BY exchange,source_trader_id,source_trade_id
        ) x ON x.latest_id=o.observation_id
        WHERE o.research_eligible=1 AND o.status='OPEN'
    """).fetchall()
    # One trader, one vote per symbol. Conflicting positions cancel to neutral.
    votes = defaultdict(lambda: defaultdict(set))
    for trader, symbol, direction, _ in rows:
        votes[str(symbol)][str(trader)].add(str(direction))
    return votes


def snapshot_consensus(con, now):
    observed_at = _iso(now)
    output = []
    for symbol, trader_votes in sorted(_latest_eligible_open(con).items()):
        long_count = sum(directions == {"LONG"} for directions in trader_votes.values())
        short_count = sum(directions == {"SHORT"} for directions in trader_votes.values())
        total = long_count + short_count
        if not total:
            continue
        share = long_count / total
        status = "FORWARD_OBSERVATION"
        if total < MIN_CONSENSUS_TRADERS:
            status = "INSUFFICIENT_TRADER_DIVERSITY"
        evidence = {
            "basis": "LATEST_ELIGIBLE_OPEN_ONE_VOTE_PER_TRADER",
            "conflicted_traders_excluded": sum(len(v) != 1 for v in trader_votes.values()),
            "real_order_authority": False,
        }
        con.execute("""
            INSERT OR IGNORE INTO external_trader_consensus_snapshots(
              observed_at,symbol,long_traders,short_traders,total_traders,long_share,
              status,evidence_json,execution_allowed
            ) VALUES(?,?,?,?,?,?,?,?,0)
        """, (observed_at, symbol, long_count, short_count, total, share, status,
              json.dumps(evidence, sort_keys=True)))
        output.append({"symbol": symbol, "long": long_count, "short": short_count,
                       "traders": total, "status": status})
    return output


def discover_candidates(con, now):
    rows = con.execute("""
        SELECT direction,count(DISTINCT source_trader_id),
               count(DISTINCT source_trader_id || ':' || source_trade_id),
               sum(reported_pnl),
               100.0*avg(CASE WHEN reported_pnl>0 THEN 1.0 ELSE 0.0 END)
        FROM external_trader_trade_observations
        WHERE research_eligible=1 AND status='CLOSED' AND retrospective_only=1
        GROUP BY direction
    """).fetchall()
    created = []
    for direction, traders, trades, pnl, win_rate in rows:
        if traders < MIN_RETROSPECTIVE_TRADERS or trades < MIN_RETROSPECTIVE_TRADES:
            continue
        pattern = f"EXTERNAL_DIRECTION:{direction}"
        evidence = {"claim": "RETROSPECTIVE_HYPOTHESIS_ONLY", "traders": traders,
                    "trades": trades, "reported_net_pnl": pnl or 0.0,
                    "win_rate_pct": win_rate or 0.0, "trader_balanced_gate": True}
        con.execute("""
            INSERT OR IGNORE INTO paper_agent_research_candidates(
              created_at,learning_version,source_kind,pattern_key,rule_json,
              discovery_evidence_json,forward_start_at,status,
              policy_mutation_allowed,promotion_allowed
            ) VALUES(?,?,?,?,?,?,?,'FORWARD_COLLECTING',0,0)
        """, (_iso(now), VERSION, "EXTERNAL_RETROSPECTIVE", pattern,
              json.dumps({"direction": direction}, sort_keys=True),
              json.dumps(evidence, sort_keys=True), _iso(now)))
        created.append(pattern)
    return created


def evaluate_forward(con, now):
    results = []
    candidates = con.execute("""
        SELECT candidate_id,forward_start_at,rule_json
        FROM paper_agent_research_candidates
        WHERE learning_version=? AND status IN ('FORWARD_COLLECTING','READY_FOR_ORCHESTRATOR_REVIEW')
    """, (VERSION,)).fetchall()
    for candidate_id, start_at, rule_json in candidates:
        direction = json.loads(rule_json)["direction"]
        rows = con.execute("""
            SELECT source_trader_id,source_trade_id,reported_pnl
            FROM external_trader_trade_observations
            WHERE research_eligible=1 AND status='CLOSED' AND retrospective_only=0
              AND direction=? AND first_observed_at<=? AND exit_time>?
            GROUP BY source_trader_id,source_trade_id
        """, (direction, start_at, start_at)).fetchall()
        traders = len({row[0] for row in rows})
        trades = len(rows)
        pnl = sum(float(row[2]) for row in rows)
        wins = 100.0 * sum(float(row[2]) > 0 for row in rows) / trades if trades else 0.0
        status = "FORWARD_COLLECTING"
        enough_forward = (
            trades >= MIN_FORWARD_TRADES and traders >= MIN_FORWARD_TRADERS
        )
        economically_positive = (
            pnl > MIN_POSITIVE_FORWARD_NET_PNL
            and wins > MIN_FORWARD_WIN_RATE_PCT
        )
        if enough_forward and economically_positive:
            status = "READY_FOR_ORCHESTRATOR_REVIEW"
        elif enough_forward:
            status = "REJECTED_FORWARD_ECONOMICS"
        evidence = {
            "basis": "KNOWN_OPEN_BEFORE_CANDIDATE_THEN_CLOSED",
            "lookahead_allowed": False, "automatic_promotion": False,
            "economic_gate": "POSITIVE_FORWARD_NET_PNL_AND_WIN_RATE_ABOVE_50",
            "net_pnl_positive": pnl > MIN_POSITIVE_FORWARD_NET_PNL,
            "win_rate_above_50": wins > MIN_FORWARD_WIN_RATE_PCT,
        }
        con.execute("""
            INSERT OR IGNORE INTO paper_agent_forward_evaluations(
              created_at,candidate_id,forward_trade_count,forward_trader_count,
              net_pnl,win_rate_pct,status,evidence_json,promotion_allowed
            ) VALUES(?,?,?,?,?,?,?,?,0)
        """, (_iso(now), candidate_id, trades, traders, pnl, wins, status,
              json.dumps(evidence, sort_keys=True)))
        con.execute("UPDATE paper_agent_research_candidates SET status=? WHERE candidate_id=?",
                    (status, candidate_id))
        results.append({"candidate_id": candidate_id, "trades": trades,
                        "traders": traders, "net_pnl": pnl, "status": status})
    return results


def run(con, now=None):
    now = now or datetime.now(timezone.utc)
    ensure_tables(con)
    consensus = snapshot_consensus(con, now)
    created = discover_candidates(con, now)
    evaluations = evaluate_forward(con, now)
    con.commit()
    return {"version": VERSION, "consensus_symbols": len(consensus),
            "candidates_seen": created, "evaluations": evaluations,
            "policy_mutation_allowed": False, "promotion_allowed": False,
            "real_orders_allowed": False}
