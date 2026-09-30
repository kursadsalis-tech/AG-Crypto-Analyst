# AGCA — Current status, 2026-09-30

Source: `kursadsalis-tech/AG-Crypto-Analyst`; runtime:
`/home/kursadsalis/AG-Crypto-Analyst-cloud`. Main base
`56f85d11a757732bfbf34307ebf39aa3b23fb814`; hardening builds on unmerged
PR #103 head `b30f18e48e1b389620d56b51fefba4262d678da2`.

## Implemented candidate

- Independent SPOT/USD-M metadata availability and bounded direct-official refresh.
- Immutable execution rules protect OPEN management after helper failures;
  legacy adoption is explicit and never rewrites historical fill evidence.
- V2 behavior cache validates schema and exact producer/core contracts.
- At least 100 USDT actual new OPEN exposure, within unchanged hard risk/cash/
  liquidity constraints; quarter recovery is a soft target, gross cap is 1.5%.
- Global risk remains a 15-minute support cycle; local dynamic-symbol coverage
  runs separately every 15 seconds with bounded work and genuine local evidence.
- Helper degradation reduces risk caps; candidate-critical corruption remains vetoed.
- Process SHA/version/last-result health and deployment preflight/stability acceptance.

`runtime/paper_realtime_worker.py` is the single PAPER lifecycle authority.
Technical plans affect ranking, sizing, entry, stop and take-profit through that
one authority. The observer has 10 deep slots; planning has 12 per scan. Neither
slot limit defines the eligible market universe.

SQLite remains authoritative, Supabase an audit mirror. PAPER_ONLY and
`real_order_allowed=0` remain mandatory. No real orders or history deletion.

## Acceptance state

This source candidate is not deployed. Test and exact PR evidence live in
`LAST_SESSION_HANDOFF.md` / the superseding PR. Operational canary follows
`HARDENING_CANARY.md`; profitability is not claimed by passing software tests.
No merge of #103, production mutation, VM restart or canary occurred here.

Earlier P0/P1 checkpoints and observation-only technical-context statements are
[historical records](history/PROJECT_STATUS_BEFORE_HARDENING_20260930.md), not
current authority or next-step instructions.
