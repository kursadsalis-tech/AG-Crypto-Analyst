"""Bounded, process-wide coordination for canonical local SQLite writers.

The database remains authoritative; these advisory locks only serialize admission
before SQLite itself takes a write lock. Read/compute/network work owns no writer
slot. A realtime waiter closes background admission until its transaction ends.
Lock files are stable sidecars: never unlink them while any process is running.
No journal-mode migration is performed; existing WAL/rollback mode is preserved.
"""
from __future__ import annotations

import fcntl
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

BACKGROUND_BUSY_SECONDS = 0.25
REALTIME_BUSY_SECONDS = 0.05
_SQL_COMMENTS = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)


def is_sqlite_busy(exc):
    """Only SQLite BUSY/LOCKED (including extended codes) are transient."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int):
        return (code & 0xff) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    # Python-created exceptions and older SQLite adapters lack numeric codes.
    message = str(exc).strip().lower()
    return message in {"database is locked", "database is busy", "database table is locked"} or message.startswith("database table is locked:")


def _writes(sql):
    text = _SQL_COMMENTS.sub("", str(sql)).strip().upper()
    if not text:
        return False
    first = text.split(None, 1)[0]
    if first in {"SELECT", "EXPLAIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE"}:
        return False
    if first == "BEGIN":
        return "IMMEDIATE" in text or "EXCLUSIVE" in text
    if first == "PRAGMA":
        # Read pragmas and connection-local tuning do not take the writer slot.
        return bool(re.search(r"\b(JOURNAL_MODE|WAL_CHECKPOINT|USER_VERSION|APPLICATION_ID)\b", text) and ("=" in text or "WAL_CHECKPOINT(" in text))
    if first == "WITH":
        return bool(re.search(r"\b(INSERT|UPDATE|DELETE|REPLACE)\b", text))
    return True  # DML/DDL, VACUUM, ANALYZE and unknown extensions fail safe.


def _database_file(database):
    value = os.fsdecode(database)
    if value == ":memory:" or (value.startswith("file:") and "mode=memory" in value):
        return None
    if value.startswith("file:"):
        value = unquote(urlparse(value).path)
    return str(Path(value).resolve()) if value else None


class CoordinatedCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        sql = self.connection._bounded_sql(sql)
        return self.connection._statement(lambda: super(CoordinatedCursor, self).execute(sql, parameters), sql)

    def executemany(self, sql, parameters):
        return self.connection._statement(lambda: super(CoordinatedCursor, self).executemany(sql, parameters), sql)

    def executescript(self, sql_script):
        # executescript implicitly commits first, just like sqlite3 itself.
        self.connection.commit()
        return self.connection._statement(lambda: super(CoordinatedCursor, self).executescript(sql_script), sql_script, script=True)


class CoordinatedConnection(sqlite3.Connection):
    """sqlite3-compatible connection; coordination follows transaction lifetime."""
    def __init__(self, database, *args, **kwargs):
        super().__init__(database, *args, **kwargs)
        self._writer_database = _database_file(database)
        self._writer_priority = "background"
        self._writer_wait = BACKGROUND_BUSY_SECONDS
        self._writer_fd = None
        self._priority_fd = None
        self._writer_owned = False
        self._priority_owned = False
        sqlite3.Connection.execute(self, f"PRAGMA busy_timeout={int(self._writer_wait * 1000)}")

    def configure_writer(self, *, priority="background", timeout=BACKGROUND_BUSY_SECONDS):
        if priority not in {"background", "realtime"}:
            raise ValueError("unknown SQLite writer priority")
        if self._writer_owned:
            raise RuntimeError("cannot change SQLite priority in a write transaction")
        self._writer_priority = priority
        self._writer_wait = min(max(0.0, float(timeout)), BACKGROUND_BUSY_SECONDS)
        sqlite3.Connection.execute(self, f"PRAGMA busy_timeout={int(self._writer_wait * 1000)}")
        return self

    def _bounded_sql(self, sql):
        match = re.fullmatch(r"\s*PRAGMA\s+busy_timeout\s*=\s*(\d+)\s*;?\s*", str(sql), re.IGNORECASE)
        if match:
            return f"PRAGMA busy_timeout={min(int(match[1]), int(self._writer_wait * 1000))}"
        return sql

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _admit_writer(self):
        if self._writer_owned or self._writer_database is None:
            return
        if self._writer_fd is None:
            self._writer_fd = os.open(self._writer_database + ".agca-writer.lock", os.O_CREAT | os.O_RDWR, 0o600)
            self._priority_fd = os.open(self._writer_database + ".agca-realtime.lock", os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + self._writer_wait
        while True:
            try:
                if not self._priority_owned:
                    kind = fcntl.LOCK_EX if self._writer_priority == "realtime" else fcntl.LOCK_SH
                    fcntl.flock(self._priority_fd, kind | fcntl.LOCK_NB)
                    self._priority_owned = True
                try:
                    fcntl.flock(self._writer_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._writer_owned = True
                    return
                finally:
                    # Background never queues while holding admission. Realtime
                    # keeps it closed while waiting, so background cannot jump it.
                    if self._writer_priority == "background":
                        fcntl.flock(self._priority_fd, fcntl.LOCK_UN)
                        self._priority_owned = False
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self._release_writer()
                    exc = sqlite3.OperationalError("database is locked")
                    exc.sqlite_errorcode = sqlite3.SQLITE_BUSY
                    exc.sqlite_errorname = "SQLITE_BUSY"
                    raise exc
                time.sleep(min(0.005, max(0, deadline - time.monotonic())))

    def _release_writer(self):
        if self._writer_owned:
            fcntl.flock(self._writer_fd, fcntl.LOCK_UN)
            self._writer_owned = False
        if self._priority_owned:
            fcntl.flock(self._priority_fd, fcntl.LOCK_UN)
            self._priority_owned = False

    def _statement(self, operation, sql, *, script=False):
        if _writes(sql) or (script and any(_writes(part) for part in sql.split(";"))):
            self._admit_writer()
        try:
            return operation()
        finally:
            if not self.in_transaction:
                self._release_writer()

    def cursor(self, factory=CoordinatedCursor):
        return super().cursor(factory)

    def execute(self, sql, parameters=()):
        return CoordinatedCursor(self).execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        return CoordinatedCursor(self).executemany(sql, seq_of_parameters)

    def executescript(self, sql_script):
        return CoordinatedCursor(self).executescript(sql_script)

    def commit(self):
        try:
            super().commit()
        finally:
            if not self.in_transaction:
                self._release_writer()

    def rollback(self):
        try:
            super().rollback()
        finally:
            if not self.in_transaction:
                self._release_writer()

    def close(self):
        try:
            super().close()
        finally:
            self._release_writer()
            for name in ("_writer_fd", "_priority_fd"):
                fd = getattr(self, name, None)
                if fd is not None:
                    os.close(fd)
                    setattr(self, name, None)

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is not None:
            self.rollback()
        else:
            try:
                self.commit()
            except Exception:
                self.rollback()
                raise
        return False


def connect_local(database, *args, timeout=BACKGROUND_BUSY_SECONDS, priority="background", **kwargs):
    kwargs.setdefault("factory", CoordinatedConnection)
    connection = sqlite3.connect(database, *args, timeout=min(float(timeout), BACKGROUND_BUSY_SECONDS), **kwargs)
    return connection.configure_writer(priority=priority, timeout=timeout)
