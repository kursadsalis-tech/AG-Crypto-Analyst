"""Bounded, resumable collector for OKX's public copy-trader leaderboard."""
from __future__ import annotations

import json
import hashlib
import math
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from database.runtime_store import connect
from paper_trading import external_trader_learning
from paper_trading import external_trader_rationale


ENDPOINT = "https://www.okx.com/priapi/v5/ecotrade/public/follow-rank"
SOURCE_URL = "https://www.okx.com/en-us/copy-trading"
PAGE_SIZE = 6  # OKX's public web client contract.
MAX_PAGES = 100
REQUEST_DELAY_SECONDS = 0.5
PROFILE_WORKERS = 4
PROFILE_REQUEST_TIMEOUT_SECONDS = 8
MAX_CURRENT_PROFILES_PER_RUN = 6
MAX_HISTORY_PROFILES_PER_RUN = 1
MAX_RETROSPECTIVE_HISTORY_TRADES_PER_PROFILE = 20
CURRENT_ENDPOINT = (
    "https://www.okx.com/priapi/v5/ecotrade/public/community/user/position-current"
)
HISTORY_ENDPOINT = (
    "https://www.okx.com/priapi/v5/ecotrade/public/community/user/position-history"
)


class PublicCopyTraderDataError(RuntimeError):
    pass


def _progress(stage, started):
    print(json.dumps({
        "collector_stage": stage,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }, sort_keys=True), flush=True)


def _fetch_page(start):
    query = urllib.parse.urlencode({
        "size": PAGE_SIZE,
        "instNumLimit": 4,
        "type": "",
        "latestNum": "0",
        "start": start,
    })
    request = urllib.request.Request(
        f"{ENDPOINT}?{query}",
        headers={"User-Agent": "AGCA-Public-Research/1.0"},
    )
    last_error = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status != 200:
                    raise PublicCopyTraderDataError(f"HTTP_{response.status}")
                payload = json.load(response)
            if payload.get("code") != "0" or not isinstance(payload.get("data"), list):
                raise PublicCopyTraderDataError("OKX_RESPONSE_NOT_SUCCESS")
            if len(payload["data"]) != 1 or not isinstance(payload["data"][0], dict):
                raise PublicCopyTraderDataError("OKX_RESPONSE_SHAPE_CHANGED")
            return payload["data"][0]
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1.0)
    raise PublicCopyTraderDataError(type(last_error).__name__) from last_error


def _fetch_profile(endpoint, trader_id):
    query = urllib.parse.urlencode({"uniqueName": trader_id, "instType": "SWAP"})
    request = urllib.request.Request(
        f"{endpoint}?{query}", headers={"User-Agent": "AGCA-Public-Research/1.0"}
    )
    last_error = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(
                request, timeout=PROFILE_REQUEST_TIMEOUT_SECONDS
            ) as response:
                payload = json.load(response)
            if payload.get("code") != "0" or not isinstance(payload.get("data"), list):
                raise PublicCopyTraderDataError("OKX_PROFILE_RESPONSE_INVALID")
            return payload["data"]
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.5)
    raise PublicCopyTraderDataError(type(last_error).__name__) from last_error


def _fetch_profiles_bounded(endpoint, trader_ranks, workers=PROFILE_WORKERS):
    """Fetch profiles concurrently but keep all SQLite writes on the caller."""
    profiles = {}
    errors = []
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        futures = {
            pool.submit(_fetch_profile, endpoint, trader): (trader, rank)
            for trader, rank in trader_ranks
        }
        for future in as_completed(futures):
            trader, rank = futures[future]
            try:
                profiles[trader] = (rank, future.result())
            except Exception as exc:
                errors.append({
                    "trader": trader,
                    "error": type(exc).__name__,
                })
    return profiles, sorted(errors, key=lambda row: row["trader"])


def _number(value):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _stamp_ms(value):
    parsed = _number(value)
    if parsed is None:
        return None
    return datetime.fromtimestamp(parsed / 1000.0, tz=timezone.utc).isoformat()


def _symbol(value):
    return str(value or "").replace("-", "").replace("SWAP", "")


def _stable_trade_id(trader_id, row, direction):
    basis = f"{trader_id}|{row.get('instId')}|{row.get('cTime')}|{direction}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def _rotated(values, offset):
    if not values:
        return []
    start = offset % len(values)
    return values[start:] + values[:start]


def _history_profiles_for_run(con, ranks, now):
    """Prioritize causal closure checks without starving cohort backfill.

    A public position that AGCA previously observed open can become forward
    evidence only after a later history response discloses its close. Rotate
    that bounded priority set daily so more than one batch of open traders is
    eventually revisited. Any remaining capacity continues the one-time
    retrospective cohort backfill.
    """
    latest_open_traders = {
        str(row[0]) for row in con.execute("""
            SELECT DISTINCT o.source_trader_id
            FROM external_trader_trade_observations o
            WHERE o.exchange='OKX' AND o.status='OPEN'
              AND NOT EXISTS (
                SELECT 1 FROM external_trader_trade_observations closed
                WHERE closed.exchange=o.exchange
                  AND closed.source_trader_id=o.source_trader_id
                  AND closed.source_trade_id=o.source_trade_id
                  AND closed.status='CLOSED'
                  AND closed.observation_id>o.observation_id
              )
        """)
        if str(row[0]) in ranks
    }
    priority = sorted(latest_open_traders, key=lambda trader: ranks[trader])
    run_slot = int(now.timestamp() // (30 * 60))
    priority = _rotated(priority, run_slot * MAX_HISTORY_PROFILES_PER_RUN)
    backfilled = {
        str(row[0]) for row in con.execute("""
            SELECT DISTINCT source_trader_id
            FROM external_trader_trade_observations
            WHERE exchange='OKX' AND status='CLOSED'
        """)
    }
    new_backfill = [
        trader for trader in sorted(ranks, key=lambda value: ranks[value])
        if trader not in backfilled and trader not in latest_open_traders
    ]
    new_backfill = _rotated(new_backfill, run_slot // 2)
    pools = (new_backfill, priority) if run_slot % 2 == 0 else (priority, new_backfill)
    selected = []
    for pool in pools:
        for trader in pool:
            if trader in selected:
                continue
            selected.append(trader)
            if len(selected) == MAX_HISTORY_PROFILES_PER_RUN:
                break
        if len(selected) == MAX_HISTORY_PROFILES_PER_RUN:
            break
    return [(trader, ranks[trader]) for trader in selected]


def _ensure_queue_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS okx_copy_trader_profile_queue (
            exchange TEXT PRIMARY KEY,
            cohort_observed_at TEXT NOT NULL,
            next_rank_index INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)


def _current_profiles_for_run(con, ranks, cohort_observed_at, now):
    """Return one persisted bounded slice and its next cursor."""
    _ensure_queue_table(con)
    ordered = sorted(ranks, key=lambda trader: ranks[trader])
    if not ordered:
        return [], 0
    state = con.execute("""
        SELECT cohort_observed_at,next_rank_index
        FROM okx_copy_trader_profile_queue WHERE exchange='OKX'
    """).fetchone()
    start = int(state[1]) % len(ordered) if state else 0
    indexes = list(range(
        start, min(start + MAX_CURRENT_PROFILES_PER_RUN, len(ordered))
    ))
    batch = [(ordered[index], ranks[ordered[index]]) for index in indexes]
    return batch, (start + len(batch)) % len(ordered)


def _advance_current_queue(con, cohort_observed_at, next_index, now):
    con.execute("""
        INSERT INTO okx_copy_trader_profile_queue(
            exchange,cohort_observed_at,next_rank_index,updated_at
        ) VALUES('OKX',?,?,?)
        ON CONFLICT(exchange) DO UPDATE SET
            cohort_observed_at=excluded.cohort_observed_at,
            next_rank_index=excluded.next_rank_index,
            updated_at=excluded.updated_at
    """, (cohort_observed_at, int(next_index), now.isoformat()))


def _cached_cohort(con):
    observed = con.execute("""
        SELECT max(observed_at) FROM external_trader_source_snapshots
        WHERE exchange='OKX' AND sampling_frame='FULL_LEADERBOARD'
    """).fetchone()[0]
    if not observed:
        return None
    rows = con.execute("""
        SELECT source_trader_id,leaderboard_rank,leaderboard_size,metrics_json
        FROM external_trader_source_snapshots
        WHERE exchange='OKX' AND sampling_frame='FULL_LEADERBOARD'
          AND observed_at=? AND research_eligible=1
        ORDER BY leaderboard_rank
    """, (observed,)).fetchall()
    if not rows or len(rows) != int(rows[0][2]):
        return None
    metrics = json.loads(rows[0][3])
    return {
        "data_version": str(metrics.get("data_version") or f"cached:{observed}"),
        "total": len(rows), "observed_at": observed,
        "rows": [{"uniqueName": row[0]} for row in rows],
    }


def _history_payload(trader, row, rank, total):
    direction = str(row.get("posSide", "")).upper()
    entry_price = _number(row.get("openAvgPx"))
    quantity = _number(row.get("openMaxAmount"))
    contract_value = _number(row.get("ctVal"))
    notional = None
    if row.get("quoteCcy") == "USDT" and None not in (entry_price, quantity, contract_value):
        notional = entry_price * quantity * contract_value
    return {
        "exchange": "OKX", "source_url": SOURCE_URL,
        "source_trader_id": trader, "source_trade_id": _stable_trade_id(trader, row, direction),
        "sampling_frame": "FULL_LEADERBOARD", "leaderboard_rank": rank,
        "leaderboard_size": total, "symbol": _symbol(row.get("instId")),
        "direction": direction, "status": "CLOSED",
        "entry_time": _stamp_ms(row.get("cTime")), "exit_time": _stamp_ms(row.get("uTime")),
        "entry_price": entry_price, "exit_price": _number(row.get("closeAvgPx")),
        "notional": notional, "leverage": _number(row.get("lever")),
        "reported_pnl": _number(row.get("realizedPnl")),
        "reported_roi_pct": (
            100.0 * _number(row.get("pnlRatio"))
            if _number(row.get("pnlRatio")) is not None else None
        ),
        "fees": (
            abs(_number(row.get("fee"))) if _number(row.get("fee")) is not None else None
        ),
        "funding": _number(row.get("fundingFee")), "source_record": row,
    }


def _bounded_history_payloads(con, trader, rows, rank, total):
    """Keep every causally known closure; bound retrospective-only backfill."""
    known_open = {
        str(row[0]) for row in con.execute("""
            SELECT o.source_trade_id
            FROM external_trader_trade_observations o
            WHERE o.exchange='OKX' AND o.source_trader_id=? AND o.status='OPEN'
              AND NOT EXISTS (
                SELECT 1 FROM external_trader_trade_observations closed
                WHERE closed.exchange=o.exchange
                  AND closed.source_trader_id=o.source_trader_id
                  AND closed.source_trade_id=o.source_trade_id
                  AND closed.status='CLOSED'
                  AND closed.observation_id>o.observation_id
              )
        """, (trader,))
    }
    causal = []
    retrospective = []
    for row in rows:
        payload = _history_payload(trader, row, rank, total)
        if payload["source_trade_id"] in known_open:
            causal.append(payload)
        elif len(retrospective) < MAX_RETROSPECTIVE_HISTORY_TRADES_PER_PROFILE:
            retrospective.append(payload)
    return causal + retrospective


def _current_payload(trader, row, rank, total):
    direction = str(row.get("posSide", "")).upper()
    if direction == "NET":
        direction = "LONG" if (_number(row.get("pos")) or 0) >= 0 else "SHORT"
    return {
        "exchange": "OKX", "source_url": SOURCE_URL,
        "source_trader_id": trader, "source_trade_id": _stable_trade_id(trader, row, direction),
        "sampling_frame": "FULL_LEADERBOARD", "leaderboard_rank": rank,
        "leaderboard_size": total, "symbol": _symbol(row.get("instId")),
        "direction": direction, "status": "OPEN", "entry_time": _stamp_ms(row.get("cTime")),
        "entry_price": _number(row.get("avgPx")), "notional": _number(row.get("notionalUsd")),
        "leverage": _number(row.get("lever")), "reported_pnl": _number(row.get("realizedPnl")),
        "fees": (
            abs(_number(row.get("fee"))) if _number(row.get("fee")) is not None else None
        ),
        "funding": _number(row.get("fundingFee")), "source_record": row,
    }


def fetch_full_cohort():
    first = _fetch_page(1)
    pages = int(first.get("pages", 0))
    total = int(first.get("total", 0))
    version = str(first.get("dataVersion", ""))
    if not version or pages < 1 or pages > MAX_PAGES or total < 1:
        raise PublicCopyTraderDataError("OKX_COHORT_HEADER_INVALID")
    rows = list(first.get("ranks") or [])
    for page in range(2, pages + 1):
        time.sleep(REQUEST_DELAY_SECONDS)
        item = _fetch_page(page)
        if str(item.get("dataVersion", "")) != version:
            raise PublicCopyTraderDataError("OKX_DATA_VERSION_CHANGED_DURING_FETCH")
        if int(item.get("total", 0)) != total or int(item.get("pages", 0)) != pages:
            raise PublicCopyTraderDataError("OKX_COHORT_SIZE_CHANGED_DURING_FETCH")
        rows.extend(item.get("ranks") or [])
    identities = [str(row.get("uniqueName", "")) for row in rows]
    if len(rows) != total or any(not value for value in identities):
        raise PublicCopyTraderDataError("OKX_COHORT_INCOMPLETE")
    if len(set(identities)) != total:
        raise PublicCopyTraderDataError("OKX_COHORT_DUPLICATE_TRADER")
    return {"data_version": version, "total": total, "rows": rows}


def collect(database=None, now=None):
    database = database or os.getenv("AGCA_DATABASE_PATH", "crypto_data.db")
    started = time.monotonic()
    now = now or datetime.now(timezone.utc)
    observed_at = now.astimezone(timezone.utc).isoformat()
    con = connect(database, timeout=30)
    try:
        external_trader_learning.ensure_tables(con)
        _ensure_queue_table(con)
        cached = _cached_cohort(con)
        refresh_due = not cached or datetime.fromisoformat(
            cached["observed_at"].replace("Z", "+00:00")
        ).date() < now.astimezone(timezone.utc).date()
        cohort_refresh_error = None
        cohort_source = "CACHED_VALIDATED_FULL_COHORT"
        if refresh_due:
            try:
                cohort = fetch_full_cohort()
                con.execute("BEGIN")
                for rank, row in enumerate(cohort["rows"], start=1):
                    metrics = {
                        "data_version": cohort["data_version"],
                        "pnl": row.get("pnl"), "yield_ratio": row.get("yieldRatio"),
                        "win_ratio": row.get("winRatio"), "aum": row.get("aum"),
                        "follower_count": row.get("followerNum"),
                        "historical_follower_count": row.get("historyFollowerNum"),
                        "follow_pnl": row.get("followPnl"),
                        "leading_days": row.get("initialDay"),
                        "reported_leverage": row.get("lever"),
                        "api_trader": row.get("apiTrader"),
                        "instruments": row.get("instruments") or [],
                        "rates": row.get("rates") or [],
                    }
                    external_trader_learning.ingest_snapshot(con, {
                        "exchange": "OKX", "source_url": SOURCE_URL,
                        "source_trader_id": str(row["uniqueName"]),
                        "sampling_frame": "FULL_LEADERBOARD",
                        "leaderboard_scope": "OKX_SWAP_OVERALL_ALL_TIME",
                        "leaderboard_rank": rank,
                        "leaderboard_size": cohort["total"], "metrics": metrics,
                    }, observed_at=observed_at, commit=False, ensure=False)
                con.commit()
                cohort["observed_at"] = observed_at
                cohort_source = "LIVE_VALIDATED_FULL_COHORT"
            except Exception as exc:
                con.rollback()
                if not cached:
                    raise
                cohort = cached
                cohort_refresh_error = type(exc).__name__
                cohort_source = "STALE_VALIDATED_FULL_COHORT"
        else:
            cohort = cached
        _progress("COHORT_READY", started)
        profile_errors = []
        open_count = 0
        history_count = 0
        ranks = {str(row["uniqueName"]): rank for rank, row in enumerate(cohort["rows"], 1)}
        current_batch, next_index = _current_profiles_for_run(
            con, ranks, cohort["observed_at"], now
        )
        current_profiles, current_errors = _fetch_profiles_bounded(
            CURRENT_ENDPOINT, current_batch
        )
        _progress("CURRENT_FETCHED", started)
        profile_errors.extend({**row, "kind": "CURRENT"} for row in current_errors)
        rationale_contexts = 0
        for trader, (rank, groups) in current_profiles.items():
            for group in groups:
                for row in group.get("posData") or []:
                    payload = _current_payload(trader, row, rank, cohort["total"])
                    stored = external_trader_learning.ingest_trade(
                        con, payload, first_observed_at=observed_at,
                        commit=False, ensure=False,
                    )
                    if stored["research_eligible"]:
                        captured = external_trader_rationale.capture_open_trade_context(
                            con, payload, observed_at
                        )
                        rationale_contexts += int(captured["stored"])
                    open_count += 1
        _progress("CURRENT_STORED", started)

        history_profiles = _history_profiles_for_run(con, ranks, now)
        history_payloads, history_errors = _fetch_profiles_bounded(
            HISTORY_ENDPOINT, history_profiles
        )
        _progress("HISTORY_FETCHED", started)
        profile_errors.extend({**row, "kind": "HISTORY"} for row in history_errors)
        for trader, (rank, rows) in history_payloads.items():
            for payload in _bounded_history_payloads(
                con, trader, rows, rank, cohort["total"]
            ):
                external_trader_learning.ingest_trade(
                    con, payload,
                    first_observed_at=observed_at, commit=False, ensure=False,
                )
                history_count += 1
        _progress("HISTORY_STORED", started)
        _advance_current_queue(con, cohort["observed_at"], next_index, now)
        con.commit()
        _progress("COMMITTED", started)
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return {
        "status": "SUCCESS", "exchange": "OKX", "observed_at": observed_at,
        "data_version": cohort["data_version"], "traders": cohort["total"],
        "cohort_source": cohort_source,
        "cohort_refresh_error": cohort_refresh_error,
        "current_profiles_attempted": len(current_batch),
        "current_queue_next_index": next_index,
        "open_observations": open_count, "history_observations": history_count,
        "profile_errors": profile_errors,
        "rationale_contexts_captured": rationale_contexts,
        "rationale_review": {
            "status": "DEFERRED_TO_HOURLY_TRADE_LEARNING",
        },
        "review": {
            "status": "DEFERRED_TO_HOURLY_TRADE_LEARNING",
        },
        "continuous_improvement": {
            "status": "DEFERRED_TO_HOURLY_TRADE_LEARNING",
            "duplicate_evaluation_avoided": True,
        },
    }


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False, sort_keys=True))
