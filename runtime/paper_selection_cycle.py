"""Independent minute PAPER lifecycle, isolated from the heavy product cycle."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from paper_trading import crypto_profit_agent


HEALTH_PATH = Path(os.getenv(
    "AGCA_PAPER_SELECTION_HEALTH", "/var/lib/agca/paper-selection-health.json"
))


def _write_health(status, **details):
    payload = {
        "status": status, "at": datetime.now(timezone.utc).isoformat(),
        "execution_mode": "PAPER_ONLY", "real_order_allowed": False, **details,
    }
    HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = HEALTH_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, HEALTH_PATH)


def main():
    """Legacy timer shim: lifecycle authority belongs to paper_realtime_worker."""
    if os.getenv("AGCA_EXECUTION_MODE", "PAPER_ONLY") != "PAPER_ONLY":
        raise SystemExit("Safety gate: PAPER selection accepts PAPER_ONLY exclusively")
    result = {
        "status": "DELEGATED_TO_REALTIME_WORKER",
        "selected": 0, "filled": 0, "closed": 0, "cancelled": 0,
    }
    _write_health(
        "DELEGATED_TO_REALTIME_WORKER",
        decision_status="LIVE_EVENT_STREAM_AUTHORITY",
        selected=0, filled=0, closed=0, cancelled=0,
        open_positions=0, learning_status="REALTIME_WORKER_OWNS_LIFECYCLE",
    )
    return result


if __name__ == "__main__":
    main()
