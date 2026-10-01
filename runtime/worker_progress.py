"""Evidence of completed work for bounded producer startup supervision.

SQL progress is emitted only from SQLite's executed-opcode callback; a timer or
an unchanged RUNNING health write cannot extend the readiness deadline.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone


class WorkProgress:
    def __init__(self, publish, *, clock=time.monotonic, interval_seconds=5.0):
        self.publish = publish
        self.clock = clock
        self.interval_seconds = float(interval_seconds)
        self.sequence = 0
        self.phase = "STARTING"
        self.last_report = float("-inf")
        self.last_progress = None

    def advance(self, phase):
        self.sequence += 1
        self.phase = phase
        self.last_report = self.clock()
        self.last_progress = {"phase": phase, "sequence": self.sequence,
                              "at": datetime.now(timezone.utc).isoformat()}
        self.publish(dict(self.last_progress))

    def step(self, phase):
        """Call after a real completed CPU/collection chunk, never from a timer."""
        if self.clock() - self.last_report >= self.interval_seconds:
            self.advance(phase)

    def sqlite_step(self):
        # Called only after 100,000 SQLite VM instructions actually execute.
        self.step(self.phase + "_SQL" if not self.phase.endswith("_SQL") else self.phase)
        return 0

    def attach(self, connection):
        connection.set_progress_handler(self.sqlite_step, 100_000)
