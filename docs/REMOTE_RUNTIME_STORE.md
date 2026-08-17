# AGCA Remote Runtime Store

AGCA continues to use `crypto_data.db` as its authoritative local runtime
database. An optional PostgreSQL/Supabase connection enables best-effort
dual-write without changing the existing SQLite data contracts.

## Setup

1. Create a PostgreSQL database (a Supabase project's direct PostgreSQL URL is
   supported).
2. Install the optional runtime dependency:

   ```bash
   python3 -m pip install -r requirements-runtime.txt
   ```

3. Copy the relevant values from `.env.example` into the environment used by
   the AGCA process or LaunchAgent. Do not commit the real URL or password.
4. Set `AGCA_REMOTE_DATABASE_URL`. SSL can be required in the URL with
   `?sslmode=require`.

The default remote schema is `agca_runtime`. AGCA creates the schema, tables,
and newly discovered columns automatically. Existing SQLite rows in a table
are included the first time that table is changed and committed while remote
storage is enabled.

## Failure behavior

- SQLite commits first and remains the source of truth.
- Missing configuration means normal SQLite-only operation.
- A missing PostgreSQL driver, timeout, invalid credentials, or unavailable
  remote database is logged as a warning and does not fail the local cycle.
- Failed remote connections enter a short retry cooldown (60 seconds by
  default) to avoid slowing every step of a live cycle.
- Connection URLs and database error details are not printed by this layer.

This is deliberately a transition design. Remote writes are synchronous,
best-effort mirrors; they do not authorize remote reads for trading decisions
and do not change any SHADOW/production safety policy.
