# Stability freeze — separately authorized PAPER re-canary

PR #104 / `codex/agca-comprehensive-hardening` remains **open and unmerged**.
PR #103 remains superseded and unmerged. Pin the exact reviewed 40-character
head from PR #104/delivery; never use a moving branch tip. This source task
permits no VM/GCP access, deployment, systemd operation, production database
operation, operational canary or real order. The procedure below is for a
subsequent, explicitly authorized operator run.

## Why acceptance was reset

The user-reported canary at `2573de648edf229f09fbab2b68ccef2fdbe2828a` failed:

- Behavior first-cache production exceeded the installer's 120-second deadline,
  then produced an accepted cache at the exact SHA. Time elapsed alone did not
  distinguish slow progress from failure.
- Remaining manual restarts contended for the same SQLite writer. A busy manager
  UPDATE escaped through `process_state -> decision_loop -> asyncio.gather` and
  killed the canonical worker. Systemd restarted it after 31 seconds;
  `NRestarts=1` invalidated acceptance.
- Behavior/trade/technical health was RUNNING, stale or missing success during
  cold start. A later realtime restart reached ENTRY_PATH and
  OPEN_POSITION_MANAGEMENT READY/PAPER_ONLY/ZEC HOLD. That recovery does not
  erase the lifecycle death.

These are supplied observations, not independently refreshed live evidence.
Previous green software CI and short HOLD windows are not a stability verdict.
The prior STARTING missing-field and exact frozen technical stop fixes remain
required, as do actual new OPEN >=100 USDT and frozen execution rules.

## Preconditions and evidence

Operator target remains `agca-runtime`, project
`project-6a717580-e37d-48f6-832`, zone `us-east1-b`; checkout
`/home/kursadsalis/AG-Crypto-Analyst-cloud`; authoritative history
`/var/lib/agca/crypto_data.db`. Keep PAPER_ONLY and real_order_allowed=false.

1. Confirm final-head full regression, PAPER integration and CI; capture clean
   source SHA, process SHA/PID/start time, NRestarts, service files/drop-ins,
   timer enablement, health, cache provenance, OPEN/PENDING IDs and unmanaged
   count. Evidence goes outside the checkout and must not contain credentials.
2. Preserve service overrides and SQLite history. A forensic backup, if needed,
   uses SQLite's online backup API. Never copy only a busy main database file,
   replace live history with an old backup, remove WAL files, or reset learning.
3. Check exact-head ancestry from #103
   `b30f18e48e1b389620d56b51fefba4262d678da2`, one canonical trade authority,
   and no outstanding schema/integrity/authority error. An absent natural test
   opportunity remains unobserved; do not create or close positions for a test.
4. Capture rollback source and units. A successful code checkout alone does not
   prove an older worker can protect current obligations.

## Readiness and installation contract

Use the reviewed installer's exact-SHA mode (`AGCA_DEPLOY_REEXEC=1` with
`AGCA_EXPECTED_DEPLOY_SHA` equal to the reviewed head). Its ordinary main-update
mode is not a selector for this unmerged PR. Operator authorization is required
before any invocation; no installer was executed during source development.

The installer serializes canonical producer startup: behavior/cache, technical,
observer, then trade. Each producer must prove current systemd PID, process-bound
exact SHA, clean source, matching contract and a fresh current-process success
before the next writer starts. Behavior additionally needs its matching cache.
Metadata preflight and all required producer gates precede realtime startup.
Supporting timers start after the gates, rather than adding a cold-start writer
storm.

Readiness uses `--ready-timeout 120` as a **no-useful-progress** budget and
`--max-ready-seconds 1800` as a hard total startup ceiling. Current-PID
STARTING/RUNNING may wait for contracts/success while actual `progress.sequence`
advances. A timestamp-only heartbeat is not work progress. Wrong populated
SHA/dirty/version, fatal health, PID replacement or restart-count change fail
immediately. No-progress and hard ceiling failures stop the sequence; they do
not trigger blind repeated restarts. The bounds must be reviewed against measured
phase durations, not raised to hide a stuck phase.

The runtime stability window starts only after all five required services meet
their full contract. It requires stable PIDs, NRestarts delta zero, fresh producer
success, cache/metadata readiness and explicit PAPER_ONLY/false authority. If a
producer loses readiness during that window, acceptance fails. STARTING missing
fields may wait but never count toward stability.

## Controlled faults before the soak

Run faults only with separate authorization, an operator present, healthy
quotes, verified frozen/adopted rules for every existing OPEN, and a cleanup
plan. Run one fault at a time. Stop on unmanaged OPEN, fatal DB health, stale
protective quotes, unexpected restart/PID change or authority violation. Restore
normal readiness after each case; then start a **new** 24-hour baseline.

### Metadata/helper outage

Temporarily make one metadata/helper input unavailable through a canary-owned
runtime override. Do not delete or edit canonical cache/history. Verify that
entries on the affected route degrade while existing OPEN protection continues
from hash-valid frozen/adopted execution rules. Test each route only when natural
obligations exist; a missing route case remains unobserved. Existing exact frozen
stops, immediate stop/liquidation and reduce-only behavior must stay intact.

A fault requiring a deliberate worker restart happens **before** the soak. Record
that deliberate operation separately, remove only the canary-owned override,
restore readiness and establish a fresh baseline. Never erase user drop-ins with
`systemctl revert` or count a planned restart as continuous operation.

### SQLite contention (no data mutation)

First run the focused synthetic contention tests on a disposable database. For
an authorized runtime test, use a separate local SQLite connection to the same
DB, acquire `BEGIN IMMEDIATE`, hold it for a bounded interval (initially <=1
second), and always `ROLLBACK`/close in a `finally` block. The injected connection
executes **no INSERT, UPDATE, DELETE, DDL, checkpoint, VACUUM or journal change**.
The acquisition itself has a short timeout; failure to acquire aborts that trial.
Use an outer process timeout so a broken harness cannot retain the lock.

Capture per-symbol/per-path SQLITE_BUSY degradation, manager invocation priority,
continued websocket freshness, stable realtime PID/NRestarts, rollback and the
next fresh tick's recovery. Compare event/fill/exit IDs and accounting deltas for
duplicates. A test window with no affected tick is inconclusive, not a pass.
Do not enlarge the lock duration merely to force an event; the offline integration
proves the exact manager UPDATE fault deterministically. No order or position
mutation is initiated by the fault harness; normal PAPER authority stays singular.

## Minimum 24-hour PAPER soak

After restored readiness and all controlled faults, record the Istanbul and UTC
start time, exact SHA, per-service PID/NRestarts and counters. Observe for at least
24 continuous hours with bounded, read-only snapshots. Avoid repeated full-history
scans. A restart, deployment, source change or protective lifecycle loss resets
the candidate's acceptance and requires investigation.

All of these are required:

- Realtime NRestarts delta **0**, unchanged PID; DB-lock lifecycle deaths **0**.
- Unmanaged OPEN **0** for the complete window, with fresh protective quotes and
  per-path health. DEGRADED contention must recover on a fresh tick; persistent
  inability to manage is an acceptance failure even if the process stays alive.
- Exact SHA, clean process source, process-bound PID/contracts and fresh producer
  successes. No silent READY based on an old process/cache or heartbeat alone.
- PAPER_ONLY / real_order_allowed=false continuously; real orders **0**.
- Every natural new OPEN has actual executed notional **>=100 USDT**, including
  partial fills. Below-floor fills cancel before cash mutation. Reduce-only dust
  is exempt. Cash/exposure/risk limits and frozen rules stay enforced.
- No duplicate fill, cash debit, event or exit under contention/recovery; no
  second lifecycle authority. Preserve LONG/SHORT recovery, negative-edge veto,
  three-confirmation thesis invalidation and immediate stop/liquidation.
- Controlled metadata/helper outage and SQLite contention outcomes are attached.
  Missing natural scenarios remain explicitly unverified, never fabricated.

## Rollback and merge gate

On failure, retain health/log evidence, remove only canary-owned overrides and
restore the recorded source/unit/timer configuration. Do not restore old DB
history or delete sidecars. Start producers sequentially with current-process
readiness before the sole realtime consumer. Verify the rollback can protect
existing obligations; otherwise keep the proven protective authority and block
new entries rather than claiming safety from a checkout alone.

Record exact head, start/end times, full 24-hour evidence, restart/PID deltas,
unmanaged counts, contention recovery, fault outcomes and any unobserved criteria.
**Starting** a soak is not acceptance. #104 stays open/unmerged until the complete
24-hour soak and re-canary criteria pass and a separate merge authorization is
given. #103 remains superseded. Software tests establish a reviewable source
candidate; they do not establish runtime reliability or profitability.
