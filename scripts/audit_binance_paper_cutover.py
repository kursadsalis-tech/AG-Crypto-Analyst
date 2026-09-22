#!/usr/bin/env python3
"""Read-only VM audit before any AGCA Binance-native PAPER cutover.

No imports from runtime execution modules, database creation, orders, external
requests, position cancellation, file writes or user credentials in output.
The local SQLite source of truth cannot be replaced by a Supabase mirror.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time


DB_PATH = "/var/lib/agca/crypto_data.db"
METADATA_PATH = "/var/lib/agca/exchange-info-usdm.json"
HEALTH_PATHS = {
    "paper": "/var/lib/agca/paper-realtime-health.json",
    "behavior": "/var/lib/agca/market-behavior-health.json",
}
ACTIVE_STATUSES = ("OPEN", "PENDING_ENTRY", "PENDING")


def _safe_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _table(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def _age_seconds(value, now_ms):
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return None
        return round(now_ms / 1000 - timestamp.timestamp(), 2)
    except (TypeError, ValueError, OverflowError):
        return None


def audit(*, db_path=DB_PATH, metadata_path=METADATA_PATH,
          health_paths=HEALTH_PATHS, now_ms=None):
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    metadata = _safe_json(metadata_path)
    payload = metadata.get("payload") if isinstance(metadata.get("payload"), dict) else {}
    contracts = {
        row["symbol"] for row in payload.get("symbols", [])
        if isinstance(row, dict) and isinstance(row.get("symbol"), str)
        and row.get("status") == "TRADING"
        and row.get("contractType") == "PERPETUAL"
        and row.get("quoteAsset") == "USDT"
        and row.get("marginAsset") == "USDT"
    }
    result = {
        "mode": "LOCAL_READ_ONLY_NO_POSITION_WRITES",
        "metadata": {
            "cached_market": metadata.get("market"),
            "age_seconds": _age_seconds(metadata.get("fetched_at"), now_ms),
            "eligible_native_contract_count": len(contracts),
            "pepe_native": "1000PEPEUSDT" in contracts,
            "pepe_alias": "PEPEUSDT" in contracts,
        },
        "health": {},
        "positions": {"active_rows": [], "active_count": None, "status_counts": {}},
        "pending_funding": {"count": None},
        "risk": {"policy": None, "asset_rows": []},
        "behavior": {"last_eligible_minute_age_seconds": None},
    }
    for key, path in health_paths.items():
        health = _safe_json(path)
        result["health"][key] = {
            "available": bool(health),
            "status": health.get("status"),
            "error": health.get("error"),
            "completed_at": health.get("completed_at"),
            "stale_symbols": health.get("stale_symbols", []),
            "eligible_row_count": health.get("eligible_row_count"),
        }

    # Immutable SQLite read-only URI: query_only protects even against
    # accidental writes during future maintenance of this audit script.
    with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.execute("PRAGMA query_only=ON")
        if _table(con, "paper_agent_positions"):
            result["positions"]["status_counts"] = dict(con.execute("""
                SELECT status, COUNT(*) FROM paper_agent_positions
                GROUP BY status ORDER BY status
            """).fetchall())
            rows = con.execute("""
                SELECT position_id,symbol,market_type,direction,status
                FROM paper_agent_positions
                WHERE status IN ('OPEN','PENDING_ENTRY','PENDING')
                ORDER BY position_id
            """).fetchall()
            result["positions"]["active_count"] = len(rows)
            result["positions"]["active_rows"] = [{
                "position_id": int(id_), "symbol": symbol, "market_type": market,
                "direction": direction, "status": status,
                "valid_native_usdm_contract": symbol in contracts,
            } for id_, symbol, market, direction, status in rows]
        if _table(con, "paper_funding_reconciliation_segments"):
            result["pending_funding"]["count"] = con.execute("""
                SELECT COUNT(*) FROM paper_funding_reconciliation_segments
                WHERE status='PENDING'
            """).fetchone()[0]
        if _table(con, "risk_policy_snapshots"):
            row = con.execute("""
                SELECT id,created_at,market_state,new_positions_allowed,
                       risk_per_trade_pct,coverage_count,coverage_total
                FROM risk_policy_snapshots ORDER BY id DESC LIMIT 1
            """).fetchone()
            if row:
                id_, created, state, allowed, trade_pct, covered, total = row
                result["risk"]["policy"] = {
                    "id": id_, "market_state": state, "new_positions_allowed": bool(allowed),
                    "risk_per_trade_pct": trade_pct, "coverage_count": covered,
                    "coverage_total": total,
                    "age_seconds": _age_seconds(created, now_ms),
                }
                if _table(con, "asset_risk_snapshots"):
                    rows = con.execute("""
                        SELECT symbol,as_of,new_position_allowed,max_position_pct
                        FROM asset_risk_snapshots WHERE policy_snapshot_id=?
                          AND symbol IN ('BTCUSDT','ETHUSDT','1000PEPEUSDT','PEPEUSDT')
                        ORDER BY symbol
                    """, (id_,)).fetchall()
                    result["risk"]["asset_rows"] = [{
                        "symbol": symbol, "age_seconds": _age_seconds(as_of, now_ms),
                        "new_position_allowed": bool(allowed),
                        "max_position_pct": cap,
                    } for symbol, as_of, allowed, cap in rows]
        if _table(con, "market_behavior_minute_features"):
            latest = con.execute("""
                SELECT MAX(minute_end_timestamp_ms)
                FROM market_behavior_minute_features WHERE eligible_for_learning=1
            """).fetchone()[0]
            if latest is not None:
                result["behavior"]["last_eligible_minute_age_seconds"] = round(
                    (now_ms - int(latest)) / 1000, 2)
    return result


def main():
    print(json.dumps(audit(), sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
