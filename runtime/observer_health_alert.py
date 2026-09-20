"""Local, idempotent health-state tracking for the market-behavior observer."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from runtime.cloud_attestation import market_behavior_health


STATE_PATH = Path("/var/lib/agca/market-behavior-alert-state.json")


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"consecutive_unhealthy": 0, "alert_open": False}


def _write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def evaluate(health, previous, forced_event=None, threshold=3):
    healthy = health.get("state") == "CURRENT" and health.get("status") == "HEALTHY"
    consecutive = 0 if healthy else int(previous.get("consecutive_unhealthy") or 0) + 1
    alert_open = bool(previous.get("alert_open"))
    action = None
    reason = forced_event
    if forced_event:
        action, alert_open = "ALERT", True
    elif not healthy and (health.get("state") in {"MISSING", "INVALID", "STALE"} or consecutive >= threshold):
        if not alert_open:
            action, alert_open = "ALERT", True
        reason = f"state={health.get('state')} status={health.get('status')}"
    elif healthy and alert_open:
        action, alert_open, reason = "RECOVERY", False, "HEALTHY"
    return {
        "consecutive_unhealthy": consecutive,
        "alert_open": alert_open,
        "last_action": action,
        "last_reason": reason,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }, action, reason


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event")
    parser.add_argument("--health-path", default="/var/lib/agca/market-behavior-health.json")
    parser.add_argument("--state-path", default=str(STATE_PATH))
    parser.add_argument("--threshold", type=int, default=3)
    args = parser.parse_args()
    health = market_behavior_health(path=args.health_path)
    state, action, reason = evaluate(
        health, _load(args.state_path), forced_event=args.event, threshold=args.threshold,
    )
    # Operational health is recorded locally. Notification delivery is intentionally
    # outside this runtime component; AGCA must not depend on Telegram to stay healthy.
    state["delivery_attempted"] = False
    state["delivery_succeeded"] = None
    _write(args.state_path, state)
    print(json.dumps({"action": action, "reason": reason, "delivered": None}, sort_keys=True))


if __name__ == "__main__":
    main()
