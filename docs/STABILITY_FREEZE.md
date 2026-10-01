# PR #104 stability freeze: writer and failure-domain architecture

Starting head: `2573de648edf229f09fbab2b68ccef2fdbe2828a`.
This is a source-only follow-up on `codex/agca-comprehensive-hardening`.
No new trading authority, feature, database reset or live operation is included.
PAPER_ONLY, real_order_allowed=false, actual new OPEN >=100 USDT and immutable
frozen execution evidence remain binding. #103 remains superseded; #104 stays
open/unmerged pending the complete separately authorized re-canary.

## Transaction ownership and failure domains

`database/sqlite_policy.py` supplies the shared local connection policy used by
`runtime_store.connect` and direct canonical writer stores. It uses two stable
sidecar advisory locks per resolved database path: one writer slot and one
realtime admission gate. Background transactions serialize on the writer slot;
a waiting realtime transaction closes background admission until its transaction
ends. Locks are released on commit, rollback and close, including failed commit
and connection collection. These are admission controls, not another database
or trade authority. SQLite remains the final arbiter, including for old/external
connections that do not participate in coordination.

Background lock waits are capped at 250ms; realtime execution uses 50ms. There
are no 30-second hot-path busy waits. Blocking SQLite work remains off the
websocket event loop. BUSY/LOCKED numeric base/extended codes and exact legacy
lock messages are transient; IO/corruption/schema/programming failures are not.
No tick is replayed by a catch-all retry wrapper.

The connection policy preserves the existing persistent journal mode (the
existing standalone agent bootstrap selects WAL). There is **no journal/schema
migration** in this policy and no history rewrite. Per-connection busy_timeout
is ephemeral; rollback to older source restores its own settings. Existing WAL
and DELETE databases are both covered by policy tests. Do not delete or replace
`.agca-writer.lock` / `.agca-realtime.lock` while any process is running: that
would split the coordination domain. Files contain no business data and remain
ignored runtime artifacts. All participating services run as the same service
user on the same local filesystem; this is not distributed locking.

### Canonical writer map

| Owner/path | Connection and transaction boundary | Contention/failure behavior |
| --- | --- | --- |
| Realtime OPEN manager | Dedicated execution thread; prioritized local connection; durable manager/event/exit accounting checkpoints | Manager invoked before pending fill; BUSY rollback, per-symbol/path DEGRADED, next fresh tick; fatal DB error visible and fail-closed |
| Realtime pending fill | Same execution connection; fill, cash debit, immutable fill event and manager initialization share one commit | Guarded PENDING->OPEN update; failed initialization rolls back new exposure; no committed unmanaged new OPEN |
| Opportunity selection | Separate scan thread/connection; bounded retries with rollback/new connection; existing decision/position keys remain authoritative | Entry degradation cannot bypass OPEN manager; actual execution revalidates fresh market evidence |
| Symbol universe/cache | Dedicated read connection/thread; refresh no longer performs expiry/init writes | Busy preserves last known obligations and reports SYMBOL_CACHE degradation; no execution event loop stall |
| Pending expiry and funding | Separate maintenance connection; expiry transaction and per-position funding transaction | Funding fetched before local writes; guarded PENDING segment resolution prevents duplicate charges; committed positions are not replayed on later contention |
| Technical klines | `technical_bar_store` coordinated connection; small closed-bar batches; separate bounded retention pruning | Commit between batches; idempotent bar keys; pending buffer retained on BUSY; counts occur after commit |
| Broad technical radar | `broad_market_store` coordinated single-snapshot upsert | Small local commit; no order authority; technical success requires actual current-process closed bars and fresh radar |
| Behavior learning/cache | Direct coordinated connection; schema commit, queued outcomes, missed settlements, historical aggregation, cache publication | Historical aggregation owns no writer lock; outcome horizon commits are idempotent; bounded fresh-connection retry |
| Trade learning | Direct coordinated connection; per-closed-position review, per-challenger evaluation, separate learning/allocator commits | Historical scans and CPU analysis between commits; existing immutable review/unique evaluation keys preserve retries |
| Market behavior observer | `market_behavior_store`; prepared closed minute/external evidence/settlement queue, then commit | Collection and aggregation before writes; retention deletion bounded; historical counts after commit; fresh next-minute continuation on BUSY |
| Local dynamic-symbol risk | Coordinated per-symbol upsert; 12-symbol/two-second existing work budget | Calculation before each short commit; transient contention/budget exhaustion visible; real DB errors propagate |
| Global risk timer | `risk_cycle` / `risk_manager` through coordinated `runtime_store` | Network/evidence preparation before snapshot persistence; existing bounded retries use the strict BUSY classifier |
| Exact macro timer and other runtime-store collectors | `observe_exact_macro_events`, shared repository/runtime-store connections | Same admission policy; no independent execution authority |
| Cloud attestation / realtime health monitor | Coordinated runtime-store/local connection for attestation/alert writes | Short local writes; support work serialized at installation |
| Remote mirror drain | `runtime_store`; local acknowledgement/health after remote completion | Authoritative local commit releases writer before remote work; failed auxiliary writes roll back before network retry; durable outbox remains |
| Metadata refresh / integrity tools | Metadata publishes files; integrity check is read-only | No competing lifecycle; integrity timer remains disabled/manual |

The design intentionally uses process-shared deterministic admission rather than
adding a central writer daemon, IPC lifecycle or second execution owner. SQLite
contention can still occur, including from nonparticipating connections; it must
produce bounded degradation rather than canonical realtime death. Persistent
contention that prevents protection remains an operational acceptance failure.

## Readiness architecture

Deployment drains scheduled support writers and stops background workers
serially, preserving the existing protective realtime authority until replacement
inputs are ready. Startup order is behavior/cache -> technical -> observer ->
trade, with a synchronous current-process acceptance gate after each. Supporting
oneshots finish sequentially and timers resume before the final producer/cache/
metadata recheck. The sole realtime worker is restarted last. Failure ends the
sequence; there is no blind retry/restart loop.

Each producer reports exact process SHA/clean/PID/start identity, versions and
real successful work. Completed collection/compute phases and SQLite executed
work advance a monotonic progress sequence. RUNNING/STARTING cannot start the
stability window. Healthy workers may retain a prior still-fresh success while
performing the next cycle, but heartbeat/phase publication never renews its
success timestamp. Technical readiness additionally needs a current-process
closed-bar write and fresh radar; its 180-second freshness budget covers the
one-minute publication cadence without converting socket connect to success.

`--ready-timeout 120` is a per-producer no-progress budget;
`--max-ready-seconds 1800` is an absolute ceiling. One producer cannot extend
another's deadline. Wrong populated SHA/dirty/contract, fatal health, PID/start
identity changes, restart deltas and invalid/regressing progress fail fast.
The existing STARTING missing-field tolerance is retained for startup and RUNNING
warmup. Behavior cache is bound to its current producer PID/start, not just SHA.
All services must be ready before the uninterrupted runtime window starts.

## Verification and acceptance boundary

Focused tests exercise actual manager UPDATE/exit contention, rollback and fresh
tick recovery, manager-before-entry, atomic pending fill, no duplicate accounting,
real SQLite writer contention across processes, transaction release between
technical batches, progress-only extension, no-progress/absolute timeout,
process-bound cache/health, fatal mismatch and staged installer commands against
isolated fake commands. All fault databases are disposable; no runtime history
or position is touched.

The PAPER CI list includes the new failure-domain, writer-coordination and
producer-progress suites. Full regression, PAPER integration and high-signal
credential/diff checks must be recorded against the delivered exact head in
PR #104 and the delivery. Old 723/258 results do not validate this candidate.

See [HARDENING_CANARY.md](HARDENING_CANARY.md) for the minimum 24-hour PAPER soak,
zero realtime restart delta, zero unmanaged OPEN, zero lock lifecycle death,
controlled metadata/helper and non-mutating contention faults, and merge gate.
No live reliability or profitability claim follows from software CI alone.
