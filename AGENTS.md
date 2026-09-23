# AGCA repository working agreement

This repository is the single source of truth for AGCA development. Work only
inside this checkout; do not create shadow source copies in ChatGPT project
mirrors or temporary folders.

## Start of every session

1. Confirm the working directory is this Git repository before searching for
   project files or making changes. If it is not, locate the canonical checkout
   and state the mismatch immediately.
2. Read `docs/PROJECT_HUB.md` and `docs/LAST_SESSION_HANDOFF.md`, then inspect
   `git status --short --branch`.
3. Preserve existing user changes. Never overwrite, reset, or clean them without
   first identifying their owner and purpose.
4. Do not infer that a missing file is empty or lost until the canonical checkout
   and project mapping have both been verified.

## File placement

- Product code belongs in the existing domain packages.
- Cloud process entry points belong in `runtime/`; service definitions in
  `deploy/`; operator utilities in `scripts/`; tests in `tests/`.
- Durable project documentation belongs in `docs/`.
- Reproducible research outputs belong in `reports/` and should have a matching
  generator or documented provenance.
- Databases, SQLite sidecars, logs, caches, secrets, local environments, and
  ad-hoc transfer files are runtime artifacts and must not be committed.
- Temporary editing copies must be created under the system temporary directory
  and removed before the task ends.

## End of every implementation session

1. Run tests proportional to the change; run the complete suite for shared
   runtime, database, governance, or deployment changes.
2. Review `git diff --check`, `git status`, and scan new files for credentials.
3. Always update `docs/LAST_SESSION_HANDOFF.md`, even if the user does not ask.
   Record what completed, what remains, verification evidence, external service
   state, the current branch/commit, and any material warning. Replace obsolete
   next-session instructions; never leave a completed incident as the priority.
4. Leave the repository either clean or in one clearly named checkpoint commit.
   Do not leave an unexplained mix of staged, unstaged, and untracked work.
5. End the user-facing response with a "Next session" instruction containing:
   the canonical project path, any required Codex project-selection warning, and
   one exact copyable opening prompt that continues from the handoff file.
6. Treat this closeout as part of task completion, not optional housekeeping.
   If closeout cannot be completed, say exactly what remains and do not claim the
   session is fully handed off.

## Safety

- SQLite is authoritative for runtime history; Supabase is an operational audit
  mirror unless a document explicitly states otherwise.
- Keep real-order authority fail-closed. Do not weaken PAPER_ONLY, governance,
  hash, freshness, reconciliation, or approval gates to make a check pass.
- PAPER risk coverage must follow the eligible dynamic behavior universe; a
  fixed reference-symbol list may anchor global stress but must not silently
  exclude otherwise testable opportunities. Missing or vetoed coverage remains
  fail-closed and must be recorded for counterfactual learning.
- Valid, executable market volatility, range, drawdown, or recent direction may
  reduce PAPER experiment size but must not by itself become a context-blind
  asset veto. Hard asset vetoes are reserved for missing/corrupt evidence or
  execution infeasibility such as inadequate liquidity.
- `docs/PAPER_EXECUTION_GATE_REGISTER.md` is binding for the active PAPER path.
  A new hard veto requires evidence, a regression test, and an explicit reason
  why position sizing cannot contain the risk. Every zero-selection cycle must
  have a durable machine-readable cause; "healthy but zero" is insufficient.
- Never commit credentials or print secret values in diagnostics.
- PAPER learning experiments must not be blocked by promotion/proven-skill
  thresholds. A point-in-time, cost-positive, risk-approved hypothesis may be
  tested with bounded exploratory PAPER size and an outcome-blind thesis;
  stronger sample/day/performance gates govern trust, sizing, or promotion.
  Changing this separation requires the user's explicit approval.

## Persistent problem-solving principle

- Treat a blocked first approach as a routing problem, not as proof that the
  objective is impossible. Preserve the user's actual goal, search broadly for
  alternative official, open, or reproducible paths, and test them with small
  concrete samples before concluding that no workable route exists.
- Use "there is usually another route" as the default working stance. Do not
  use it to invent evidence, bypass permissions, weaken safety gates, or hide a
  genuine limitation. Report failure only after materially different safe
  approaches have been checked and their evidence recorded.

## Efficiency and user communication

- Diagnose before iterating. Measure the failing stage and separate network,
  CPU/process, storage, locking, and external-service causes before changing
  production code or starting a long run.
- Compare materially different hypotheses first. Do not deploy a sequence of
  narrow parameter tweaks when the architecture or transport mechanism may be
  the dominant cause.
- During development, run the smallest focused test that can reject the current
  hypothesis. Run the full suite once for the final candidate, plus only any
  genuinely necessary post-deployment verification required by the risk of the
  change. Do not rerun the full suite after every small edit.
- Before a long command, state internally what evidence it will add and whether
  a cheaper bounded check can provide the same answer. Avoid repeated live
  trials without first incorporating the evidence from the previous trial.
- Codex's own heartbeat/automation notifications are the default user-facing
  monitoring channel. Do not propose or discuss Telegram unless the user
  explicitly asks for it.
- Do not send session closeout, handoff instructions, or a next-session opening
  prompt until the user explicitly says `oturum bitti`. Continue normal work
  without treating each implementation response as the end of a session.

## Core architecture rule: specialist workers, one trade authority

- AGCA should use small specialist workers/services for bounded responsibilities
  such as market-data collection, technical context, behavior evidence, risk,
  execution-state telemetry and learning support. These workers may run in
  parallel so the trading path does not wait for unrelated collection work.
- Every specialist must have an explicit input, output, ownership boundary and
  a clear statement of what authority it does **not** have.
- Specialist workers produce evidence, state or bounded analysis; they do not
  independently open/close positions, mutate live policy or become competing
  decision loops.
- A common orchestrator combines eligible evidence, while the canonical realtime
  PAPER lifecycle remains the single trade-decision/execution authority.
- When a continuous data obligation is required, prefer a long-lived collector
  over forcing that responsibility into a rotating/oneshot observer.
- Add a new worker only when the responsibility is separable, measurable and
  improves latency, reliability or clarity without creating duplicate authority.

## Product mission

- Build AGCA into an adaptive, market-wide trading system that can read public
  market behavior, combine advanced technical and fundamental analysis with
  order flow, derivatives, liquidation, on-chain, whale, macro, and critical
  news evidence, discover opportunities beyond a fixed coin list, manage risk,
  and keep learning from measured outcomes.
- Judge success by reproducible, cost-after live PAPER performance across
  enough trades and different market regimes, including drawdown and execution
  realism. Reports, backtests, prediction accuracy, or isolated wins are not
  proof of a successful trader.
- The intended destination is real-money profitability, but this is a goal,
  never a guarantee or present capability. Real orders remain impossible until
  the evidence gates pass and the user gives separate explicit approval. Do
  not optimize appearances, activity, or report volume instead of this mission.
