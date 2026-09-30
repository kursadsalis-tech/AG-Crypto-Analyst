#!/usr/bin/env python3
"""Read-only local preflight and bounded process acceptance; never deploys."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from paper_trading import behavior_decision_core as core
from paper_trading.exchange_metadata import ExchangeMetadataError, FROZEN_RULES_CONTRACT_VERSION, load_cache
from paper_trading.position_management import VERSION as POSITION_MANAGER_VERSION
from runtime.process_identity import PROCESS_SOURCE


class AcceptanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServiceContract:
    health_file: str
    statuses: tuple[str, ...]
    fresh_seconds: int
    versions: dict[str, str]


SERVICES = {
    "agca-paper-realtime-worker.service": ServiceContract(
        "paper-realtime-health.json", ("CONNECTED", "HEALTHY"), 30,
        {"health_contract_version": "AGCA_REALTIME_HEALTH_V2_ISOLATED_PATHS",
         "core_version": core.VERSION,
         "cache_contract_version": core.PATTERN_CACHE_CONTRACT_VERSION,
         "execution_rules_contract_version": FROZEN_RULES_CONTRACT_VERSION,
         "position_manager_version": POSITION_MANAGER_VERSION}),
    "agca-behavior-learning-worker.service": ServiceContract(
        "behavior-learning-health.json", ("HEALTHY",), 360,
        {"health_contract_version": "AGCA_BEHAVIOR_LEARNING_HEALTH_V1_BACKGROUND_CACHE",
         "producer_version": core.PATTERN_CACHE_PRODUCER_VERSION,
         "core_version": core.VERSION,
         "cache_contract_version": core.PATTERN_CACHE_CONTRACT_VERSION}),
    "agca-trade-learning-worker.service": ServiceContract(
        "trade-learning-health.json", ("HEALTHY",), 180,
        {"health_contract_version": "AGCA_TRADE_LEARNING_HEALTH_V2_CONTROLLED_CHALLENGER",
         "producer_version": "PAPER_TRADE_LEARNING_PRODUCER_V1",
         "core_version": "PAPER_TRADE_LEARNING_REVIEW_V3_CONTROLLED_CHALLENGER"}),
    "agca-technical-kline-worker.service": ServiceContract(
        "technical-kline-health.json", ("CONNECTED",), 60,
        {"health_contract_version": "AGCA_TECHNICAL_KLINE_HEALTH_V1_BINANCE_WS",
         "producer_version": "BINANCE_TECHNICAL_KLINE_PRODUCER_V1"}),
    "agca-market-behavior-observer.service": ServiceContract(
        "market-behavior-health.json", ("HEALTHY",), 180,
        {"health_contract_version": "AGCA_MARKET_BEHAVIOR_HEALTH_V2_BINANCE_CANONICAL",
         "producer_version": "BINANCE_MARKET_BEHAVIOR_PRODUCER_V1"}),
}


def utcnow():
    return datetime.now(timezone.utc)


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp missing")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone missing")
    return result.astimezone(timezone.utc)


def read_service_state(unit):
    result = subprocess.run(
        ["systemctl", "show", unit, "--property=MainPID,NRestarts,ActiveState,SubState"],
        check=False, capture_output=True, text=True, timeout=5,
    )
    if result.returncode:
        raise AcceptanceError(f"{unit}:SYSTEMCTL_SHOW_FAILED")
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    try:
        return {"pid": int(values["MainPID"]), "restarts": int(values["NRestarts"]),
                "active": values["ActiveState"], "sub": values["SubState"]}
    except (KeyError, ValueError) as exc:
        raise AcceptanceError(f"{unit}:INVALID_SYSTEMCTL_STATE") from exc


def read_health(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def health_failures(unit, health, state, expected_sha, now):
    spec = SERVICES[unit]
    failures = []
    if state.get("active") != "active" or state.get("sub") != "running" or state.get("pid", 0) <= 0:
        failures.append("PROCESS_NOT_RUNNING")
    source = health.get("process_source")
    source = source if isinstance(source, dict) else {}
    if source.get("git_sha") != expected_sha:
        failures.append("PROCESS_SHA_MISMATCH")
    if source.get("git_dirty") is not False:
        failures.append("PROCESS_SOURCE_NOT_CLEAN")
    if type(source.get("pid")) is not int or source["pid"] != state.get("pid"):
        failures.append("HEALTH_PID_MISMATCH")
    try:
        generated = timestamp(health.get("at"))
        process_started = timestamp(source.get("process_started_at"))
        age = (now - generated).total_seconds()
        if age < 0 or age > spec.fresh_seconds or generated < process_started:
            failures.append("HEALTH_STALE_OR_FUTURE")
    except (ValueError, TypeError):
        failures.append("HEALTH_TIMESTAMP_INVALID")
    if health.get("status") not in spec.statuses:
        detail = health.get("error_detail") or health.get("error") or health.get("status", "MISSING")
        failures.append(f"HEALTH_NOT_READY:{str(detail)[:400]}")
    for field, expected in spec.versions.items():
        actual = health.get(field)
        if actual != expected:
            # STARTING is published before workers finish loading their contracts.
            # Missing versions stay unready; an explicitly wrong version is fatal.
            pending = health.get("status") == "STARTING" and actual is None
            failures.append(f"{'VERSION_PENDING' if pending else 'VERSION_MISMATCH'}:{field}")
    if health.get("execution_mode") != "PAPER_ONLY":
        failures.append("PAPER_ONLY_MISSING")
    authority = "real_order_allowed" if unit == "agca-paper-realtime-worker.service" else "may_send_real_orders"
    if health.get(authority) is not False:
        failures.append("REAL_ORDER_AUTHORITY_NOT_FALSE")
    if unit == "agca-paper-realtime-worker.service":
        entry = health.get("ENTRY_PATH")
        manager = health.get("OPEN_POSITION_MANAGEMENT")
        if not isinstance(entry, dict) or entry.get("status") != "READY":
            failures.append("ENTRY_PATH_NOT_READY")
        if not isinstance(manager, dict) or manager.get("status") not in {"READY", "IDLE"}:
            failures.append("OPEN_POSITION_MANAGEMENT_NOT_READY")
    else:
        try:
            age = (now - timestamp(health.get("last_success_at"))).total_seconds()
            if age < 0 or age > spec.fresh_seconds:
                failures.append("PRODUCER_SUCCESS_STALE_OR_FUTURE")
        except (ValueError, TypeError):
            failures.append("PRODUCER_SUCCESS_MISSING")
    return failures


def metadata_preflight(health_dir, *, spot_path=None, usdm_path=None):
    paths = {
        "SPOT": spot_path or os.getenv("AGCA_SPOT_EXCHANGE_METADATA_CACHE") or Path(health_dir) / "exchange-info-spot.json",
        "USD_M_PERPETUAL": usdm_path or os.getenv("AGCA_EXCHANGE_METADATA_CACHE") or Path(health_dir) / "exchange-info-usdm.json",
    }
    routes = {}
    failures = []
    for market, path in paths.items():
        try:
            cache = load_cache(path, market, max_age_seconds=float(os.getenv("AGCA_EXCHANGE_METADATA_MAX_AGE_SECONDS", "7200")))
            routes[market] = {"status": "READY", "symbol_count": len(cache.symbols),
                              "fetched_at": cache.fetched_at, "payload_sha256": cache.payload_sha256}
        except (ExchangeMetadataError, OSError, ValueError) as exc:
            failures.append(f"{market}:{exc}")
    if failures:
        raise AcceptanceError("METADATA_PREFLIGHT_FAILED:" + ";".join(failures))
    return {"status": "ACCEPTED", "phase": "metadata", "routes": routes}


def cache_preflight(database_path, expected_sha, *, now=None):
    # mode=ro plus query_only: never bootstrap schemas, refresh cache or write history.
    try:
        uri = Path(database_path).resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1) as con:
            con.execute("PRAGMA query_only=ON")
            patterns, detail = core.load_cached_patterns(con, now or utcnow())
    except sqlite3.Error as exc:
        raise AcceptanceError(f"CACHE_PREFLIGHT_FAILED:{type(exc).__name__}") from exc
    if patterns is None or detail.get("status") != "READY":
        raise AcceptanceError("CACHE_PREFLIGHT_FAILED:" + str(detail.get("status")))
    source = detail.get("producer_source") or {}
    if detail.get("producer_git_sha") != expected_sha or source.get("git_sha") != expected_sha:
        raise AcceptanceError("CACHE_PREFLIGHT_FAILED:PRODUCER_SHA_MISMATCH")
    if source.get("git_dirty") is not False:
        raise AcceptanceError("CACHE_PREFLIGHT_FAILED:PRODUCER_SOURCE_NOT_CLEAN")
    return {**detail, "cache_status": detail["status"], "status": "ACCEPTED", "phase": "cache"}


def wait_for_cache(database_path, expected_sha, *, ready_timeout=120, poll_seconds=5,
                   clock=time.monotonic, sleep=time.sleep, check=cache_preflight):
    deadline = clock() + ready_timeout
    while True:
        try:
            return check(database_path, expected_sha)
        except AcceptanceError as exc:
            if clock() >= deadline:
                raise AcceptanceError(f"CACHE_READINESS_TIMEOUT:{exc}") from exc
            sleep(min(poll_seconds, max(0, deadline - clock())))


def accept_runtime(expected_sha, health_dir, *, window_seconds=60, ready_timeout=120,
                   poll_seconds=5, state_reader=read_service_state, health_reader=read_health,
                   clock=time.monotonic, sleep=time.sleep, now_fn=utcnow):
    deadline = clock() + ready_timeout
    baseline = {}
    stable_since = None
    sample_count = 0
    while True:
        errors = []
        sample_count += 1
        for unit, spec in SERVICES.items():
            state = state_reader(unit)
            if unit not in baseline:
                baseline[unit] = dict(state)
            original = baseline[unit]
            if state["restarts"] != original["restarts"]:
                raise AcceptanceError(f"{unit}:NRESTARTS_CHANGED:{original['restarts']}->{state['restarts']}")
            if original["pid"] > 0 and state["pid"] != original["pid"]:
                raise AcceptanceError(f"{unit}:PID_CHANGED:{original['pid']}->{state['pid']}")
            if original["pid"] == 0 and state["pid"] > 0:
                original["pid"] = state["pid"]
            if state["active"] == "failed":
                raise AcceptanceError(f"{unit}:SYSTEMD_FAILED")
            health = health_reader(Path(health_dir) / spec.health_file)
            failures = health_failures(unit, health, state, expected_sha, now_fn())
            source = health.get("process_source")
            source = source if isinstance(source, dict) else {}
            # During readiness a previous PID's health is expected briefly.
            # An error from this exact running PID must not be hidden by retries.
            fatal = (source.get("pid") == state["pid"] and state["pid"] > 0 and (
                health.get("status") in {"FAILED", "STARTUP_FAILED", "ERROR"}
                or any(item.startswith(("PROCESS_SHA", "PROCESS_SOURCE", "VERSION_MISMATCH")) for item in failures)
            ))
            if fatal:
                raise AcceptanceError(f"{unit}:" + ";".join(failures))
            errors.extend(f"{unit}:{failure}" for failure in failures)
        if errors:
            if stable_since is not None:
                raise AcceptanceError("STABILITY_WINDOW_FAILED:" + ";".join(errors))
            if clock() >= deadline:
                raise AcceptanceError("RUNTIME_READINESS_TIMEOUT:" + ";".join(errors))
            sleep(min(poll_seconds, max(0, deadline - clock())))
            continue
        if stable_since is None:
            stable_since = clock()
        elapsed = clock() - stable_since
        if elapsed >= window_seconds:
            return {"status": "ACCEPTED", "phase": "runtime", "expected_sha": expected_sha,
                    "stable_window_seconds": elapsed, "sample_count": sample_count,
                    "services": {unit: {"pid": state["pid"], "nrestarts": state["restarts"],
                                        "nrestarts_delta": 0} for unit, state in baseline.items()}}
        sleep(min(poll_seconds, window_seconds - elapsed))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("metadata", "cache", "runtime"))
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--database-path", default="/var/lib/agca/crypto_data.db")
    parser.add_argument("--health-dir", default="/var/lib/agca")
    parser.add_argument("--window-seconds", type=float, default=60)
    parser.add_argument("--ready-timeout", type=float, default=120)
    parser.add_argument("--poll-seconds", type=float, default=5)
    parser.add_argument("--spot-cache")
    parser.add_argument("--usdm-cache")
    args = parser.parse_args(argv)
    if (len(args.expected_sha) != 40 or any(ch not in "0123456789abcdef" for ch in args.expected_sha)
            or not 0 < args.ready_timeout <= 120 or not 0 < args.window_seconds <= 300
            or not 0 < args.poll_seconds <= 30):
        parser.error("exact lowercase 40-character SHA and bounded positive timing arguments required")
    try:
        if PROCESS_SOURCE["git_sha"] != args.expected_sha or PROCESS_SOURCE["git_dirty"] is not False:
            raise AcceptanceError("CHECKOUT_SHA_OR_CLEAN_STATE_MISMATCH")
        if args.phase == "metadata":
            result = metadata_preflight(args.health_dir, spot_path=args.spot_cache, usdm_path=args.usdm_cache)
        elif args.phase == "cache":
            result = wait_for_cache(args.database_path, args.expected_sha,
                                    ready_timeout=args.ready_timeout, poll_seconds=args.poll_seconds)
        else:
            result = accept_runtime(args.expected_sha, args.health_dir,
                                    window_seconds=args.window_seconds, ready_timeout=args.ready_timeout,
                                    poll_seconds=args.poll_seconds)
    except (AcceptanceError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "REJECTED", "phase": args.phase, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
