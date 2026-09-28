# Broad radar and bounded deep analysis

Implementation base: canonical main `3fc9c4b5fa65498b4419c99d2acc6c11dedfca21`.
This change does not claim live acceptance or profitable trading performance.

## Ownership and data path

1. Existing `runtime/technical_kline_worker.py` is the continuous broad-data
   owner. Its already-existing Binance all-market ticker/book fetch produces
   the eligible USD-M universe and continuous exact 1m subscriptions. It now
   publishes that same compact universe into `broad_market_radar`: one local
   SQLite row, replaced atomically, never mirrored. No new network request,
   worker, timer, execution API or decision loop is added. Technical continuity
   retention remains independent from current radar eligibility.
2. Existing behavior observer reads the radar snapshot read-only (maximum age
   180 seconds, no future timestamp). Its duplicate all-market network fetch is
   removed from the deployed database-backed path. The standalone CLI without a
   database still supports direct Binance discovery. A stale/missing producer
   fails visibly; it never substitutes the deep list or another exchange.
3. `adaptive_market_scanner` scores all radar members for attention only. Default
   depth/trade/funding subscriptions remain at most 12: two context instruments,
   eight ranked opportunities and two rotating opportunities. No context symbol
   is inserted when absent from the eligible universe. Rotation visits the
   remaining instruments under stable membership/ranking; the current deep list
   is not a membership or trade-eligibility filter.
4. The canonical behavior core reads only its existing 180-second freshness
   window. Each symbol uses its own latest usable minute instead of a global
   latest-minute slice. External-only setups retain a matching event-bearing
   minute for that instrument. Events are joined to the original minute and
   availability time; no timestamp is relabeled or freshness extended.
5. Realtime execution subscriptions retain all fresh evidence routes and every
   OPEN/PENDING route. These expensive execution subscriptions are a working set,
   not the broad monitored market. Risk support covers all eligible behavior
   symbols in its existing 600-second window instead of just one global minute.
   Risk history is prepared on demand for measured opportunities; broad radar
   membership by itself never grants a risk record or permission to trade.
6. The existing shared orchestrator plans at most 12 candidates per scan. Eight
   priority slots plus four rotating slots prevent a stable candidate tail from
   being permanently excluded. All candidates remain in the behavior result;
   `DEEP_ANALYSIS_CAPACITY_DEFERRED` records temporary scheduling. Selection,
   portfolio limits and fills stay in the canonical realtime PAPER lifecycle.

No threshold for evidence, promotion, risk, plan freshness, quote freshness,
liquidity, latency cadence or real-order permission is relaxed. Insufficient
fresh behavior, risk or technical evidence still fails closed and may prevent
an observed symbol from becoming a trade. Radar coverage is not execution
readiness. The broad universe remains Binance USDT perpetuals passing the
existing volume/spread filters; it does not claim every listed asset is eligible.

## Observability and regression evidence

- Technical health: `broad_radar_instrument_count`,
  `broad_radar_snapshot_timestamp_ms`, `broad_radar_age_ms`,
  `broad_radar_fresh`, `deep_analysis_dependency=false`. Fresh bar writes cannot
  hide a stale radar; health becomes DEGRADED after the radar freshness limit.
- Observer health/report `collection_contract`: radar owner, snapshot timestamp,
  broad count, `outside_deep_analysis_count`, routing/rotation metadata and
  `deep_shortlist_is_trade_universe=false`. Existing top-level broad/deep counts
  remain compatible.
- Selection scan diagnostics `deep_analysis`: total opportunity candidates,
  slot limit, attempted/deferred counts. Capacity deferrals are durable in
  `paper_behavior_selection_runs.reason_counts_json`; behavior reviews include
  `fresh_behavior_instrument_count`.
- Deterministic offline fixture: 102 eligible symbols remain 102 radar and kline
  members with 0/1/4/10 opportunity slots (2/3/6/12 total deep subscriptions).
  All 102 receive attention within 50 simulated cycles with stable ranks.
- Producer publishes independently of a failing deep observer; changing the
  deep capacity does not change the persisted radar. Late older writes cannot
  rewind it. Local storage stays at one row and has no mirror/trade authority.
- 40 eligible candidates remain 40; each scan plans at most 12, explicitly defers
  28, and all 40 receive planning within 10 simulated scans. At zero planning
  slots, no plan call occurs and all 40 remain in the candidate result.
- Different-minute symbols remain eligible together; stale/future/other-source
  data remains excluded. OPEN and PENDING SPOT routes survive rotation.

## Minimal controlled live acceptance

Run after PR review/CI and canonical main release. Preserve the canonical VM DB
`/var/lib/agca/crypto_data.db`; do not copy the dirty local primary checkout.
Record the previous clean deployment SHA and service state for rollback.

1. Update the VM to the reviewed canonical main commit. Use one short maintenance
   window to restart the changed technical worker, observer and realtime worker.
   Start the technical worker first; verify its new radar row is fresh and broad
   count is positive before restarting the observer. Restart realtime promptly
   after source update so process-bound SHA matches. Keep all existing risk and
   learning guards/services; do not force a position or run another decision loop.
2. The technical worker restart resets live stream continuity. Its persisted
   minute cache survives, but any missed minute must recover naturally. Missing
   exact bars remain fail-closed; do not backfill from another source or lower the
   24-bar requirement. This is a material deployment consideration.
3. Observe at least three naturally completed observer cycles. Compare technical
   radar count/age against observer broad/deep/outside counts. Under a market
   snapshot larger than 12, require outside count > 0, deep count <= 12 and fresh
   radar timestamps progressing. Inspect rotating instrument names. Do not run
   deliberate production failure or reduced-capacity experiments: offline tests
   already cover them.
4. Verify realtime process SHA/clean state, PAPER_ONLY, real-order flags false,
   no extra execution service, fresh risk, unchanged execution quote validation,
   and existing scan timing/SQLite-error telemetry. Planning count must stay <=12
   and opportunity count must equal planned + deferred when orchestration runs.
   Capacity/portfolio/risk/technical rejections are valid; no trade is required.
5. Compare scan and observer timing against the immediately pre-deploy sample.
   If radar is stale, SQLite contention grows, observer becomes ineligible or
   scan gaps regress materially, roll all three changed processes back to the
   recorded release in a controlled window. Preserve DB history; the extra
   local-only single-row radar table is inert to the old code. Do not claim
   acceptance until exact process source and bounded natural evidence agree.

No live deployment, restart, trade or production failure injection is performed
by the development tests. Live latency/resource acceptance remains outstanding.

## Changed files

| Files | Change |
| --- | --- |
| `database/broad_market_store.py` | Single-row local radar publication/read contract |
| `runtime/technical_kline_worker.py` | Independent existing producer, health and freshness |
| `data/binance_market_universe.py` | Exclude non-finite discovery values |
| `data/adaptive_market_scanner.py` | Full breadth metadata and bounded rotating attention |
| `scripts/collect_closed_behavior_minute.py` | Read shared radar, bounded configurable deep attention |
| `paper_trading/behavior_decision_core.py` | Per-symbol fresh evidence selection and original event-minute joins |
| `runtime/paper_realtime_worker.py` | Preserve fresh prior-slice execution routes plus active obligations |
| `risk_manager/risk_manager.py` | Risk coverage across the existing fresh evidence window |
| `decision_engine/paper_candidate_orchestrator.py` | Bounded technical planning and rotating capacity deferrals |
| `paper_trading/crypto_profit_agent.py` | Publish planning-capacity diagnostics through existing selection |
| `tests/test_broad_market_radar.py` | Breadth, failure independence, rotation, freshness and authority |
| `tests/test_binance_market_universe.py` | Non-finite source rejection |
| `tests/test_behavior_decision_core.py` | Fresh-window query and cross-slice regressions |
| `tests/test_paper_candidate_orchestrator.py` | 40-candidate/12-plan capacity and zero-slot independence |
| `tests/test_single_runtime_authority.py` | Point-in-time subscription test adaptation |
| `tests/test_technical_kline_worker.py` | Shared-universe selection test adaptation |
| `docs/PAPER_EXECUTION_GATE_REGISTER.md`, `docs/TASKS.md`, `docs/LAST_SESSION_HANDOFF.md`, this document | Current contracts, progress and outstanding live acceptance |
