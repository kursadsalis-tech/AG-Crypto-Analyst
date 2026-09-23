#!/usr/bin/env python3
"""Deterministic preflight for approval-gated AGCA observer deployment."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.causal_evidence_store import MACRO_TABLE
from database.behavior_event_outcome_store import OUTCOME_TABLE
from database.market_behavior_store import EXTERNAL_EVIDENCE_TABLE, TABLE
from database.runtime_store import REMOTE_RUNTIME_TABLES
from paper_trading.governance import authority_contract
from runtime.cloud_attestation import (
    EXACT_MACRO_HEALTH_VERSION, MARKET_BEHAVIOR_HEALTH_VERSION,
)
from scripts.collect_closed_behavior_minute import HEALTH_CONTRACT_VERSION
from scripts.observe_exact_macro_events import HEALTH_VERSION


REQUIRED_FILES = (
    "deploy/agca-market-behavior-observer.service",
    "deploy/agca-market-behavior-observer.timer",
    "deploy/agca-exact-macro-observer.service",
    "deploy/agca-exact-macro-observer.timer",
    "deploy/agca-cloud-attestation.service",
    "deploy/agca-market-behavior-observer-failure-alert.service",
    "deploy/agca-observer-health-monitor.service",
    "deploy/agca-observer-health-monitor.timer",
    "scripts/collect_closed_behavior_minute.py",
    "scripts/observe_exact_macro_events.py",
    "runtime/cloud_attestation.py",
    "runtime/observer_health_alert.py",
    "database/runtime_store.py",
    "database/market_behavior_store.py",
    "database/behavior_event_outcome_store.py",
    "database/causal_evidence_store.py",
    "data/adaptive_market_scanner.py",
    "data/behavior_event_forward_measurement.py",
    "data/causal_minute_evidence.py",
    "data/federal_reserve_exact_events.py",
    "data/live_behavior_evidence_bundle.py",
    "data/binance_liquidation_channel.py",
    "data/binance_live_evidence.py",
    "data/binance_market_universe.py",
    "data/rolling_behavior_window.py",
    "paper_trading/governance.py",
    "requirements-runtime.txt",
)
LOCAL_ONLY_TABLES = (TABLE, EXTERNAL_EVIDENCE_TABLE, MACRO_TABLE, OUTCOME_TABLE)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_readiness(root=PROJECT_ROOT):
    root = Path(root).resolve()
    failures = []
    hashes = {}
    for relative in REQUIRED_FILES:
        path = root / relative
        if not path.is_file():
            failures.append(f"MISSING_FILE:{relative}")
        else:
            hashes[relative] = _sha256(path)

    services = {}
    for name in (
        "agca-market-behavior-observer.service",
        "agca-exact-macro-observer.service",
    ):
        path = root / "deploy" / name
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        checks = {
            "paper_only": "Environment=AGCA_EXECUTION_MODE=PAPER_ONLY" in text,
            "remote_inline_disabled": "Environment=AGCA_REMOTE_MIRROR_INLINE=0" in text,
            "private_umask": "UMask=0077" in text,
            "bounded_memory": "MemoryMax=" in text,
            "bounded_cpu": "CPUQuota=" in text,
            "bounded_timeout": "TimeoutStartSec=" in text,
            "does_not_enable_itself": "systemctl enable" not in text,
        }
        services[name] = checks
        failures.extend(
            f"SERVICE_CONTRACT:{name}:{key}"
            for key, passed in checks.items() if not passed
        )

    requirements_path = root / "requirements-runtime.txt"
    requirements = (
        requirements_path.read_text(encoding="utf-8")
        if requirements_path.is_file() else ""
    )
    dependencies = {
        "requests": "requests>=" in requirements,
        "websockets": "websockets>=" in requirements,
        "certifi": "certifi>=" in requirements,
    }
    failures.extend(
        f"RUNTIME_DEPENDENCY_MISSING:{name}"
        for name, passed in dependencies.items() if not passed
    )
    local_only = {
        table: table not in REMOTE_RUNTIME_TABLES for table in LOCAL_ONLY_TABLES
    }
    failures.extend(
        f"LOCAL_TABLE_REMOTE_MIRROR_ENABLED:{table}"
        for table, passed in local_only.items() if not passed
    )
    health_versions = {
        "market_behavior_matches_attestation": (
            HEALTH_CONTRACT_VERSION == MARKET_BEHAVIOR_HEALTH_VERSION
        ),
        "exact_macro_matches_attestation": (
            HEALTH_VERSION == EXACT_MACRO_HEALTH_VERSION
        ),
    }
    failures.extend(
        f"HEALTH_VERSION_MISMATCH:{name}"
        for name, passed in health_versions.items() if not passed
    )
    behavior_service = services.get(
        "agca-market-behavior-observer.service", {}
    )
    behavior_service_path = root / "deploy/agca-market-behavior-observer.service"
    behavior_service_text = (
        behavior_service_path.read_text(encoding="utf-8")
        if behavior_service_path.is_file() else ""
    )
    canonical_provider = {
        "behavior_service_binance_websocket_timeout": (
            "AGCA_BINANCE_WS_OPEN_TIMEOUT_SECONDS=10" in behavior_service_text
        ),
        "behavior_service_has_no_binance_rest_timeout": (
            "AGCA_BINANCE_REQUEST_TIMEOUT_SECONDS" not in behavior_service_text
        ),
    }
    failures.extend(
        f"CANONICAL_PROVIDER_CONTRACT:{name}"
        for name, passed in canonical_provider.items() if not passed
    )
    authority = authority_contract()
    authority_safe = (
        authority["execution_mode"] == "PAPER_ONLY"
        and authority["may_send_real_orders"] is False
        and authority["may_promote_model"] is False
        and authority["may_promote_policy"] is False
    )
    if not authority_safe:
        failures.append("GOVERNANCE_AUTHORITY_EXPANDED")
    return {
        "contract_version": "OBSERVER_DEPLOYMENT_READINESS_V2_BINANCE_CANONICAL",
        "project_root": str(root),
        "required_file_sha256": hashes,
        "service_contracts": services,
        "runtime_dependencies_declared": dependencies,
        "local_only_tables": local_only,
        "health_contract_versions": health_versions,
        "canonical_market_provider": canonical_provider,
        "paper_authority_safe": authority_safe,
        "failure_count": len(failures), "failures": failures,
        "ready_for_controlled_cloud_install": not failures,
        "cloud_install_performed": False,
        "timers_started": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = build_readiness()
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if result["ready_for_controlled_cloud_install"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
