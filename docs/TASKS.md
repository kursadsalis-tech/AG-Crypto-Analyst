# AG Crypto Analyst — Active Tasks

## Product rule

AGCA is a realtime PAPER trading product. Work is prioritized only when it helps
the live loop observe → interpret → discover opportunity → decide → execute →
manage → measure economic result → improve.

Fixed coin lists, scheduled committee reports, Telegram reporting, legacy shadow
schedulers and research-for-research's-sake are not product objectives.

## Current

- [x] Consolidate the validated realtime PAPER lifecycle.
- [x] Restore executable bid/ask, freshness and visible-liquidity safety.
- [x] Make open positions plus dynamic eligible market behaviour drive the
  monitored universe.
- [x] Connect evidence-gated trade learning and strategy allocation to the live
  PAPER lifecycle.
- [x] Remove obsolete committee/report/Telegram/hourly-forecast/forward-shadow
  runtime authorities and their dead regression tests.
- [x] Make the cleaned validated product line canonical `main`.
- [x] Establish clean CI baseline: 83 focused + 331 broad = 414 passing tests.
- [ ] Audit remaining runtime entry points for duplicate decision/execution
  authority; remove or demote obsolete loops.
- [ ] Audit opportunity discovery for hidden fixed-cohort assumptions and make
  eligibility fully market-driven where evidence supports it.
- [ ] Validate exact-live entry path uses executable side price and side-specific
  visible liquidity without double spread/slippage.
- [ ] Keep learning economically gated: clean forward PAPER evidence,
  net-after-cost improvement, adequate sample and risk compliance.
- [ ] Reduce operational documentation to current truth and archive/remove stale
  handoff/deployment instructions that can misdirect future work.
- [ ] Re-verify external runtime/deployment health only when deployment work
  becomes the active objective.

## Guardrails

- `PAPER_ONLY`; real-order authority remains disabled.
- No capability gets production authority from backtest evidence alone.
- No new architecture unless required to improve the working product.
- Research/challenger code may measure hypotheses but cannot become a parallel
  live decision authority.
- Git history is the archive; operational docs must describe current truth.
