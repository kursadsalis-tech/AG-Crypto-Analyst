# Repository hygiene

The canonical AGCA checkout is:

`/Users/elifzilan/Documents/AG-Crypto-Analyst`

ChatGPT project mirrors are context-only and are not valid development
checkouts. New Codex coding tasks should be opened against the canonical local
Git repository.

## Directory ownership

| Path | Purpose |
| --- | --- |
| `analysis/` | Reproducible research and backtest programs |
| `data/` | Market and external evidence collectors |
| `database/` | SQLite-first persistence and remote mirror code |
| `decision_engine/` | Forecast and decision orchestration |
| `paper_trading/` | PAPER execution, governance, accounting, and learning |
| `runtime/` | Long-running cloud worker and operational entry points |
| `deploy/` | Versioned service and deployment definitions |
| `scripts/` | Bounded operator and repair utilities |
| `tests/` | Automated regression tests |
| `reports/` | Reproducible research artifacts and report templates |
| `docs/` | Durable decisions, status, runbooks, and handoffs |

## Never commit

- `.env` files or credentials
- SQLite databases and `-wal`/`-shm` sidecars
- process logs, caches, virtual environments, or editor metadata
- upload bundles, temporary copies, or ad-hoc backup files

## Checkpoint protocol

Before a checkpoint, run:

```text
python3 -m unittest discover -s tests
git diff --check
git status --short --branch
```

Every checkpoint message should describe one coherent operational milestone.
Generated research artifacts may be committed only when their generator and
provenance are present in the same history.

## Mandatory session handoff

Every implementation session ends with an updated
`docs/LAST_SESSION_HANDOFF.md` and a clean or explicitly checkpointed Git state.
The handoff must contain:

- completed work and verification evidence
- incomplete work and the single next priority
- current local branch and checkpoint commit
- relevant cloud, Supabase, and automation state
- warnings that must not be rediscovered in the next session
- an exact opening prompt for the next session

The final user response must repeat the canonical checkout path and the opening
prompt. When the current Codex task is backed by a ChatGPT mirror, explicitly
tell the user to open the canonical local repository for the next coding task.
