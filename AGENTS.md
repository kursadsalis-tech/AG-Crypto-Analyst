# AGCA repository working agreement

This repository is the single source of truth for AGCA development. Work only
inside this checkout; do not create shadow source copies in ChatGPT project
mirrors or temporary folders.

## Start of every session

1. Confirm the working directory is this Git repository before searching for
   project files or making changes. If it is not, locate the canonical checkout
   and state the mismatch immediately.
2. Read `docs/LAST_SESSION_HANDOFF.md` and inspect `git status --short --branch`.
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
- Never commit credentials or print secret values in diagnostics.
