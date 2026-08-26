"""Daily, full-cohort collector for OKX's public copy-trader leaderboard."""
from __future__ import annotations

import json
import hashlib
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from database.runtime_store import connect
from paper_trading import external_trader_learning
from paper_trading import continuous_improvement


ENDPOINT = "https://www.okx.com/priapi/v5/ecotrade/public/follow-rank"
SOURCE_URL = "https://www.okx.com/en-us/copy-trading"
PAGE_SIZE = 6  # OKX's public web client contract.
MAX_PAGES = 100
REQUEST_DELAY_SECONDS = 0.5
PROFILE_DELAY_SECONDS = 0.25
MAX_HISTORY_PROFILES_PER_RUN = 30
CURRENT_ENDPOINT = (
    "https://www.okx.com/priapi/v5/ecotrade/public/community/user/position-current"
)
HISTORY_ENDPOINT = (
    "https://www.okx.com/priapi/v5/ecotrade/public/community/user/position-history"
)


class PublicCopyTraderDataError(RuntimeError):
    pass


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
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
    if payload.get("code") != "0" or not isinstance(payload.get("data"), list):
        raise PublicCopyTraderDataError("OKX_PROFILE_RESPONSE_INVALID")
    return payload["data"]


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


def collect(database="crypto_data.db", now=None):
    now = now or datetime.now(timezone.utc)
    observed_at = now.astimezone(timezone.utc).isoformat()
    cohort = fetch_full_cohort()  # Fetch and validate fully before any DB write.
    con = connect(database, timeout=30)
    try:
        external_trader_learning.ensure_tables(con)
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
            payload = {
                "exchange": "OKX", "source_url": SOURCE_URL,
                "source_trader_id": str(row["uniqueName"]),
                "sampling_frame": "FULL_LEADERBOARD",
                "leaderboard_scope": "OKX_SWAP_OVERALL_ALL_TIME",
                "leaderboard_rank": rank,
                "leaderboard_size": cohort["total"],
                "metrics": metrics,
            }
            external_trader_learning.ingest_snapshot(
                con, payload, observed_at=observed_at, commit=False, ensure=False
            )
        con.commit()
        profile_errors = []
        open_count = 0
        history_count = 0
        ranks = {str(row["uniqueName"]): rank for rank, row in enumerate(cohort["rows"], 1)}
        for trader, rank in ranks.items():
            time.sleep(PROFILE_DELAY_SECONDS)
            try:
                groups = _fetch_profile(CURRENT_ENDPOINT, trader)
                for group in groups:
                    for row in group.get("posData") or []:
                        external_trader_learning.ingest_trade(
                            con, _current_payload(trader, row, rank, cohort["total"]),
                            first_observed_at=observed_at,
                        )
                        open_count += 1
            except Exception as exc:
                profile_errors.append({"trader": trader, "kind": "CURRENT", "error": type(exc).__name__})

        backfilled = {
            row[0] for row in con.execute(
                "SELECT DISTINCT source_trader_id FROM external_trader_trade_observations "
                "WHERE status='CLOSED'"
            )
        }
        candidates = [(trader, rank) for trader, rank in ranks.items() if trader not in backfilled]
        for trader, rank in candidates[:MAX_HISTORY_PROFILES_PER_RUN]:
            time.sleep(PROFILE_DELAY_SECONDS)
            try:
                for row in _fetch_profile(HISTORY_ENDPOINT, trader):
                    external_trader_learning.ingest_trade(
                        con, _history_payload(trader, row, rank, cohort["total"]),
                        first_observed_at=observed_at,
                    )
                    history_count += 1
            except Exception as exc:
                profile_errors.append({"trader": trader, "kind": "HISTORY", "error": type(exc).__name__})
        review = external_trader_learning.run_review(con, now)
        improvement = continuous_improvement.run(con, now)
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return {
        "status": "SUCCESS", "exchange": "OKX", "observed_at": observed_at,
        "data_version": cohort["data_version"], "traders": cohort["total"],
        "open_observations": open_count, "history_observations": history_count,
        "profile_errors": profile_errors,
        "review": review,
        "continuous_improvement": improvement,
    }


if __name__ == "__main__":
    print(json.dumps(collect(), ensure_ascii=False, sort_keys=True))
