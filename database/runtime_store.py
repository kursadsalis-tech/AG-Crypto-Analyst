"""SQLite-first connection with optional best-effort PostgreSQL mirroring.

The local SQLite commit is always authoritative.  When
AGCA_REMOTE_DATABASE_URL is configured, tables changed by a transaction are
copied to PostgreSQL after the local commit succeeds.  A remote failure is
logged and never rolls back (or prevents) the local write.
"""

from __future__ import annotations

import logging
import getpass
import os
import re
import sqlite3
import subprocess
import time
from typing import Iterable


LOGGER = logging.getLogger("agca.runtime_store")
REMOTE_URL_ENV = "AGCA_REMOTE_DATABASE_URL"
REMOTE_SCHEMA_ENV = "AGCA_REMOTE_DATABASE_SCHEMA"
REMOTE_TIMEOUT_ENV = "AGCA_REMOTE_CONNECT_TIMEOUT_SECONDS"
KEYCHAIN_SERVICE = "agca.remote-runtime-store"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WRITE_TABLE = re.compile(
    r"^\s*(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM|"
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s+[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_retry_after = 0.0


def _quote(identifier: str) -> str:
    if not _IDENTIFIER.fullmatch(identifier):
        raise ValueError(f"Unsafe database identifier: {identifier!r}")
    return f'"{identifier}"'


def _postgres_type(sqlite_type: str) -> str:
    value = (sqlite_type or "").upper()
    if "INT" in value:
        return "BIGINT"
    if any(part in value for part in ("REAL", "FLOA", "DOUB")):
        return "DOUBLE PRECISION"
    if "BLOB" in value:
        return "BYTEA"
    if any(part in value for part in ("NUM", "DEC", "BOOL")):
        return "NUMERIC"
    return "TEXT"


class RuntimeCursor(sqlite3.Cursor):
    def _track(self, statement: str) -> None:
        match = _WRITE_TABLE.match(statement)
        if match:
            self.connection._remote_dirty_tables.add(match.group(1))

    def execute(self, sql, parameters=()):
        self._track(sql)
        return super().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters: Iterable):
        self._track(sql)
        return super().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script):
        for statement in sql_script.split(";"):
            self._track(statement)
        return super().executescript(sql_script)


class RuntimeConnection(sqlite3.Connection):
    """A sqlite3-compatible connection that mirrors committed writes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._remote_dirty_tables: set[str] = set()

    def cursor(self, factory=RuntimeCursor):
        return super().cursor(factory)

    def execute(self, sql, parameters=()):
        cursor = self.cursor()
        return cursor.execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        cursor = self.cursor()
        return cursor.executemany(sql, seq_of_parameters)

    def executescript(self, sql_script):
        cursor = self.cursor()
        return cursor.executescript(sql_script)

    def commit(self):
        super().commit()
        dirty_tables = set(self._remote_dirty_tables)
        self._remote_dirty_tables.clear()
        if dirty_tables:
            _mirror_after_local_commit(self, dirty_tables)

    def rollback(self):
        super().rollback()
        self._remote_dirty_tables.clear()

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


def connect(database, *args, **kwargs):
    """Return the SQLite-compatible AGCA runtime connection."""

    kwargs.setdefault("factory", RuntimeConnection)
    return sqlite3.connect(database, *args, **kwargs)


def _mirror_after_local_commit(local: sqlite3.Connection, tables: set[str]) -> None:
    global _retry_after
    remote_url = _remote_url()
    if not remote_url or time.monotonic() < _retry_after:
        return

    schema = os.getenv(REMOTE_SCHEMA_ENV, "agca_runtime").strip()
    try:
        _quote(schema)
        timeout = max(1, int(os.getenv(REMOTE_TIMEOUT_ENV, "5")))
        import psycopg

        with psycopg.connect(remote_url, connect_timeout=timeout) as remote:
            with remote.cursor() as cursor:
                cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote(schema)}")
                for table in sorted(tables):
                    _mirror_table(local, cursor, schema, table)
        LOGGER.info("Remote runtime mirror updated: tables=%s", ",".join(sorted(tables)))
    except Exception as exc:  # SQLite must remain available under every failure mode.
        retry_seconds = max(1, int(os.getenv("AGCA_REMOTE_RETRY_SECONDS", "60")))
        _retry_after = time.monotonic() + retry_seconds
        LOGGER.warning(
            "Remote runtime mirror failed; local SQLite commit is intact (error_type=%s)",
            type(exc).__name__,
        )


def _remote_url() -> str:
    """Read the URL from the environment, then macOS Keychain if available."""

    configured = os.getenv(REMOTE_URL_ENV, "").strip()
    if configured:
        return configured
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                getpass.getuser(),
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _mirror_table(local, remote_cursor, schema: str, table: str) -> None:
    table_q = _quote(table)
    schema_q = _quote(schema)
    columns = local.execute(f"PRAGMA table_info({table_q})").fetchall()
    if not columns:
        return

    names = [column[1] for column in columns]
    primary_keys = [column[1] for column in sorted(columns, key=lambda item: item[5]) if column[5]]
    definitions = []
    for column in columns:
        definition = f"{_quote(column[1])} {_postgres_type(column[2])}"
        if column[3]:
            definition += " NOT NULL"
        definitions.append(definition)
    if primary_keys:
        definitions.append("PRIMARY KEY (" + ",".join(_quote(name) for name in primary_keys) + ")")

    remote_cursor.execute(
        f"CREATE TABLE IF NOT EXISTS {schema_q}.{table_q} ({','.join(definitions)})"
    )
    remote_cursor.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s",
        (schema, table),
    )
    existing = {row[0] for row in remote_cursor.fetchall()}
    for column in columns:
        if column[1] not in existing:
            remote_cursor.execute(
                f"ALTER TABLE {schema_q}.{table_q} ADD COLUMN {_quote(column[1])} "
                f"{_postgres_type(column[2])}"
            )

    rows = local.execute(
        f"SELECT {','.join(_quote(name) for name in names)} FROM {table_q}"
    ).fetchall()
    if not primary_keys:
        remote_cursor.execute(f"DELETE FROM {schema_q}.{table_q}")
    if not rows:
        return

    columns_sql = ",".join(_quote(name) for name in names)
    placeholders = ",".join(["%s"] * len(names))
    if primary_keys:
        updates = [name for name in names if name not in primary_keys]
        conflict = ",".join(_quote(name) for name in primary_keys)
        if updates:
            action = "DO UPDATE SET " + ",".join(
                f"{_quote(name)}=EXCLUDED.{_quote(name)}" for name in updates
            )
        else:
            action = "DO NOTHING"
        suffix = f" ON CONFLICT ({conflict}) {action}"
    else:
        suffix = ""
    remote_cursor.executemany(
        f"INSERT INTO {schema_q}.{table_q} ({columns_sql}) VALUES ({placeholders}){suffix}",
        rows,
    )
