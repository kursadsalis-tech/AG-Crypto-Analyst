"""Drain the durable SQLite-to-Supabase outbox outside the forecast worker."""
from __future__ import annotations

import argparse
import json
import os

from database import runtime_store

PRIORITY_TABLES = (
    "cloud_runtime_attestations",
    "paper_agent_daily_reports",
    "paper_agent_portfolio",
    "paper_agent_positions",
    "paper_agent_trade_learning_reviews",
    "paper_agent_trade_learning_hypotheses",
    "external_trader_learning_reviews",
    "external_trader_source_snapshots",
    "external_trader_trade_observations",
    "external_trader_consensus_snapshots",
    "paper_agent_research_candidates",
    "paper_agent_forward_evaluations",
    "paper_agent_strategy_allocations",
    "paper_agent_strategy_allocation_history",
)
CRITICAL_EVIDENCE_TABLES = frozenset(PRIORITY_TABLES[:4])
PRIORITY_BATCH_SIZE_ENV = "AGCA_REMOTE_PRIORITY_BATCH_SIZE"


def drain(database: str = "crypto_data.db"):
    con = runtime_store.connect(database, timeout=30)
    try:
        tables = set(con._remote_dirty_tables)
        if not tables:
            return runtime_store.MirrorResult()
        ordered = [table for table in PRIORITY_TABLES if table in tables]
        ordered.extend(sorted(tables - set(ordered)))
        attempted = set()
        succeeded = set()
        error_type = None
        for table in ordered:
            batch_env = runtime_store.REMOTE_BATCH_SIZE_ENV
            previous_batch_size = os.environ.get(batch_env)
            if table in CRITICAL_EVIDENCE_TABLES:
                os.environ[batch_env] = os.getenv(PRIORITY_BATCH_SIZE_ENV, "1000")
            try:
                result = runtime_store._mirror_after_local_commit(con, {table})
            finally:
                if previous_batch_size is None:
                    os.environ.pop(batch_env, None)
                else:
                    os.environ[batch_env] = previous_batch_size
            attempted.update(result.attempted)
            succeeded.update(result.succeeded)
            if result.error_type:
                error_type = result.error_type
                if result.error_type != "backlog_pending":
                    break
        return runtime_store.MirrorResult(
            attempted=frozenset(attempted),
            succeeded=frozenset(succeeded),
            error_type=error_type,
        )
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", default="crypto_data.db")
    args = parser.parse_args()
    result = drain(args.database)
    print(json.dumps({
        "attempted": sorted(result.attempted),
        "succeeded": sorted(result.succeeded),
        "error_type": result.error_type,
    }, sort_keys=True))
    return result


if __name__ == "__main__":
    main()
