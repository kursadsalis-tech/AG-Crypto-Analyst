# Hardening review — source candidate, no runtime deployment

Branch `codex/agca-comprehensive-hardening` supersedes PR #103 and retains its
exact head as ancestor. Single PAPER authority, real_order_allowed=0, SQLite
history, LONG/SHORT recovery, negative-edge veto, reason/direction telemetry,
three-confirmation flow invalidation and immediate stop/liquidation remain.

## Root cause → correction → verification

| Root cause | Correction and files | Regression evidence |
|---|---|---|
| One stale metadata route kills both routes at startup | `exchange_metadata.py`: independent route loads, malformed/future/stale rejection; `paper_realtime_worker.py`: tolerant startup + local reload | `test_exchange_metadata`, `test_metadata_failure_domain_integration`, `test_realtime_hardening_health` |
| OPEN rules depend on live helper; dust slice can strand risk | `position_management.py`: immutable hashed fill rules, truthful legacy adoption, frozen fee fallback, reduce-only monetary-minimum exemption, step-quantized slices; `crypto_profit_agent.py`: validate/freeze before OPEN or cash mutation | Actual selection→live fill→opposite stale→own missing→stop on SPOT and USD-M; frozen liquidation fee, tamper rejection, legacy adoption, SHORT fractional-slice closure and genuine legacy residue tests |
| Supabase egress is sole refresh route | `exchange_metadata.py`, `refresh_exchange_metadata.py`: official primary, one bounded optional fallback, independent route attempts/health | Direct success avoids DB; direct failure falls back once; both fail preserve previous cache; malformed one route leaves other ready |
| Old producer payload silently accepted | `behavior_decision_core.py`: V2 exact contract/producer/core equality, strict nested recovery schema and additive provenance | `test_behavior_cache_contract`: V1/producer mismatch, corrupt schema/count/timestamps/latest_exit/source rejection; existing recovery tests |
| Sub100 planned/partial exposure and incompatible recovery cap | `paper_candidate_orchestrator.py`, `crypto_profit_agent.py`:100 actual floor, soft rounding headroom,1.5% recovery cap, hard cash/exposure/stop limits, pre-mutation partial cancellation | `test_minimum_paper_notional`, `test_executable_quotes`, `test_behavior_paper_integration`, `test_paper_policy_selection_audit` |
| 15m scheduler cannot cover 180s dynamic opportunity | `local_symbol_risk.py`: every15s,12symbols/two seconds,360 local1m bars; `behavior_decision_core.py`: merge missing coverage; realtime separate support task | `test_local_symbol_risk`: fresh symbol coverage with old global snapshot, missing/gapped/corrupt/source/liquidity checks, no network or trade authority |
| Helper outage globally DATA_BLOCKED; global caps not fully consumed | `risk_manager.py`: DEFENSIVE-or-tighter helper degradation; `crypto_profit_agent.py`: actual total/alt/count/current asset+stop caps, marked realtime fill valuation | `test_global_policy_entry_limits`: count/exposure/alt/current policy changes, stale/missing schema, marked value, stop risk; helper-degradation localrisk tests |
| Active unit masks old process/restart loop | Five worker `.service` files:30s/four starts per300s; `install_gce_free_worker.sh`, `runtime_acceptance.py`: metadata then exactproducer cache preflight + exact source/freshness/PID/restart stability | `test_runtime_acceptance`, `test_deploy_worker_restart`: stale/wrongSHA/dirty/version/startup/PID/restart failures and valid bounded acceptance |
| Health conflates entry availability and protective management | `paper_realtime_worker.py`: separate domains, true startup failure, isolated entry exception; `process_identity.py` and four producer entrypoints: frozen source/version/result timestamps | `test_realtime_hardening_health`, `test_producer_process_health`, existing priority/producer/observer tests |
| Active docs point to obsolete Mac path/10USDT/12observer slots | PROJECT_HUB/status/tasks/handoff/gate rewritten; previous status/tasks/handoff retained under `docs/history`; canary plan explicit | Source check: observer10 vs planner12; technical plan authority current; no historical record deleted |

## Files by responsibility

- `decision_engine/paper_candidate_orchestrator.py`: soft sizing floor, bounded recovery, invalid sizing rejection.
- `paper_trading/{crypto_profit_agent,exchange_metadata,position_management,behavior_decision_core}.py`: actual entry safeguards, risk enforcement, metadata/cache/frozen rules, candidate contract.
- `risk_manager/{risk_manager,local_symbol_risk}.py`: helper policy and bounded local coverage.
- `runtime/{paper_realtime_worker,process_identity,behavior_learning_worker,trade_learning_worker,technical_kline_worker}.py`: isolated lifecycle/support and source-bound producer health.
- `scripts/{refresh_exchange_metadata,collect_closed_behavior_minute,runtime_acceptance}.py`: route refresh, observer identity and read-only operational acceptance.
- `deploy/agca-{paper-realtime-worker,behavior-learning-worker,trade-learning-worker,technical-kline-worker,market-behavior-observer}.service`, `deploy/install_gce_free_worker.sh`: bounded restart and ordered preflight.
- `.github/workflows/paper-integration.yml`: new failure-domain tests enter required PAPER CI.
- New test modules: `test_behavior_cache_contract`, `test_global_policy_entry_limits`, `test_local_symbol_risk`, `test_metadata_failure_domain_integration`, `test_minimum_paper_notional`, `test_producer_process_health`, `test_realtime_hardening_health`, `test_runtime_acceptance`; affected existing fixtures/contracts updated explicitly.
- Current docs and historical archives: source authority/active instructions clarified, canary and rollback recorded.

## Validation and verdict boundaries

Local final integration:248/248 pass. Local full regression ran once:713 tests,
712 pass plus one obsolete recovery-size expectation. Updated expectation's
8/8 audit module passes. No product logic changed after that full run. Exact
final-head GitHub Actions must supply the final full-suite result; the PR records
that evidence and SHA. Shell syntax/diff check pass; changed-file high-signal
credential scan has zero findings.

Code is prepared for review and operator canary, not a claim of deployment or
profitability. Merge should wait for final-head CI and the user's bounded live
acceptance. Existing legacy OPEN without original frozen rules needs a current
valid route once for explicit adoption; historical execution rules cannot be
reconstructed from missing evidence. Genuine old sub-step residue remains
visible rather than a fabricated close. Local risk requires360 contiguous real
minutes and measured liquidity; it does not guarantee every newly seen symbol
is immediately tradable. Microstructure freshness, liquidity and stop-risk may
legitimately reject a >=100 fill. Economic validation remains future natural
PAPER outcomes across regimes, after all costs.
