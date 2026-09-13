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
    if os.getenv("AGCA_EXECUTION_MODE", "PAPER_ONLY") != "PAPER_ONLY":
        raise SystemExit("Safety gate: PAPER selection accepts PAPER_ONLY exclusively")
    try:
        result = crypto_profit_agent.run_behavior_selection()
        decision_status = result.get("decision_status", "NO_SELECTION_EVIDENCE")
        _write_health(
            "DATA_BLOCKED" if decision_status in {"NO_FRESH_ELIGIBLE_BEHAVIOR", "NO_SELECTION_EVIDENCE"} else "SUCCESS",
            decision_status=decision_status,
            selected=int(result["selected"]), filled=int(result["filled"]),
            closed=int(result["closed"]), cancelled=int(result["cancelled"]),
            open_positions=int(result["portfolio"]["open_positions"]),
            learning_status=str(result["trade_learning"]["status"]),
        )
        return result
    except Exception as exc:
        _write_health("FAILED", error_type=type(exc).__name__, message=str(exc)[:300])
        raise


if __name__ == "__main__":
    main()
