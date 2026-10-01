"""Process-bound source identity and health history for evidence producers.

Capture once while modules load, never refresh it from a checkout that may have
changed underneath a running worker. This module has no execution authority.
"""
from __future__ import annotations

import os
import string
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def capture_process_source(repo_path=None):
    root = Path(repo_path or Path(__file__).resolve().parents[1])
    source = {
        "git_sha": None, "git_dirty": None, "pid": os.getpid(),
        "process_started_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        sha = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False, capture_output=True, text=True, timeout=2,
        )
        dirty = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), "status",
             "--porcelain", "--untracked-files=no"],
            check=False, capture_output=True, text=True, timeout=2,
        )
        value = sha.stdout.strip()
        if (sha.returncode == 0 and dirty.returncode == 0 and len(value) == 40
                and all(ch in string.hexdigits for ch in value)):
            source.update(git_sha=value.lower(), git_dirty=bool(dirty.stdout.strip()))
    except (OSError, subprocess.SubprocessError):
        pass
    return source


PROCESS_SOURCE = capture_process_source()


class WorkerHealthState:
    """Keep process identity and last result timestamps across health writes."""

    def __init__(self, source=None):
        self.source = dict(PROCESS_SOURCE if source is None else source)
        self.last_success_at = None
        self.last_error_at = None
        self.last_error = None

    def decorate(self, payload, *, success=None):
        payload = dict(payload)
        stamp = payload.setdefault("at", datetime.now(timezone.utc).isoformat())
        if success is True or (success is None and payload.get("status") in {"HEALTHY", "CONNECTED"}):
            self.last_success_at = stamp
        elif payload.get("status") in {"DEGRADED", "ERROR", "STARTUP_FAILED"}:
            self.last_error_at = stamp
            self.last_error = str(payload.get("error", payload["status"]))[:500]
        payload.update(
            process_source=dict(self.source),
            last_success_at=self.last_success_at,
            last_error_at=self.last_error_at,
            last_error=self.last_error,
        )
        return payload
