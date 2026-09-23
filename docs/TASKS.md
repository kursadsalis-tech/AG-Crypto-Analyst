# AG Crypto Analyst — Active Tasks

## Product rule

AGCA is a realtime PAPER trading product. Work is prioritized only when it helps
the live loop observe → interpret → discover opportunity → decide → execute →
manage → measure economic result → improve.

Fixed coin lists, scheduled committee reports, Telegram reporting, legacy shadow
schedulers and research-for-research's-sake are not product objectives.

## Current

- [x] Adopt the specialist-worker orchestration rule: bounded workers run in
  parallel, a common orchestrator combines evidence, and the canonical realtime
  PAPER path remains the single trade/execution authority.
- [x] Move exact Binance USD-M closed 1m technical-bar collection out of the
  oneshot behavior observer into a long-lived data-only worker.
- [ ] Live-accept 24+ contiguous exact 1m bars on the new worker and verify
  `TECHNICAL_PLAN_UNAVAILABLE` is no longer caused by minute-cache gaps.
- [ ] Verify the next natural PAPER candidates receive fresh adaptive technical
  plans and that entry/stop/TP/sizing authority is exercised only through the
  canonical realtime decision path.
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
- [x] P0-1: propagate fresh volatility-stop evidence into PAPER sizing/management
  and fail closed instead of inventing a default stop.
- [x] P0-2: suspend evidence-mature non-positive calibrated expectancy while
  preserving small-sample exploratory sizing.
- [x] P0-3: block adverse same-setup churn until materially new evidence or a
  one-risk-unit market reset exists.
- [x] P0-4: keep PENDING SPOT execution routes subscribed and attest exact
  deployed git source identity.
- [x] P0-5: unify selection and simulated fills under one venue-specific PAPER
  taker-fee contract.
- [x] Deploy P0 canonical `main` to `agca-runtime` and verify process-bound
  source SHA, clean checkout, CONNECTED worker health, `PAPER_ONLY` and
  real-order authority disabled.
- [x] Fix deployment self-update re-exec, process-bound source attestation and
  stale realtime pending-entry housekeeping discovered during live acceptance.
- [ ] Observe Supabase mirror freshness until the canonical cancellation/attestation
  updates catch up; SQLite remains authoritative and mirror lag must not block
  trading hot-path behavior.
- [ ] Verify natural post-deployment trades exercise the corrected stop, venue,
  fee, negative-expectancy and re-entry contracts before adding decision-path
  complexity.
- [x] P1 research gate: inventory existing causal technical/multi-timeframe
  capabilities, benchmark CPU/latency/storage cost, and deploy the minimal
  observation-only 1h/4h candidate context without promoting the historical
  multi-timeframe research model.
- [x] P1 runtime acceptance: reuse existing risk candles with zero extra market
  requests; measure 23 contexts at 119.863 ms total; keep latest-only local
  storage, no mirrored technical-context table, and no decision authority.
- [x] Retire/mask the legacy Binance risk refresh that recreated a local
  `crypto_data.db`; restore canonical risk refresh, stale-risk fail-closed and
  transient SQLite busy handling.
- [ ] P1 forward evidence gate: accumulate natural PAPER theses carrying
  `technical_context_observed` and compare realized net-after-cost outcomes by
  alignment/structure/regime. Keep direction/ranking/confidence/sizing/stop
  authority disabled until adequate forward evidence exists.
- [ ] P2 research gate: separate broad lightweight opportunity radar from scarce
  deep-analysis slots so the shortlist cannot silently become the trade universe.
- [ ] P3 research gate: define bounded autonomous challenger generation and
  forward economic comparison without a second live execution authority.
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
