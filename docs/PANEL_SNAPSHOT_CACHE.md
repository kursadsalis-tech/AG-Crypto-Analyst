# Panel snapshot cache — 2026-09-28

Scope: read-only panel computation and diagnostic feedback only. This PR must
remain **unmerged**. No VM deployment, trade, policy change, migration or worker
restart was performed. The commands below are for a separately reviewed release.
They supersede the older panel audit's installation/realtime-restart suggestions
for this task: do not restart realtime, observer or technical workers.

## Recovery and production evidence

Continuation of Work thread `01a0e7c0-b473-7381-b181-b23ece755c6b`.
No recoverable prior checkout, worktree or unstaged patch was accessible in this
Work environment. Personal history referenced the Mac path
`/Users/elifzilan/Documents/AG-Crypto-Analyst/.worktrees/...`, which is not mounted
here. All 103 published branch names, current refs, recent commits and seven
historical open PRs were inspected; no panel-cache branch/commit/PR was found.
The existing panel audit #101 is already merged. Canonical main was verified as
`b878c17553f8d53ae1e92aa858b50d1d0fc945e9` and its exact Git objects/source were
materialized through the connected GitHub API. Local reports are sparse; none
were changed. Old Mac dirty checkouts and historical PRs remain a separate TODO.

The user supplied these production observations; they were **not independently
rerun** from this Work environment:

| Observation | Evidence |
| --- | --- |
| Panel hard quota | `CPUQuota=25%`, `CPUQuotaPerSecUSec=250ms` |
| Direct `control_center.server.snapshot()` | 1.932 s with `AGCA_DATABASE_PATH=/var/lib/agca/crypto_data.db` |
| Service `/api/v1/overview` | 20 s timeout |
| `cpu.stat` before | `nr_throttled=6753`, `throttled_usec=752200967` |
| After one overview request | `nr_throttled=6951`, `throttled_usec=784407619` |
| Delta | 198 throttle events, 32,206,652 us (32.207 s) throttled |
| `/healthz` | about 0.97 s |
| Clients | Mac and phone poll every 2 s; each request previously recomputed the snapshot |

The realtime worker was reported healthy, with `OPPORTUNITY_SCAN`,
`CONTINUE_SCANNING`, `deep_analysis_slot_limit=12`, and
`deep_shortlist_is_trade_universe=False`. These are supplied live observations,
not a reason to alter the healthy trading path. Throttled cgroup time is not
snapshot CPU time; the 1.932 s direct elapsed time alone cannot prove a new
minimum quota or sustained production latency.

## Behavior and observability

Each `PanelHTTPServer` owns one request-driven cache. A miss atomically claims
the refresh slot, then one daemon thread runs the existing read-only
`snapshot()`. All request threads immediately share the previous encoded
success, or receive 503 while no success exists. There is no polling timer or
second trading/learning loop. No clients means no new computations.

The snapshot is JSON-encoded once per success. Request handling only encodes
small cache telemetry; it does not copy/serialize the full data model again.
One immutable success, one in-flight computation and fixed-size counters are
retained. No client IDs, request history, exception text or unbounded metric
arrays are stored. Counters saturate at `2**63-1`.

| State | HTTP | Meaning |
| --- | --- | --- |
| `UNAVAILABLE` | 503, `Retry-After: 2` | No successfully computed/encoded snapshot yet |
| `CURRENT` | 200 | Last successful computation completed less than 2 s ago |
| `STALE` | 200 | Last success expired; refresh is running, failed, or awaiting retry |

`snapshot_cache` is present in every overview response, including 503. The
`X-AGCA-Snapshot-Status` header agrees with it and `Cache-Control: no-store`
prevents browser/proxy caching. `/healthz` reports the same cache diagnostics
without starting a computation; its `status=ok` means HTTP process liveness,
not snapshot readiness or trader health.

- `age_seconds`: monotonic time since successful compute/encoding **completion**.
- `snapshot_age_seconds`: monotonic time since that successful compute **started**;
  includes compute time. Separate source timestamps/quote ages remain unchanged.
- `captured_at`, `last_success_at`: UTC start/completion of the retained success.
- `ttl_seconds=2`, `refresh_in_progress`, `refresh_elapsed_seconds`.
- `compute_duration_seconds`, `compute_cpu_seconds`: latest attempt's wall/thread
  CPU time, including encoding. These describe the last attempt, even if it failed.
- `hits`: CURRENT responses; `misses`: responses with no CURRENT snapshot;
  `stale_served`: subset of misses returning a previous success.
- `refresh_attempts`, `refresh_failures`, `generation`, and a fixed error code.

On any compute/serialization exception the last success, its timestamps and
generation are retained. It is served only as STALE. Failed/cold retries are
spaced by 2 s from completion, independently of request volume. A stuck read
keeps its one refresh slot; no timeout spawns a replacement computation.
The panel can be restarted independently if a read never returns.

CURRENT is a cache-completion label, **not proof of live underlying quotes**.
The UI shows both ages, expiry, refresh state, duration and hit/miss. STALE
replaces the top status/mode and removes green live labels from retained rows.
A local expiry timer marks a previously CURRENT screen STALE even if fetch
stalls; a successful refresh restores normal labels. Completion-to-completion
2 s UI polling and the mismatch payload/endpoint remain unchanged. Diagnostic
feedback may take until the next successful snapshot to appear in the incident
table; POST is never replayed by cache refresh.

## Bounded resource choice and remaining risk

Proposed starting ceiling: **CPUQuota=50%, CPUWeight=20, Nice=10**; keep the
existing 256 MiB memory limit, loopback binding, PAPER_ONLY, read-only runtime
mount and separate diagnostic state directory. The realtime unit remains
unchanged at its existing priority.

50% is the smallest proposed upward step here with useful headroom over the
proven failing 25%; it is **not a measured production minimum**. Single-flight
removes client-count multiplication, encoding reuse removes repeated work, and
lower CPU weight/nice priority favors trading under contention. A quota is a
hard fraction of one CPU, not a percentage of all VM CPUs. Upstream definitions:
[systemd resource control](https://github.com/systemd/systemd/blob/main/man/systemd.resource-control.xml)
and [process scheduling](https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml).

Keep the hard quota. Accept 50% only if the gates below pass; do not call it
production-safe based on local fixtures. A later downward trial can establish
a lower safe quota, and a failed 50% trial needs measured compute CPU/wall time
and worker contention before any reviewed increase. Do not silently uncap CPU,
change worker priorities, alter freshness gates, or buy resources. Persistent
slow SQL can still cause long STALE periods; this PR deduplicates reads without
changing queries, table indexes or trading logic.

## Exact live acceptance commands

Run on the VM only after review and a separately authorized merge/release.
Keep this PR unmerged now. Fill in the reviewed **main** release SHA (a squash
merge changes the SHA). Preserve the current shared checkout and all dirty work.

### 1. Record process identities; install only the panel from an isolated release

```bash
set -euo pipefail
cd /home/kursadsalis/AG-Crypto-Analyst-cloud
AGCA_PANEL_COMMIT=REPLACE_WITH_REVIEWED_MAIN_RELEASE_SHA
export AGCA_PANEL_PROBE_DIR
AGCA_PANEL_PROBE_DIR=$(mktemp -d /tmp/agca-panel-acceptance.XXXXXX)
AGCA_PANEL_RELEASE="/home/kursadsalis/AGCA-panel-${AGCA_PANEL_COMMIT}"
systemctl show agca-paper-realtime-worker.service agca-market-behavior-observer.service agca-technical-kline-worker.service \
  -p Id -p MainPID -p ExecMainStartTimestamp -p NRestarts > "$AGCA_PANEL_PROBE_DIR/workers.before"
systemctl cat agca-live-panel.service > "$AGCA_PANEL_PROBE_DIR/panel.before.txt"
sudo cp -p /etc/systemd/system/agca-live-panel.service "$AGCA_PANEL_PROBE_DIR/panel.unit.before"
git fetch --no-tags origin main
git merge-base --is-ancestor "$AGCA_PANEL_COMMIT" origin/main
git worktree add --detach "$AGCA_PANEL_RELEASE" "$AGCA_PANEL_COMMIT"
sed "s|^WorkingDirectory=.*|WorkingDirectory=$AGCA_PANEL_RELEASE|" \
  "$AGCA_PANEL_RELEASE/deploy/agca-live-panel.service" > "$AGCA_PANEL_PROBE_DIR/panel.unit"
sudo install -m 0644 "$AGCA_PANEL_PROBE_DIR/panel.unit" /etc/systemd/system/agca-live-panel.service
sudo systemctl daemon-reload
sudo systemctl restart agca-live-panel.service
systemctl show agca-live-panel.service -p ActiveState -p MainPID -p WorkingDirectory \
  -p ExecStart -p CPUQuotaPerSecUSec -p CPUWeight -p Nice -p MemoryMax
curl --fail --max-time 2 http://127.0.0.1:8787/healthz
```

Require the isolated `WorkingDirectory`, `CPUQuotaPerSecUSec=500ms`,
`CPUWeight=20`, `Nice=10`, `MemoryMax=268435456`. Inspect any conflicting drop-in;
do not remove it blindly. The existing venv's Python remains the interpreter.
No shared checkout switch/pull, schema operation, general deploy script,
worker restart, mask, Caddy or Tailscale change is needed.

### 2. Run a bounded two-client probe, with an idle worker baseline

Close phone/Mac panel tabs during this probe so the baseline has no overview
clients. This one-off script makes **GET requests only**, reads existing JSON
and cgroup files, and writes its report under the temporary probe directory.
It never opens the production database or calls a trading/feedback endpoint.

```bash
python3 - <<'PY'
import concurrent.futures, json, math, os, pathlib, statistics, subprocess, time
import urllib.error, urllib.request

root = pathlib.Path('/var/lib/agca')
out = pathlib.Path(os.environ['AGCA_PANEL_PROBE_DIR'])
base = 'http://127.0.0.1:8787'
def get(path):
    start = time.monotonic()
    try:
        response = urllib.request.urlopen(base + path, timeout=2)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        payload = json.load(response)
        return response.status, payload, time.monotonic() - start
def read(name):
    return json.loads((root / name).read_text())
def worker_sample():
    health, market = read('paper-realtime-health.json'), read('paper-realtime-market-state.json')
    assert health['execution_mode'] == 'PAPER_ONLY' and health['real_order_allowed'] is False
    assert market['execution_mode'] == 'PAPER_ONLY' and market['real_order_allowed'] is False
    stamp = time.time() * 1000
    quotes = {r['symbol']: stamp - r['quote_timestamp_ms'] for r in market.get('live_market', [])
              if r.get('symbol') in ('BTCUSDT', 'ETHUSDT')
              and r.get('market_type') == 'USD_M_PERPETUAL' and r.get('quote_timestamp_ms')}
    ingest = health.get('quote_ingest', {}).get('USD_M_PERPETUAL', {})
    return {'quotes_ms': quotes, 'receive_lag_ms': ingest.get('last_receive_lag_ms'),
            'worker_status': health.get('status'), 'source': health.get('process_runtime_source')}
def cpu_stat():
    group = subprocess.check_output(['systemctl', 'show', 'agca-live-panel.service',
                                     '-p', 'ControlGroup', '--value'], text=True).strip()
    return {k: int(v) for k, v in (line.split() for line in
            (pathlib.Path('/sys/fs/cgroup') / group.lstrip('/') / 'cpu.stat').read_text().splitlines())}
def p95(values):
    return sorted(values)[max(0, math.ceil(len(values) * .95) - 1)] if values else None

idle = []
for _ in range(15):
    idle.append(worker_sample())
    time.sleep(2)
before = cpu_stat()
samples, workers, health_latencies = [], [], []
started = time.monotonic()
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    for tick in range(60):
        wait = started + tick * 2 - time.monotonic()
        if wait > 0: time.sleep(wait)
        futures = [pool.submit(get, '/api/v1/overview') for _ in range(2)]
        pair = [f.result() for f in futures]
        for status, data, latency in pair:
            c = data['snapshot_cache']
            assert status == (503 if c['state'] == 'UNAVAILABLE' else 200)
            if c['state'] == 'CURRENT':
                assert c['age_seconds'] <= c['ttl_seconds'] and c['last_error'] is None
            samples.append({'status': status, 'latency': latency, 'cache': c})
        health_latencies.append(get('/healthz')[2])
        workers.append(worker_sample())
after = cpu_stat()
duration = time.monotonic() - started
last = get('/healthz')[1]['snapshot_cache']
ready = [s for s in samples if s['status'] == 200]
major_gates = {}
for symbol in ('BTCUSDT', 'ETHUSDT'):
    baseline = [x['quotes_ms'][symbol] for x in idle if symbol in x['quotes_ms']]
    loaded = [x['quotes_ms'][symbol] for x in workers if symbol in x['quotes_ms']]
    major_gates[symbol] = {'baseline_p95_ms': p95(baseline), 'loaded_p95_ms': p95(loaded),
        'pass': len(baseline) == len(idle) and len(loaded) == len(workers)
                and min(loaded) >= 0 and p95(loaded) <= 1500
                and p95(loaded) <= p95(baseline) + 250}
lag = [x['receive_lag_ms'] for x in workers if x['receive_lag_ms'] is not None]
delta = {k: after[k] - before[k] for k in before}
gates = {
    'ready_after_warmup': all(s['status'] == 200 for s in samples[10:]),
    'shared_computation': 1 < last['refresh_attempts'] <= math.ceil(duration / 2) + 1,
    'no_refresh_failures': last['refresh_failures'] == 0,
    'overview_p95_under_500ms': bool(ready) and p95([s['latency'] for s in ready]) <= .5,
    'health_p95_under_500ms': p95(health_latencies) <= .5,
    'compute_under_5s': bool(ready) and max(s['cache']['compute_duration_seconds'] for s in ready) <= 5,
    'success_age_under_10s': bool(ready) and max(s['cache']['age_seconds'] for s in ready) <= 10,
    'receive_lag_under_1s': len(lag) == len(workers) and min(lag) >= 0 and p95(lag) <= 1000,
    'major_quotes': all(x['pass'] for x in major_gates.values()),
    'worker_source_unchanged': all(x['source'] == idle[0]['source'] for x in workers),
}
report = {'gates': gates, 'passed': all(gates.values()), 'duration_seconds': duration,
    'http_requests': len(samples), 'cache': last, 'cpu_stat_delta': delta,
    'overview_p95_seconds': p95([s['latency'] for s in ready]),
    'health_p95_seconds': p95(health_latencies), 'major_quotes': major_gates,
    'receive_lag_p95_ms': p95(lag), 'samples': samples, 'workers': workers, 'idle': idle}
(out / 'acceptance.json').write_text(json.dumps(report, indent=2))
print(json.dumps({k: v for k, v in report.items() if k not in ('samples','workers','idle')}, indent=2))
assert report['passed'], 'Live acceptance failed; inspect the report before resource changes'
PY
systemctl show agca-paper-realtime-worker.service agca-market-behavior-observer.service agca-technical-kline-worker.service \
  -p Id -p MainPID -p ExecMainStartTimestamp -p NRestarts > "$AGCA_PANEL_PROBE_DIR/workers.after"
diff -u "$AGCA_PANEL_PROBE_DIR/workers.before" "$AGCA_PANEL_PROBE_DIR/workers.after"
```

The 1.5 s quote ceiling is an acceptance tolerance around the existing ~1 s
target, not a changed trading gate; no >250 ms p95 regression is accepted.
Missing BTC/ETH evidence is inconclusive/failing, never invented. Worker process
identities must remain unchanged. Inspect `cpu_stat_delta` together with
compute CPU/wall time and worker latency; nonzero throttling is expected under
a hard quota and alone is not failure. Compare against the supplied 198/32.207 s
single-request incident, allowing for the different 120 s sample duration.

The deterministic local/CI tests prove exact one-compute concurrency and
failed-refresh semantics. Do not break production DB access to inject a failure.
Open the Tailscale URL on Mac/phone after the probe. Verify cache telemetry,
STALE warning during expiry/refresh, its recovery, and existing mismatch-button
quote attributes. Diagnostic POST remains covered by offline HTTP/DOM tests;
no synthetic live feedback or trades are needed for acceptance.

### 3. Rollback if the panel trial fails

```bash
sudo install -m 0644 "$AGCA_PANEL_PROBE_DIR/panel.unit.before" /etc/systemd/system/agca-live-panel.service
sudo systemctl daemon-reload
sudo systemctl restart agca-live-panel.service
```

This returns to the previous panel unit, including its old quota/path. It does
not repair the old timeout. Retain the failed acceptance report and isolated
worktree for diagnosis; do not clean historical checkouts/PRs in this task.
