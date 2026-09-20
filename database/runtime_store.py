"""SQLite-first connection with optional best-effort PostgreSQL mirroring.

The local SQLite commit is always authoritative.  When
AGCA_REMOTE_DATABASE_URL is configured, tables changed by a transaction are
copied to PostgreSQL after the local commit succeeds.  A remote failure is
logged and never rolls back (or prevents) the local write.
"""

from __future__ import annotations

import logging
import getpass
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable


LOGGER = logging.getLogger("agca.runtime_store")
REMOTE_URL_ENV = "AGCA_REMOTE_DATABASE_URL"
REMOTE_SCHEMA_ENV = "AGCA_REMOTE_DATABASE_SCHEMA"
REMOTE_TIMEOUT_ENV = "AGCA_REMOTE_CONNECT_TIMEOUT_SECONDS"
REMOTE_STATEMENT_TIMEOUT_ENV = "AGCA_REMOTE_STATEMENT_TIMEOUT_MS"
REMOTE_LOCK_TIMEOUT_ENV = "AGCA_REMOTE_LOCK_TIMEOUT_MS"
REMOTE_TCP_USER_TIMEOUT_ENV = "AGCA_REMOTE_TCP_USER_TIMEOUT_MS"
REMOTE_BATCH_SIZE_ENV = "AGCA_REMOTE_BATCH_SIZE"
REMOTE_INLINE_ENV = "AGCA_REMOTE_MIRROR_INLINE"
KEYCHAIN_SERVICE = "agca.remote-runtime-store"
REMOTE_RUNTIME_TABLES = frozenset(
    {
        "agent_run_state",
        "asset_risk_snapshots",
        "runtime_attestations",
        "crypto_news",
        "crypto_news_analysis",
        "decision_engine_snapshots",
        "data_validation_audit",
        "forecast_engine_predictions",
        "forecast_engine_runs",
        "forecast_drift_challenger_predictions",
        "forecast_feature_challenger_predictions",
        "forecast_learning_hypotheses",
        "forecast_learning_reviews",
        "external_trader_source_snapshots",
        "external_trader_trade_observations",
        "external_trader_learning_reviews",
        "external_trader_consensus_snapshots",
        "macro_observations",
        "macro_regime_snapshots",
        "onchain_observations",
        "paper_agent_daily_reports",
        "paper_agent_portfolio",
        "paper_agent_positions",
        "paper_agent_trade_learning_hypotheses",
        "paper_agent_trade_learning_reviews",
        "paper_agent_research_candidates",
        "paper_agent_forward_evaluations",
        "paper_agent_strategy_allocations",
        "paper_agent_strategy_allocation_history",
        "risk_gate_decisions",
        "risk_policy_snapshots",
        "social_sentiment_observations",
        "social_sentiment_snapshots",
        "whale_analysis_snapshots",
        "whale_events",
        "whale_evidence_snapshots",
        "whale_scan_state",
    }
)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WRITE_TABLE = re.compile(
    r"^\s*(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM|"
    r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?)\s+[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_retry_after = 0.0


@dataclass(frozen=True)
class MirrorResult:
    attempted: frozenset[str] = frozenset()
    succeeded: frozenset[str] = frozenset()
    error_type: str | None = None

    def succeeded_for(self, table: str) -> bool:
        return table in self.succeeded


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
        result = super().execute(sql, parameters)
        match = _WRITE_TABLE.match(sql)
        if match and sql.lstrip().upper().startswith("CREATE TABLE"):
            _ensure_change_triggers(self.connection, match.group(1))
        return result

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
        self.remote_mirror_result = MirrorResult()
        _ensure_outbox(self)
        for table in REMOTE_RUNTIME_TABLES:
            _ensure_change_triggers(self, table)
        pending = sqlite3.Connection.execute(
            self, "SELECT DISTINCT table_name FROM remote_mirror_outbox"
        ).fetchall()
        self._remote_dirty_tables.update(row[0] for row in pending)

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
            inline = os.getenv(REMOTE_INLINE_ENV, "1").strip().lower() not in {
                "0", "false", "no", "off"
            }
            if inline:
                self.remote_mirror_result = _mirror_after_local_commit(self, dirty_tables)
            else:
                self.remote_mirror_result = MirrorResult(
                    attempted=frozenset(dirty_tables), error_type="queued"
                )
            if inline and self.remote_mirror_result.error_type:
                # Keep failed work pending so a later commit can heal the mirror.
                self._remote_dirty_tables.update(self.remote_mirror_result.attempted)

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


def _ensure_outbox(local: sqlite3.Connection) -> None:
    """Create local-only durable state without recursively invoking mirroring."""
    sqlite3.Connection.executescript(local, """
        CREATE TABLE IF NOT EXISTS remote_mirror_outbox (
            table_name TEXT NOT NULL,
            pk_json TEXT NOT NULL,
            operation TEXT NOT NULL CHECK(operation IN ('UPSERT','DELETE')),
            queued_at TEXT NOT NULL,
            PRIMARY KEY(table_name,pk_json)
        );
        CREATE INDEX IF NOT EXISTS remote_mirror_outbox_queued
            ON remote_mirror_outbox(table_name,queued_at);
        CREATE TABLE IF NOT EXISTS remote_mirror_schema_state (
            table_name TEXT PRIMARY KEY,
            schema_sha256 TEXT NOT NULL,
            confirmed_at TEXT NOT NULL
        );
    """)


def _ensure_change_triggers(local: sqlite3.Connection, table: str) -> None:
    """Install durable row-level outbox triggers for a mirrored PK table."""
    if table not in REMOTE_RUNTIME_TABLES:
        return
    table_q = _quote(table)
    columns = sqlite3.Connection.execute(local, f"PRAGMA table_info({table_q})").fetchall()
    primary_keys = [
        column[1] for column in sorted(columns, key=lambda item: item[5]) if column[5]
    ]
    if not primary_keys:
        return
    trigger_marker = f"__local_triggers__{table}"
    trigger_signature = hashlib.sha256(
        json.dumps(primary_keys, separators=(",", ":")).encode("utf-8")
        + b":v2"
    ).hexdigest()
    if _local_schema_confirmed(local, trigger_marker, trigger_signature):
        return
    for operation, timing, prefix, suffix in (
        ("UPSERT", "AFTER INSERT", "NEW", "ai"),
        ("DELETE", "AFTER DELETE", "OLD", "ad"),
    ):
        trigger_q = _quote(f"agca_remote_mirror_{table}_{suffix}")
        values = ",".join(f'{prefix}.{_quote(name)}' for name in primary_keys)
        sqlite3.Connection.execute(local, f"""
            CREATE TRIGGER IF NOT EXISTS {trigger_q} {timing} ON {table_q}
            BEGIN
                INSERT INTO remote_mirror_outbox(table_name,pk_json,operation,queued_at)
                VALUES('{table}',json_array({values}),'{operation}',strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                ON CONFLICT(table_name,pk_json) DO UPDATE SET
                    operation=excluded.operation,queued_at=excluded.queued_at;
            END
        """)
    old_values = ",".join(f'OLD.{_quote(name)}' for name in primary_keys)
    new_values = ",".join(f'NEW.{_quote(name)}' for name in primary_keys)
    update_trigger_q = _quote(f"agca_remote_mirror_{table}_au")
    sqlite3.Connection.execute(local, f"DROP TRIGGER IF EXISTS {update_trigger_q}")
    sqlite3.Connection.execute(local, f"""
        CREATE TRIGGER {update_trigger_q} AFTER UPDATE ON {table_q}
        BEGIN
            INSERT INTO remote_mirror_outbox(table_name,pk_json,operation,queued_at)
            VALUES('{table}',json_array({old_values}),'DELETE',strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(table_name,pk_json) DO UPDATE SET
                operation=excluded.operation,queued_at=excluded.queued_at;
            INSERT INTO remote_mirror_outbox(table_name,pk_json,operation,queued_at)
            VALUES('{table}',json_array({new_values}),'UPSERT',strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(table_name,pk_json) DO UPDATE SET
                operation=excluded.operation,queued_at=excluded.queued_at;
        END
    """)
    sqlite3.Connection.execute(local, """
        INSERT INTO remote_mirror_schema_state(table_name,schema_sha256,confirmed_at)
        VALUES(?,?,?) ON CONFLICT(table_name) DO UPDATE SET
            schema_sha256=excluded.schema_sha256,confirmed_at=excluded.confirmed_at
    """, (trigger_marker, trigger_signature, _health_timestamp()))


def _local_schema_confirmed(local, marker: str, signature: str) -> bool:
    row = sqlite3.Connection.execute(
        local,
        "SELECT schema_sha256 FROM remote_mirror_schema_state WHERE table_name=?",
        (marker,),
    ).fetchone()
    return bool(row and row[0] == signature)


def _mirror_after_local_commit(local: sqlite3.Connection, tables: set[str]) -> MirrorResult:
    global _retry_after
    tables = tables & REMOTE_RUNTIME_TABLES
    if not tables:
        return MirrorResult()
    attempted = frozenset(tables)
    remote_url = _remote_url()
    if not remote_url or time.monotonic() < _retry_after:
        return MirrorResult(attempted=attempted, error_type="not_configured_or_backoff")

    schema = os.getenv(REMOTE_SCHEMA_ENV, "agca_runtime").strip()
    _quote(schema)
    timeout = max(1, int(os.getenv(REMOTE_TIMEOUT_ENV, "5")))
    statement_timeout_ms = max(
        1000, int(os.getenv(REMOTE_STATEMENT_TIMEOUT_ENV, "30000"))
    )
    lock_timeout_ms = max(1000, int(os.getenv(REMOTE_LOCK_TIMEOUT_ENV, "5000")))
    tcp_user_timeout_ms = max(
        1000, int(os.getenv(REMOTE_TCP_USER_TIMEOUT_ENV, "30000"))
    )
    attempts = max(1, int(os.getenv("AGCA_REMOTE_RETRY_ATTEMPTS", "3")))
    base_delay = max(0.0, float(os.getenv("AGCA_REMOTE_RETRY_BASE_SECONDS", "0.5")))
    import psycopg

    for attempt in range(1, attempts + 1):
        try:
            with psycopg.connect(
                remote_url,
                connect_timeout=timeout,
                tcp_user_timeout=tcp_user_timeout_ms,
            ) as remote:
                with remote.cursor() as cursor:
                    cursor.execute(
                        f"SET LOCAL statement_timeout = '{statement_timeout_ms}ms'"
                    )
                    cursor.execute(f"SET LOCAL lock_timeout = '{lock_timeout_ms}ms'")
                    infrastructure_confirmed = {}
                    schema_signature = hashlib.sha256(
                        f"{schema}|private-schema-v1".encode()
                    ).hexdigest()
                    if not _local_schema_confirmed(local, "__remote_schema__", schema_signature):
                        cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote(schema)}")
                        cursor.execute(
                            f"REVOKE ALL ON SCHEMA {_quote(schema)} FROM anon, authenticated"
                        )
                        cursor.execute(
                            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {_quote(schema)} "
                            "REVOKE ALL ON TABLES FROM anon, authenticated"
                        )
                        infrastructure_confirmed["__remote_schema__"] = schema_signature
                    health_signature = hashlib.sha256(b"remote_mirror_health_v1").hexdigest()
                    if not _local_schema_confirmed(
                        local, "__remote_mirror_health__", health_signature
                    ):
                        _create_remote_health_table(cursor, schema)
                        infrastructure_confirmed["__remote_mirror_health__"] = health_signature
                    completed = set()
                    applied = {}
                    confirmed_schemas = {}
                    for table in sorted(tables):
                        drained, applied_keys, signature = _mirror_table(
                            local, cursor, schema, table
                        )
                        applied[table] = applied_keys
                        if signature:
                            confirmed_schemas[table] = signature
                        if drained:
                            completed.add(table)
                    _write_remote_health(cursor, schema, completed)
            # Only acknowledge durable outbox work after the remote transaction commits.
            for table, keys in applied.items():
                sqlite3.Connection.executemany(local, """
                    DELETE FROM remote_mirror_outbox
                    WHERE table_name=? AND pk_json=?
                """, [(table, key) for key in keys])
            for table, signature in confirmed_schemas.items():
                sqlite3.Connection.execute(local, """
                    INSERT INTO remote_mirror_schema_state(
                        table_name,schema_sha256,confirmed_at
                    ) VALUES(?,?,?) ON CONFLICT(table_name) DO UPDATE SET
                        schema_sha256=excluded.schema_sha256,
                        confirmed_at=excluded.confirmed_at
                """, (table, signature, _health_timestamp()))
            for marker, signature in infrastructure_confirmed.items():
                sqlite3.Connection.execute(local, """
                    INSERT INTO remote_mirror_schema_state(
                        table_name,schema_sha256,confirmed_at
                    ) VALUES(?,?,?) ON CONFLICT(table_name) DO UPDATE SET
                        schema_sha256=excluded.schema_sha256,
                        confirmed_at=excluded.confirmed_at
                """, (marker, signature, _health_timestamp()))
            _retry_after = 0.0
            _safe_write_local_health(local, completed, "healthy", None)
            pending = tables - completed
            LOGGER.info(
                "Remote incremental mirror updated: completed=%s pending=%s",
                ",".join(sorted(completed)), ",".join(sorted(pending)),
            )
            return MirrorResult(
                attempted=attempted,
                succeeded=frozenset(completed),
                error_type="backlog_pending" if pending else None,
            )
        except Exception as exc:  # SQLite must remain available under every failure mode.
            if attempt < attempts:
                time.sleep(base_delay * (2 ** (attempt - 1)))
                continue
            retry_seconds = max(1, int(os.getenv("AGCA_REMOTE_RETRY_SECONDS", "60")))
            _retry_after = time.monotonic() + retry_seconds
            error_type = type(exc).__name__
            _safe_write_local_health(local, tables, "degraded", error_type)
            LOGGER.warning(
                "Remote runtime mirror failed after %s attempts; local SQLite commit is intact "
                "(tables=%s error_type=%s)",
                attempts, ",".join(sorted(tables)), error_type,
            )
            return MirrorResult(attempted=attempted, error_type=error_type)


def _safe_write_local_health(
    local, tables: set[str], status: str, error_type: str | None,
) -> None:
    """Health telemetry must never invalidate an already committed local write."""
    try:
        _write_local_health(local, tables, status, error_type)
    except sqlite3.Error as exc:
        LOGGER.warning(
            "Remote mirror health telemetry could not be stored; local commit "
            "remains authoritative (error_type=%s)",
            type(exc).__name__,
        )


def _health_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_local_health(local, tables: set[str], status: str, error_type: str | None) -> None:
    """Persist mirror health without recursively triggering another mirror."""
    now = _health_timestamp()
    sqlite3.Connection.execute(local, """
        CREATE TABLE IF NOT EXISTS remote_mirror_health (
            table_name TEXT PRIMARY KEY, status TEXT NOT NULL,
            last_attempt_at TEXT NOT NULL, last_success_at TEXT,
            error_type TEXT, consecutive_failures INTEGER NOT NULL DEFAULT 0
        )
    """)
    for table in tables:
        sqlite3.Connection.execute(local, """
            INSERT INTO remote_mirror_health(
                table_name,status,last_attempt_at,last_success_at,error_type,consecutive_failures
            ) VALUES(?,?,?,?,?,?)
            ON CONFLICT(table_name) DO UPDATE SET
                status=excluded.status,last_attempt_at=excluded.last_attempt_at,
                last_success_at=CASE WHEN excluded.status='healthy' THEN excluded.last_attempt_at
                                     ELSE remote_mirror_health.last_success_at END,
                error_type=excluded.error_type,
                consecutive_failures=CASE WHEN excluded.status='healthy' THEN 0
                    ELSE remote_mirror_health.consecutive_failures+1 END
        """, (table, status, now, now if status == "healthy" else None,
              error_type, 0 if status == "healthy" else 1))
    sqlite3.Connection.commit(local)


def _create_remote_health_table(cursor, schema: str) -> None:
    schema_q = _quote(schema)
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {schema_q}.remote_mirror_health (
            table_name TEXT PRIMARY KEY, status TEXT NOT NULL,
            last_attempt_at TEXT NOT NULL, last_success_at TEXT,
            error_type TEXT, consecutive_failures BIGINT NOT NULL DEFAULT 0
        )
    """)


def _write_remote_health(cursor, schema: str, tables: set[str]) -> None:
    schema_q = _quote(schema)
    now = _health_timestamp()
    for table in sorted(tables):
        cursor.execute(f"""
            INSERT INTO {schema_q}.remote_mirror_health(
                table_name,status,last_attempt_at,last_success_at,error_type,consecutive_failures
            ) VALUES(%s,'healthy',%s,%s,NULL,0)
            ON CONFLICT(table_name) DO UPDATE SET status='healthy',
                last_attempt_at=EXCLUDED.last_attempt_at,
                last_success_at=EXCLUDED.last_success_at,error_type=NULL,
                consecutive_failures=0
        """, (table, now, now))


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


def _schema_signature(columns) -> str:
    encoded = json.dumps(
        {
            "security_contract": "PRIVATE_RLS_V1",
            "columns": [
                (column[1], column[2], column[3], column[5]) for column in columns
            ],
        },
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _ensure_remote_table(local, remote_cursor, schema: str, table: str, columns) -> str | None:
    """Run DDL only until the local schema signature has been confirmed remotely."""
    table_q = _quote(table)
    schema_q = _quote(schema)
    signature = _schema_signature(columns)
    state = sqlite3.Connection.execute(
        local,
        "SELECT schema_sha256 FROM remote_mirror_schema_state WHERE table_name=?",
        (table,),
    ).fetchone()
    if state and state[0] == signature:
        return None
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
    remote_cursor.execute(
        f"ALTER TABLE {schema_q}.{table_q} ENABLE ROW LEVEL SECURITY"
    )
    remote_cursor.execute(
        f"REVOKE ALL ON TABLE {schema_q}.{table_q} FROM anon, authenticated"
    )

    return signature


def _mirror_table(local, remote_cursor, schema: str, table: str):
    """Mirror one outbox batch and return drain/applied/schema acknowledgement."""
    table_q = _quote(table)
    schema_q = _quote(schema)
    columns = sqlite3.Connection.execute(local, f"PRAGMA table_info({table_q})").fetchall()
    if not columns:
        return True, [], None
    names = [column[1] for column in columns]
    primary_keys = [column[1] for column in sorted(columns, key=lambda item: item[5]) if column[5]]
    if not primary_keys:
        raise RuntimeError(f"Mirrored table lacks a primary key: {table}")
    schema_signature = _ensure_remote_table(local, remote_cursor, schema, table, columns)

    batch_size = max(1, int(os.getenv(REMOTE_BATCH_SIZE_ENV, "100")))
    changes = sqlite3.Connection.execute(local, """
        SELECT pk_json,operation FROM remote_mirror_outbox
        WHERE table_name=? ORDER BY queued_at,pk_json LIMIT ?
    """, (table, batch_size)).fetchall()
    if not changes:
        return True, [], schema_signature

    pk_where = " AND ".join(f"{_quote(name)}=%s" for name in primary_keys)
    upsert_rows = []
    applied_keys = []
    for pk_json, operation in changes:
        key = json.loads(pk_json)
        if len(key) != len(primary_keys):
            raise RuntimeError(f"Invalid mirror key for {table}")
        if operation == "DELETE":
            remote_cursor.execute(
                f"DELETE FROM {schema_q}.{table_q} WHERE {pk_where}", tuple(key)
            )
        else:
            local_row = sqlite3.Connection.execute(
                local,
                f"SELECT {','.join(_quote(name) for name in names)} "
                f"FROM {table_q} WHERE {pk_where.replace('%s', '?')}",
                tuple(key),
            ).fetchone()
            if local_row is None:
                remote_cursor.execute(
                    f"DELETE FROM {schema_q}.{table_q} WHERE {pk_where}", tuple(key)
                )
            else:
                upsert_rows.append(local_row)
        applied_keys.append(pk_json)

    columns_sql = ",".join(_quote(name) for name in names)
    placeholders = ",".join(["%s"] * len(names))
    updates = [name for name in names if name not in primary_keys]
    conflict = ",".join(_quote(name) for name in primary_keys)
    if updates:
        action = "DO UPDATE SET " + ",".join(
            f"{_quote(name)}=EXCLUDED.{_quote(name)}" for name in updates
        )
    else:
        action = "DO NOTHING"
    if upsert_rows:
        remote_cursor.executemany(
            f"INSERT INTO {schema_q}.{table_q} ({columns_sql}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict}) {action}",
            upsert_rows,
        )

    total_pending = sqlite3.Connection.execute(
        local, "SELECT COUNT(*) FROM remote_mirror_outbox WHERE table_name=?", (table,)
    ).fetchone()[0]
    return total_pending <= len(applied_keys), applied_keys, schema_signature
