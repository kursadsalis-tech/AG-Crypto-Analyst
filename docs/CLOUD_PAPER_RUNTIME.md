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

## Market behavior and exact-macro observers — approval-gated deployment

The repository contains two prepared but separately approval-gated observers:

- `agca-market-behavior-observer.service/.timer` collects compact closed-minute
  market behavior without raw payload retention.
- `agca-exact-macro-observer.service/.timer` refreshes the official FOMC
  calendar at most daily on normal days and once per minute only inside the
  15-minute-before/90-minute-after decision window. It stores official release
  time and actual AGCA observation time separately.

Do not install or start these units without the user's explicit approval. When
approval is given, first create a rollback directory named with the local
checkpoint, then copy the four unit files and the changed Python sources. The
intended unit installation commands are:

```bash
python3 scripts/observer_deployment_readiness.py
```

This preflight must return `failure_count=0`,
`ready_for_controlled_cloud_install=true`, `paper_authority_safe=true`, and
`cloud_install_performed=false`. Its SHA-256 map covers both observer entry
points and their local source/database dependencies; compare the same files
after transfer before running either service.

```bash
sudo install -m 0644 deploy/agca-market-behavior-observer.service /etc/systemd/system/
sudo install -m 0644 deploy/agca-market-behavior-observer.timer /etc/systemd/system/
sudo install -m 0644 deploy/agca-exact-macro-observer.service /etc/systemd/system/
sudo install -m 0644 deploy/agca-exact-macro-observer.timer /etc/systemd/system/
sudo install -m 0644 deploy/agca-cloud-attestation.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Run each service once before enabling timers. A successful controlled check
must prove:

1. both services exit successfully under `AGCA_EXECUTION_MODE=PAPER_ONLY`;
2. `/var/lib/agca/market-behavior-health.json` and
   `/var/lib/agca/exact-macro-health.json` are mode `0600` and hash-valid;
3. SQLite contains the four local-only tables
   `market_behavior_minute_features`, `causal_minute_external_evidence`, and
   `exact_time_macro_events`, plus `behavior_event_forward_outcomes`;
4. none of those tables enters the Supabase outbox/remote allowlist;
5. a new cloud attestation contains both observer blocks, reports
   `may_send_real_orders=false`, and retains `PAPER_ONLY`;
6. the primary worker and its hourly/PAPER jobs remain healthy.

The market-behavior service also has an operational failure path:

- `agca-market-behavior-observer-failure-alert.service` is triggered by
  `OnFailure` and records/sends one immediate service-failure alert;
- `agca-observer-health-monitor.timer` checks once per minute and opens one
  deduplicated alert for missing/invalid/stale health or three consecutive
  unhealthy checks, then emits one recovery event;
- delivery uses the existing optional Telegram environment variables. If they
  are absent, the private state file still records delivery failure and the
  separate Codex heartbeat remains the user-facing alarm route.

Install and enable the monitor with the observer deployment:

```bash
sudo install -m 0644 deploy/agca-market-behavior-observer-failure-alert.service /etc/systemd/system/
sudo install -m 0644 deploy/agca-observer-health-monitor.service /etc/systemd/system/
sudo install -m 0644 deploy/agca-observer-health-monitor.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agca-observer-health-monitor.timer
```

Only after those checks may the approved timers be enabled:

```bash
sudo systemctl enable --now agca-market-behavior-observer.timer
sudo systemctl enable --now agca-exact-macro-observer.timer
```

Rollback is fail-closed and does not delete accumulated SQLite evidence:

```bash
sudo systemctl disable --now agca-market-behavior-observer.timer
sudo systemctl disable --now agca-exact-macro-observer.timer
sudo systemctl daemon-reload
```

Restore the checkpoint-named rollback copies of changed source/unit files,
restart only `agca-cloud-worker.service` and `agca-cloud-attestation.timer`, and
verify a fresh hash-valid PAPER_ONLY attestation. Do not delete observer tables
or evidence during rollback.
