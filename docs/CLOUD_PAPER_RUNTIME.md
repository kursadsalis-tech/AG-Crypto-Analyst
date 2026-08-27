# AGCA Cloud Paper Runtime

## Guaranteed properties

- The worker is an OCI/Docker process and does not depend on a logged-in Mac.
- `AGCA_EXECUTION_MODE=PAPER_ONLY` is mandatory. There is no exchange order
  adapter, API-key reader, or live-order route in this runtime.
- `crypto_data.db`, model artifacts, reports, and health state live under the
  mounted `/var/lib/agca` persistent volume.
- A non-blocking volume lock prevents two workers sharing that volume from
  opening duplicate paper positions.
- The live product cycle runs once per five-minute bucket. The hourly forecast,
  settlement, learning, and paper-agent job runs once after minute `:02`.
- PostgreSQL/Supabase remains the operational audit mirror; the mounted SQLite
  database remains authoritative for causal market history and decisions.

## Build and run

```bash
docker build -t agca-paper-worker .
docker run --restart unless-stopped \
  --env-file /secure/agca.env \
  --mount type=volume,src=agca-state,dst=/var/lib/agca \
  agca-paper-worker
```

The first start seeds the persistent database and reports from the image. Later
deployments retain the volume copy. `worker-health.json` is the health/readiness
contract used by the container health check.

## Required deployment configuration

The cloud host must provide a persistent volume and outbound HTTPS. Set the
Supabase PostgreSQL connection URL as a secret, never in the image or Git.
Choose `AGCA_PAPER_MARKET=USD_M_PERPETUAL` for the existing LONG/SHORT policy or
`SPOT` for LONG-only behavior. Defaults model taker fees, spread, adverse
latency, slippage, volume participation, partial fills, minimum notional, and
perpetual funding conservatively; venue metadata should be refreshed before a
claim of exchange-grade parity.

### Supabase with Data API disabled

Supabase may keep PostgREST alive after the Data API is disabled and repeatedly
log `schema "pg_pgrst_no_exposed_schemas" does not exist`. The documented
fail-closed sentinel configuration is:

```sql
create schema pgrst_no_exposed_schemas;
revoke all on schema pgrst_no_exposed_schemas
  from public, anon, authenticated, service_role;
alter role authenticator
  set pgrst.db_schemas = 'pgrst_no_exposed_schemas';
notify pgrst, 'reload config';
```

This does not make PostgreSQL read-only and does not expose `agca_runtime`.
Before applying it, confirm the Data API is intentionally disabled. To reverse
the role override, run `alter role authenticator reset pgrst.db_schemas;` and
reload PostgREST; drop the empty sentinel schema only after the reset is active.

## Operational limitation

This package makes the runtime Mac-independent, but a provider deployment and
its credentials are external state. Until the image is deployed on a cloud
host with the persistent volume and Supabase secret, the active process remains
the Mac LaunchAgent. OHLCV-based simulated fills are intentionally adverse but
cannot reproduce an historical order book exactly.
