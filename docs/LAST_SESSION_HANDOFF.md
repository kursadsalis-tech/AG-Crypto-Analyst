# 2026-09-30 — PR #104 canary bug fixes; re-canary pending

Canonical source: `kursadsalis-tech/AG-Crypto-Analyst`.
Branch/PR: `codex/agca-comprehensive-hardening`,
[PR #104](https://github.com/kursadsalis-tech/AG-Crypto-Analyst/pull/104).
Original canary head: `53246f15ea635c864a76ee578cc23933da3119dd`.
Source-fix commit: `7db4d10145ccacd036f90e8ea75408b000d3c49a`. The final candidate head includes the
following documentation commit; its exact SHA and final full-regression/PAPER
CI results are pinned in PR #104 and the delivery, avoiding a self-referential
commit hash in this file.
Development worktree for this follow-up:
`/Users/elifzilan/Documents/AG-Crypto-Analyst/.worktrees/agca-comprehensive-hardening`.

## Completed and verified

- STARTING absent/null version fields now remain pending until full health or
  ready_timeout. Explicit wrong versions, source mismatch/dirty and startup
  failures still fail fast; STARTING never starts the stability window.
- Frozen technical stop initialization accepts finite positive actual-fill
  distance up to 5%, including a safer distance below 0.5%. Exact frozen stop,
  target geometry behavior, original plan/thesis and plan minimum are preserved.
- Focused tests passed: runtime acceptance 21/21, position management 42/42,
  existing hardening/failure-domain batch 14/14. Ten new test methods added.
- Full regression and PAPER integration are required on the final candidate
  head; use the exact-head result in PR #104/delivery for the final verdict.

## External state and boundary

User-reported old-head canary: metadata SPOT/USD-M and V2 cache preflights
accepted; worker PID1834044 stable with NRestarts=0 and later CONNECTED/ENTRY_PATH
READY. WLD1772/ZEC1781 managed HOLD; ENA1788 OPEN but uninitialized due to the
post-fill minimum bug. Legacy rules adoption succeeded for all three.
This follow-up did not access the VM or independently refresh those observations.
No merge, deploy, restart, production DB mutation or re-canary was performed.
PAPER_ONLY / real_order_allowed=false remain unchanged.

Next product step after green exact-head CI is a separately authorized re-canary
using HARDENING_CANARY.md, including ENA only if still naturally OPEN. Do not
merge #104 or run VM operations under the source-fix authorization. Unrelated
changes in the primary local checkout remain untouched.

## Previous candidate record (historical; superseded by the follow-up above)

# 2026-09-30 — Comprehensive PAPER hardening candidate

Canonical source: `kursadsalis-tech/AG-Crypto-Analyst`.
Runtime checkout: `/home/kursadsalis/AG-Crypto-Analyst-cloud` on `agca-runtime`,
GCP `project-6a717580-e37d-48f6-832`, `us-east1-b`.
Main base: `56f85d11a757732bfbf34307ebf39aa3b23fb814`.
Branch: `codex/agca-comprehensive-hardening`, descending directly from PR #103
head `b30f18e48e1b389620d56b51fefba4262d678da2`. Do not merge #103. Exact
candidate SHA and final CI evidence are pinned in the superseding PR.
Development checkout: `/Users/elifzilan/Developer/AG-Crypto-Analyst-hardening`,
a fresh clone of the canonical repo. Do not search old Mac paths or edit project
mirror sources; a local development path is not the runtime authority.

## Completed source changes

Independent metadata routes; direct official refresh plus bounded optional DB
egress fallback; immutable hash-verified fill rules and explicit legacy rule
adoption; helper-independent protective management; quantity-step-safe staged
exits. Existing genuine historical sub-step dust stays visible, never fabricated
as closed. V2 cache validates exact contract/core/producer and complete recovery
payload. Process-bound source, versions and success/error health cover critical
producers. ENTRY_PATH and OPEN_POSITION_MANAGEMENT are distinct; entry exceptions
roll back and cannot skip the protective stage.

Actual new OPEN >=100 USDT, including partial fills; 100 is a floor, not ceiling.
Normal and recovery soft targets yield to cash, marked total/alt exposure, asset,
stop-risk, liquidity and current policy. Recovery quarter sizing remains a soft
target under a 1.5% equity gross cap. Replan cannot expand its frozen stop budget;
sub-100 actual fills cancel with an audit reason. Reduce-only monetary dust is
exempt. Global total/alt/count caps now actually bind selection and fill.

Missing dynamic-symbol risk can be prepared from 360 exact closed local Binance
minute bars (explicit six-hour scope, no fabricated 24h/7d metrics) every 15s with
12-symbol/two-second work limits. Global/full-history policy stays on its 15m
cycle. Fixed references/cross-source helper loss tightens caps instead of vetoing
valid canonical candidate evidence globally. Missing/corrupt candidate evidence
and insufficient measured liquidity still fail closed.

Deployment preflight checks metadata before restarts, then exact V2 producer
cache before consumer restart. Acceptance checks five workers' exact source,
clean state, PID, freshness, contracts and zero restart delta over 60 seconds.
StartLimit four/300s and RestartSec30 bound storms. Installer is not a PR selector:
see the exact-head command and rollback requirements in HARDENING_CANARY.md.

## Verification

Focused regressions were run by domain. Final local PAPER integration passed
248/248. One local full regression execution ran 713 tests: 712 passed; one old
PR #103 assertion still required <=12.5/25 recovery notional, contrary to the new
user decision. Only that expectation was corrected; its full eight-test audit
module passed afterwards. The full suite was not repeatedly rerun locally.
The superseding PR's final-head CI runs all 713 regression tests and the 248-test
integration suite; inspect the exact head's CI before declaring final acceptance.

The earlier integration attempt found two old exact-100 request fixtures that
rounded below100 actual; they were corrected and an explicit below-floor
regression added (31/31 quote tests passed). These were documented fixture
updates, not weakened actual-fill checks.

Diff whitespace and installer shell syntax passed. A local high-signal credential
scan found no credentials in changed/new files. See HARDENING_REVIEW.md for file
mapping, cause→fix→test mapping and limitations. Final Git/CI status belongs to
the exact PR head; it is not evidence of runtime deployment or profitability.

## External state and next authorized work

No VM connection, deployment, production DB operation, live canary, merge or real
order occurred. SQLite history is retained. Main and PR #103 are unchanged.
The next action is user-operated bounded canary of the reviewed exact head via
HARDENING_CANARY.md. Do not invent opportunities to complete live criteria.
Legacy OPEN positions without frozen historical rules require a current valid
route once for truthful adoption; genuinely unavailable/corrupt rules remain
blocked and visible. Initial preflight requires both routes ready.

All previous handoffs are preserved in
[historical record](history/LAST_SESSION_HANDOFF_BEFORE_HARDENING_20260930.md).
They are not active instructions. In particular, old Mac canonical paths,
observation-only technical policy and obsolete PR #102/#103 next steps must not
redirect this work.
