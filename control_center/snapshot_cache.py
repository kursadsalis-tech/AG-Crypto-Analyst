"""One request-driven, read-only snapshot refresh per panel server.

No timer, DB connection, market feed or trading/learning authority lives here.
Keep one immutable encoded success and constant-size, saturating telemetry.
"""
from __future__ import annotations

import json
import math
import threading
import time
from datetime import datetime, timezone

DEFAULT_TTL_SECONDS = 2.0
MAX_COUNTER = 2**63 - 1
# Fixed labels only: never expose SQL, paths, exceptions or unbounded history.
REFRESH_PHASES = frozenset({
    "COMPUTE", "INPUTS", "DB_CONNECT", "RUNTIME_VISIBILITY", "SCHEMA",
    "PORTFOLIO", "OPEN_POSITIONS", "ACTIVITY", "LEARNING", "SELECTION",
    "REPORTS", "CLOSED_POSITIONS", "PROJECTION", "PRESENTATION", "ENCODING",
})


class SnapshotCache:
    def __init__(self, compute, *, ttl_seconds=DEFAULT_TTL_SECONDS,
                 clock=time.monotonic):
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be finite and positive")
        self._compute = compute
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._prefix = None
        self._completed = None
        self._captured = None
        self._captured_at = None
        self._last_success_at = None
        self._refresh_started = None
        self._duration = None
        self._cpu_duration = None
        self._next_attempt = 0.0
        self._error = None
        self._refreshing = False
        self._closed = False
        self._thread = None
        self._phase = None
        self._phase_started = None
        self._thread_id = None
        self._cpu_started = None
        self._checkpoint_cpu = None
        self._counters = dict(hits=0, misses=0, stale_served=0,
                              refresh_attempts=0, refresh_failures=0, generation=0)

    def _increment(self, name):
        self._counters[name] = min(MAX_COUNTER, self._counters[name] + 1)

    def _telemetry(self, now):
        age = None if self._completed is None else max(0.0, now - self._completed)
        state = "UNAVAILABLE" if age is None else (
            "CURRENT" if age < self._ttl and self._error is None else "STALE")
        return {
            "state": state,
            "age_seconds": None if age is None else round(age, 3),
            "snapshot_age_seconds": None if self._captured is None else
                round(max(0.0, now - self._captured), 3),
            "ttl_seconds": self._ttl,
            "captured_at": self._captured_at,
            "last_success_at": self._last_success_at,
            "refresh_in_progress": self._refreshing,
            "refresh_elapsed_seconds": round(max(0.0, now - self._refresh_started), 3)
                if self._refreshing else None,
            "refresh_phase": self._phase,
            "refresh_phase_elapsed_seconds": round(max(0.0, now - self._phase_started), 3)
                if self._refreshing else None,
            "refresh_thread_id": self._thread_id if self._refreshing else None,
            # A checkpoint, not a continuously sampled CPU counter. The native
            # TID permits a bounded external /proc schedstat/wchan diagnostic.
            "refresh_checkpoint_cpu_seconds": self._checkpoint_cpu,
            "compute_duration_seconds": self._duration,
            "compute_cpu_seconds": self._cpu_duration,
            "last_error": self._error,
            **self._counters,
        }

    def telemetry(self):
        """Liveness inspection must never trigger an expensive refresh."""
        with self._lock:
            return self._telemetry(self._clock())

    def response(self):
        """Return immediately; cache misses never wait for snapshot computation."""
        with self._lock:
            now = self._clock()
            current = self._telemetry(now)["state"] == "CURRENT"
            self._increment("hits" if current else "misses")
            if not current and self._prefix is not None:
                self._increment("stale_served")
            if (not current and not self._refreshing and not self._closed
                    and now >= self._next_attempt):
                self._refreshing = True
                self._refresh_started = now
                self._phase = "QUEUED"
                self._phase_started = now
                self._thread_id = None
                self._checkpoint_cpu = None
                self._increment("refresh_attempts")
                try:
                    self._thread = threading.Thread(
                        target=self._refresh, name="panel-snapshot-refresh", daemon=True)
                    self._thread.start()
                except RuntimeError:
                    self._refreshing = False
                    self._error = "refresh_start_failed"
                    self._increment("refresh_failures")
                    self._next_attempt = now + self._ttl
            metadata = self._telemetry(now)
            prefix = self._prefix
        # The expensive snapshot is serialized once per success, outside the
        # lock. Each HTTP request encodes only this small telemetry dictionary.
        meta = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
        if prefix is None:
            prefix = b'{"error":"snapshot_unavailable",'
        return prefix + b'"snapshot_cache":' + meta + b"}", (
            503 if metadata["state"] == "UNAVAILABLE" else 200), metadata

    def checkpoint(self, phase):
        """Record progress from the active compute thread; never starts work."""
        if phase not in REFRESH_PHASES:
            raise ValueError("unknown snapshot phase")
        with self._lock:
            if not self._refreshing or threading.current_thread() is not self._thread:
                raise RuntimeError("snapshot checkpoint outside refresh thread")
            self._phase = phase
            self._phase_started = self._clock()
            self._checkpoint_cpu = round(max(0.0, time.thread_time() - self._cpu_started), 6)

    def _refresh(self):
        started = self._clock()
        cpu_started = time.thread_time()
        with self._lock:
            self._thread_id = threading.get_native_id()
            self._cpu_started = cpu_started
        self.checkpoint("COMPUTE")
        captured_at = datetime.now(timezone.utc).isoformat()
        prefix = None
        error = None
        try:
            payload = self._compute()
            if not isinstance(payload, dict) or "snapshot_cache" in payload:
                raise ValueError("snapshot must be an object without reserved cache metadata")
            self.checkpoint("ENCODING")
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                                 separators=(",", ":")).encode("utf-8")
            prefix = encoded[:-1] + (b"," if len(encoded) > 2 else b"")
        except Exception:
            # A failed refresh cannot poison the last success or leak exception
            # text (which may contain local paths/SQL) into diagnostics.
            error = "snapshot_refresh_failed"
        finally:
            finished = self._clock()
            cpu_duration = time.thread_time() - cpu_started
            with self._lock:
                self._duration = round(max(0.0, finished - started), 6)
                self._cpu_duration = round(max(0.0, cpu_duration), 6)
                self._error = error
                if prefix is not None:
                    self._prefix = prefix
                    self._completed = finished
                    self._captured = started
                    self._captured_at = captured_at
                    self._last_success_at = datetime.now(timezone.utc).isoformat()
                    self._increment("generation")
                else:
                    self._increment("refresh_failures")
                # Also backs off cold/error retries; no request-induced storm.
                self._next_attempt = finished + self._ttl
                self._refreshing = False

    def close(self):
        # A stuck read is never replaced by another concurrent computation.
        # The daemon refresh cannot hold up panel shutdown indefinitely.
        with self._lock:
            self._closed = True
