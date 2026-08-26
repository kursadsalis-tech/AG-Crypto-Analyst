"""Long-running provider-neutral AGCA worker for a persistent cloud host."""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

from analysis import forward_shadow_cycle
from decision_engine import hourly_forecast_job
from runtime import cloud_attestation


STATE_DIR = Path(os.getenv("AGCA_STATE_DIR", "/var/lib/agca"))
STOP = False


def stamp():
    return datetime.now(timezone.utc).isoformat()


def _stop(*_args):
    global STOP
    STOP = True


def _write_health(status, **details):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"status": status, "at": stamp(), **details}
    (STATE_DIR / "worker-health.json").write_text(
        json.dumps(payload, default=_json_default), encoding="utf-8"
    )


def _json_default(value):
    """Serialize runtime timestamps without masking unsupported result types."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    raise TypeError(
        f"Object of type {type(value).__name__} is not JSON serializable"
    )


def _record_attestation_safely(status, last_results):
    """Keep reporting transport failures separate from forecast-worker health."""
    try:
        return cloud_attestation.record(status, last_results)
    except Exception as exc:
        print(
            "Cloud attestation delivery failed; worker cycle remains authoritative: "
            f"{type(exc).__name__}: {str(exc)[:300]}"
        )
        return None


def _prepare_persistent_state():
    """Move mutable runtime state out of the disposable container layer."""
    project = Path(os.getenv("AGCA_PROJECT_DIR", "/app"))
    state_db = STATE_DIR / "crypto_data.db"
    app_db = project / "crypto_data.db"
    if not state_db.exists() and app_db.exists() and not app_db.is_symlink():
        shutil.copy2(app_db, state_db)
    if app_db.exists() or app_db.is_symlink():
        app_db.unlink()
    app_db.symlink_to(state_db)

    state_reports = STATE_DIR / "reports"
    app_reports = project / "reports"
    if not state_reports.exists():
        if app_reports.exists() and not app_reports.is_symlink():
            shutil.copytree(app_reports, state_reports)
        else:
            state_reports.mkdir(parents=True)
    if app_reports.exists() or app_reports.is_symlink():
        if app_reports.is_symlink():
            app_reports.unlink()
        else:
            shutil.rmtree(app_reports)
    app_reports.symlink_to(state_reports, target_is_directory=True)


def main():
    if os.getenv("AGCA_EXECUTION_MODE", "PAPER_ONLY") != "PAPER_ONLY":
        raise SystemExit("Safety gate: cloud worker accepts PAPER_ONLY exclusively")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chdir(os.getenv("AGCA_PROJECT_DIR", "/app"))
    _prepare_persistent_state()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    with (STATE_DIR / "worker.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another AGCA worker owns this persistent volume")
        last_five = last_hour = last_attested_five = None
        last_results = {}
        _write_health("STARTING")
        while not STOP:
            now = datetime.now(timezone.utc)
            five_key = (now.year, now.month, now.day, now.hour, now.minute // 5)
            hour_key = (now.year, now.month, now.day, now.hour)
            results = {}
            try:
                if five_key != last_five:
                    results["product_cycle"] = forward_shadow_cycle.main()
                    last_five = five_key
                if now.minute >= 2 and hour_key != last_hour:
                    results["hourly_job"] = hourly_forecast_job.main()
                    last_hour = hour_key
                if results:
                    last_results.update(results)
                    _write_health("HEALTHY", last_results=last_results)
                    _record_attestation_safely("HEALTHY", last_results)
                    last_attested_five = five_key
                else:
                    _write_health("HEALTHY", last_results=last_results)
            except Exception as exc:
                _write_health("DEGRADED", error_type=type(exc).__name__, message=str(exc)[:300])
                if five_key != last_attested_five:
                    _record_attestation_safely("DEGRADED", last_results)
                    last_attested_five = five_key
            time.sleep(10)
        _write_health("STOPPED")


if __name__ == "__main__":
    main()
