# Superseding hardening candidate — operator canary, not executed

Source repo: `kursadsalis-tech/AG-Crypto-Analyst`. Candidate branch:
`codex/agca-comprehensive-hardening`, includes all of PR #103 at
`b30f18e48e1b389620d56b51fefba4262d678da2`; main base
`56f85d11a757732bfbf34307ebf39aa3b23fb814`. Do not merge #103.
Use the exact 40-character reviewed head from the new PR/delivery, never a
moving branch tip. This document specifies the procedure; this development task
has performed no VM access, deployment, restart or operational canary.

## Preconditions and capture

Operator target: `agca-runtime`, GCP `project-6a717580-e37d-48f6-832`, zone
`us-east1-b`; user `kursadsalis`; checkout
`/home/kursadsalis/AG-Crypto-Analyst-cloud`; authoritative history
`/var/lib/agca/crypto_data.db`. Do not start a second lifecycle or restore an old
DB over current history. PAPER_ONLY / real_order_allowed=0 throughout.

1. Confirm current checkout is clean and record old exact checkout SHA, process
   SHA/PID, all five worker units and their drop-ins, NRestarts, health files,
   metadata cache provenance, and OPEN/PENDING IDs. Keep the record outside the
   Git checkout, private to the operator. Old checkout SHA alone is insufficient
   if a running worker imported another version: record both identities.
2. Preserve existing user/service overrides. Back up the five unit files and
   their drop-ins before the installer changes them. Record which timers are
   active/enabled. Use SQLite's online backup API for an optional consistent
   checkpoint; never copy a busy SQLite main file without its WAL protocol.
   The backup is forensic evidence, not rollback data: no history deletion.
3. Fetch the candidate branch; check its head equals the delivered exact SHA and
   that the SHA descends from the stated #103 parent. CI must pass that head.
   Refuse dirty checkout, source mismatch or a current authority other than the
   single realtime PAPER worker. No new capital or reset of learning history.

## Install the exact reviewed head

Set `REVIEWED_SHA` to the exact delivered 40-character head, and `ROLLBACK_SHA`
to the recorded current checkout SHA. The final delivery supplies the concrete
SHA; placeholders must not be executed.

```bash
cd /home/kursadsalis/AG-Crypto-Analyst-cloud
git diff --quiet
git diff --cached --quiet
git fetch origin codex/agca-comprehensive-hardening
test "$(git rev-parse FETCH_HEAD)" = "$REVIEWED_SHA"
git merge-base --is-ancestor b30f18e48e1b389620d56b51fefba4262d678da2 "$REVIEWED_SHA"
git switch --detach "$REVIEWED_SHA"
AGCA_DEPLOY_REEXEC=1 AGCA_EXPECTED_DEPLOY_SHA="$REVIEWED_SHA" \
  bash deploy/install_gce_free_worker.sh
```

Do not omit `AGCA_DEPLOY_REEXEC=1`: the ordinary installer intentionally updates
main and is not an unmerged-PR checkout selector. The exact-SHA assertion prevents
accidental deployment of another head.

The installer performs metadata preflight before worker restarts, starts the
behavior producer and waits at most 120 seconds for valid V2 data from the exact
clean source, then replaces the other processes. It requires compatible health
contracts, exact process SHA, clean process source, matching MainPID, fresh health,
60 continuous seconds of stable PID and NRestarts delta zero. All five workers
are checked. A failing preflight/acceptance is a failed canary, not permission to
repeat restarts. Units permit at most four starts per 300 seconds, separated by
30 seconds. Operator must investigate the recorded reason or roll back.

Read-only repeatable acceptance command (after installation, not a deploy):

```bash
.venv/bin/python -m scripts.runtime_acceptance runtime \
  --expected-sha "$REVIEWED_SHA" --database-path /var/lib/agca/crypto_data.db \
  --health-dir /var/lib/agca --ready-timeout 120 --window-seconds 60 --poll-seconds 5
```

## Bounded observations: 20 minutes, no forced trades

Record health every ten seconds and SQLite deltas for positions, entry rejection
reasons and position events. Do not scan all historical blobs in a hot loop.

- `ENTRY_PATH` READY with fresh V2 cache, correct producer/core versions and
  exact process SHA. Empty valid patterns are permitted; a zero-selection cycle
  still requires explicit reason/direction telemetry.
- `OPEN_POSITION_MANAGEMENT` READY, or IDLE only with no OPEN obligations.
  Current quotes/liquidity must be fresh for each managed route. A valid frozen
  rules hash is required for new fills. Legacy adoption must say
  `LEGACY_EXECUTION_RULES_ADOPTED`, never claim original fill-time provenance.
- New OPEN actual notional >=100 USDT. No sub-100 partial OPEN or cash debit.
  Replan and partial-fill cancellation reasons are durable. Reduce-only dust is
  exempt. Cash, current marked total/alt/single exposure and stop-risk stay within
  current policy; no duplicate symbol or second authority.
- On a newly observed dynamic symbol with 360 contiguous valid closed local
  Binance minute bars and sufficient measured six-hour liquidity, incremental
  risk appears on the next bounded support ticks, within the 180-second evidence
  window. Missing history is reported as real missing evidence; never fill it
  with invented candles or a 169-candle hot-path request. No suitable new symbol
  in the bounded window means this live criterion remains unobserved.
- Verify both LONG and SHORT stage/rejection telemetry, negative-edge veto and
  unchanged immediate stop/liquidation behavior. Three independent five-second
  opposing-flow confirmations remain necessary for flow-thesis invalidation.
  Never force a market move, opportunity or trade to meet acceptance counts.

## Controlled route-failure phase

Perform only while the operator is present, quote/liquidity paths are healthy
and required OPEN positions have verified frozen/adopted rules. First use a
USD-M OPEN with SPOT disabled; then a SPOT OPEN with USD-M disabled. If those
natural positions do not exist during the bounded canary, leave that live case
unverified rather than manufacturing exposure. Offline integration covers both.

For one route at a time, create a temporary runtime-only systemd drop-in for
`agca-paper-realtime-worker.service` that points that route's metadata path to a
nonexistent private canary file. For SPOT the environment key is
`AGCA_SPOT_EXCHANGE_METADATA_CACHE`; for USD-M it is
`AGCA_EXCHANGE_METADATA_CACHE`. Keep the other route's path unchanged. Do not
edit/delete canonical caches, Supabase data or SQLite history. Record the new
PID, restart the sole realtime worker once, and observe for 60 seconds:

- Worker remains alive; PID stable and NRestarts delta=0 after the deliberate
  restart; health source equals REVIEWED_SHA and remains clean.
- Only the disabled route is unavailable for new entries; the opposite route
  remains eligible. ENTRY_PATH reports its degraded route explicitly.
- OPEN management remains READY using frozen rules, including on the disabled
  route itself when an OPEN exists there. Fresh ticks continue; natural stop/
  liquidation/reduce-only exits require no metadata/helper access. A hold-only
  live window proves management liveness, not an observed protective execution.

Remove exactly the temporary canary drop-in, daemon-reload and restart the
realtime worker once. Run full runtime acceptance before the next route test.
Always install a shell EXIT/INT/TERM cleanup trap or equivalent operator rollback
before adding the drop-in. Never use `systemctl revert` to erase unrelated user
configuration. A failed manager state, stale OPEN quotes, restarts, mismatched
source, sub-100 OPEN or enabled real authority ends the canary immediately.

### Exact temporary fault commands

Use `ROUTE=SPOT` then, after restoring full acceptance, `ROUTE=USD_M_PERPETUAL`.
Before each command, verify every current OPEN has hash-valid frozen/adopted
rules and that an OPEN exists on the opposite route. Absence means the live
criterion remains unverified; do not manufacture a position. Run in the same
shell with REVIEWED_SHA pinned above.

```bash
(
set -euo pipefail
cd /home/kursadsalis/AG-Crypto-Analyst-cloud
test "$(git rev-parse HEAD)" = "$REVIEWED_SHA"
case "$ROUTE" in
  SPOT) CACHE_KEY=AGCA_SPOT_EXCHANGE_METADATA_CACHE ;;
  USD_M_PERPETUAL) CACHE_KEY=AGCA_EXCHANGE_METADATA_CACHE ;;
  *) exit 2 ;;
esac
DROPIN=/run/systemd/system/agca-paper-realtime-worker.service.d/90-agca-hardening-route-canary.conf
test ! -e "$DROPIN"
cleanup() {
  sudo rm -f "$DROPIN"
  sudo systemctl daemon-reload
  sudo systemctl restart agca-paper-realtime-worker.service
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
sudo mkdir -p /run/systemd/system/agca-paper-realtime-worker.service.d
printf '[Service]\nEnvironment=%s=/var/lib/agca/nonexistent-hardening-canary-%s.json\n' "$CACHE_KEY" "$ROUTE" | sudo tee "$DROPIN" >/dev/null
sudo systemctl daemon-reload
sudo systemctl restart agca-paper-realtime-worker.service
export ROUTE REVIEWED_SHA
.venv/bin/python - <<'PY'
import json, os, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
unit='agca-paper-realtime-worker.service'
def state():
    out=subprocess.check_output(['systemctl','show',unit,'-p','MainPID','-p','NRestarts'],text=True)
    return dict(line.split('=',1) for line in out.splitlines())
baseline=state()
assert int(baseline['MainPID']) > 0
ready_deadline=time.monotonic()+30
stable_at=None
while True:
    current=state()
    assert current == baseline, 'PID/restart changed unexpectedly'
    health=json.loads(Path('/var/lib/agca/paper-realtime-health.json').read_text())
    source=health.get('process_source',{})
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(health['at'])).total_seconds()
    ready=(source.get('git_sha') == os.environ['REVIEWED_SHA'] and source.get('git_dirty') is False
           and source.get('pid') == int(baseline['MainPID']) and 0 <= age <= 15
           and health.get('execution_mode') == 'PAPER_ONLY' and health.get('real_order_allowed') is False
           and health.get('ENTRY_PATH',{}).get('unavailable_routes') == [os.environ['ROUTE']]
           and health.get('OPEN_POSITION_MANAGEMENT',{}).get('status') == 'READY')
    if not ready:
        assert stable_at is None and time.monotonic() < ready_deadline, 'Route-isolation health failed'
    elif stable_at is None:
        stable_at=time.monotonic()
    elif time.monotonic()-stable_at >= 60:
        print(json.dumps({'route':os.environ['ROUTE'],'result':'ISOLATED_60_SECONDS','identity':current}))
        break
    time.sleep(5)
PY
)
.venv/bin/python -m scripts.runtime_acceptance runtime --expected-sha "$REVIEWED_SHA"
```

## Rollback and verdict

1. Remove only canary-owned runtime drop-ins; restore the previously captured
   unit files/overrides and timer enablement. Do not delete new history/events.
2. Verify both fresh metadata routes before starting legacy code, which may lack
   frozen-rule fallback. Switch checkout to recorded ROLLBACK_SHA, daemon-reload,
   restart behavior/technical/observer/trade support and then the sole realtime
   worker. Regenerate the old producer's cache with its own worker; never edit
   cache version fields by hand. Do not run the old self-updating installer.
3. Confirm previous intended process source, clean checkout, stable PID/no restart
   increase, fresh per-OPEN quotes, valid active manager and PAPER_ONLY. If the
   previous code cannot manage current obligations, stop new entries and keep
   the proven protective manager; do not declare rollback safe from Git alone.

Record exact head, start/end times in Istanbul plus UTC, per-service evidence,
route fault outcomes, actual fills/cancels, unresolved criteria and rollback
result. Software CI passing means code is reviewable; operational merge approval
requires these acceptance results. Economic performance requires subsequent
natural forward trades across market regimes and net-after-cost analysis.
