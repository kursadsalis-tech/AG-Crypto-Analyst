# AG Crypto Analyst — Session Handoff (2026-08-11, v1.2.0)

## 2026-08-12 Forecast Core V1 result and frozen V2 handoff

- Product goal is calibrated, successful 1h/4h/24h price forecasting; merely
  suppressing weak signals is not counted as product success.
- One bounded architecture change was approved: a single Forecast Core, not a
  runtime network or vote of agents.
- Added `decision_engine/forecast_contract.py` and `analysis/forecast_core_lab.py`.
- The only pre-registered price-only challenger is
  `POOLED_RIDGE_DISTRIBUTION_V1_ALPHA_0_05_FIXED` with a mean-loss + alpha*L2
  objective frozen before any real run; no indicator/threshold grid is permitted.
- Output semantics: decision-close median target and 80% interval; calibrated
  next-open-to-horizon UP/DOWN/NEUTRAL probability beyond 10 bps cost plus 5
  bps minimum move; AL/SAT/BEKLE; Risk veto remains final.
- SAT means spot SELL/REDUCE/AVOID, never an assumed short portfolio profit.
- FIT/CALIBRATION/VALIDATION are strictly purged. The final historical slice is
  only `OBSERVED_DIAGNOSTIC` because its broad chronology was seen before.
- Validation includes no-change/climatology baselines, class/action calibration,
  normalized point/distribution errors, interval quality, coin/regime breadth,
  constant-direction action baseline and weekly clustered bootstrap bounds,
  including high-confidence claimed-hit and calibration bias.
- Forecast quality is measured before Risk; Risk only changes the adjusted
  action to BEKLE.
- The real Mac V1 run rejected all three horizons. 1h/4h were near or below
  baseline with no actions; 24h had only 17 tail actions and failed the global
  probability, price, calibration, coverage and breadth gates. No observed
  diagnostic was opened and the price-only V1 family is closed.
- The only next campaign is now implemented and frozen as `DERIVATIVES_STATE_V2`:
  exact Binance USD-M funding, OI/positioning, premium/basis and closed-kline
  taker flow. There is no new indicator or threshold search.
- Added `data/derivatives_market_data.py` and
  `analysis/forecast_core_v2_lab.py`.
- Data contract: 400-day experiment, 430-day collection including warmup,
  at least 10 supported coins plus BTC/ETH, normalized 8h-equivalent funding,
  no undocumented Vision taker-ratio feature, no imputation, and bounded
  global/symbol/week context-gap gates.
- Endpoint hierarchy: 4h PRIMARY, 24h SECONDARY only after 4h clears, and 1h
  EXPLORATORY with no artifact eligibility.
- CALIBRATION is split chronologically into CAL-A and CAL-B. The artifact is
  hash-stable through a JSON round trip and remains DEVELOPMENT_ONLY.
- Validator/data red-team blockers were fixed before any real V2 result.
- Compilation, V1/collector/V2 self-tests, a 26-feature point-in-time smoke and
  a 400-day/10-coin readiness integration smoke passed.
- Live Decision Engine, report, alert and cycle integrations are unchanged.

Immediate next action in the next session: run the resumable checksum-verified
USD-M derivatives backfill on the real Mac. Only after readiness passes, run
`analysis.forecast_core_v2_lab` exactly once. Do not tune the model, thresholds,
feature pack or endpoint hierarchy after seeing the result. A rejection closes
this V2 family; a pass remains DEVELOPMENT_ONLY and still requires a newly
sealed future period plus fresh forward calibration before user-facing confidence.

## Current product state

- Product version: v1.2.0.
- Completed: Market Data, Market Analyzer, Technical Analysis, News Intelligence, Social Sentiment, On-chain, Macro, Whale Tracker, Risk Manager, Decision Engine (SHADOW mode), Report Generator.
- Core roadmap: COMPLETE through v1.2.0. Next phase is operational validation and measured reliability work, not a new module.
- Mac LaunchAgent com.agca.forward-shadow runs the live product cycle every 5 minutes.
- Live cycle: NEWS -> FORWARD SHADOW -> RISK MANAGER -> DECISION ENGINE -> REPORT GENERATOR -> ALERT BRIDGE -> SOCIAL SENTIMENT -> ON-CHAIN -> MACRO -> WHALE TRACKER.
- Full live cycle passed at 11/11 steps.
- Social, On-chain, Risk Manager and Decision Engine use 15-minute internal cadences; Macro uses 6 hours; Whale Tracker uses 5 minutes; Report Generator runs once daily after 11:00 Europe/Istanbul.

## Forward Shadow — keep frozen

- Model: REGIME_AWARE_FORWARD_V2, cutoff=0.249, horizon=1h.
- Start seal: 2026-08-11T01:29:56.397166+00:00.
- INVALID_PRESTART_SEED records never count.
- Latest scoreboard: NEWS BASE settled=13, hit=23.1%, dir_return=-0.050%; MULTIFACTOR settled=5, hit=20.0%, dir_return=-0.013%.
- Sample is small and weak. Do not change the frozen model or approve a Champion from this evidence.

## Social Sentiment v0.6.0

- Combined Reddit crypto feed, SQLite deduplication, title+content analysis.
- Rolling 24h coin sentiment and evidence-based confidence.
- Low-confidence or contradictory social volume is not a trade or alert signal.

## On-chain v0.7.0

- Bitcoin mempool and fee metrics from mempool.space.
- Chain TVL from DefiLlama.
- Approximate 24h analyzer with accepted span 18-30h.
- WARMING_UP and NO_DATA never enter decisions or alerts.
- Initial large-transfer coverage is now implemented in Whale Tracker; unsupported token chains and verified exchange-flow labeling remain future extensions.

## Macro v0.8.0

Working components:

- Official keyless FRED CSV collector for DFF, DGS10, DTWEXBGS, VIXCLS and CPIAUCSL.
- 800-day lookback and idempotent SQLite observations.
- Fed Funds 90d, US 10Y 30d, broad USD 30d, VIX level and CPI YoY-trend components.
- Persistent macro regime snapshots.
- Six-hour isolated cycle integrated into the live product cycle.

Latest validation:

- Collector: observations=2481, new=4, series_errors=0.
- Analyzer: coverage=5/5.
- PROVISIONAL regime: RISK_ON, score=-3.
- Integration cadence test: correctly SKIPPED before six hours elapsed.

Scope boundary:

- Macro regime is context only.
- It must not trigger an alert, trade decision or Decision Engine input without historical and forward validation.
- Different release dates and possible FRED revisions must remain visible.

## Whale Tracker v0.9.0

Working components:

- Confirmed Bitcoin outputs >= $10M from Blockchain.com blocks.
- Safe-block top-level native ETH transfers >= $10M.
- ERC-20 Transfer logs >= $10M for verified Ethereum contracts: ARKM, BIO, ETHFI, ILV, LQTY, SLP and UMA.
- On-chain decimals verification plus Binance public spot valuation.
- Independent BTC, native ETH and ERC-20 scan states and source isolation.
- Persistent events and a 24-hour cross-chain evidence analyzer.
- Five-minute cycle integrated into the live product cycle.

Latest validation:

- Bitcoin 24h evidence: 9 events, 9 transactions, gross estimate ~$308.2M, max ~$83.0M.
- Native ETH: 32 safe blocks scanned, 0 threshold events.
- ERC-20: 1 safe block scanned, 7 contracts validated on-chain, 0 threshold events.
- Combined cycle: SUCCESS, source_errors=0, alert_eligible=0, MONITOR_ONLY.

Scope boundary:

- Gross output/transfer is not net flow.
- Change, self-transfer, mint, burn and contract activity are possible.
- UNKNOWN / UNVERIFIED evidence never triggers alerts or enters decisions.
- Unsupported chains or tokens must not be presented as covered.

## Risk Manager v1.0.0

Working components:

- Closed-1h-candle risk metrics for BTC, ETH and 11 priority Binance Spot USDT assets.
- Per-asset NORMAL / ELEVATED / HIGH / EXTREME risk and position caps.
- Global NORMAL / CAUTION / DEFENSIVE / LOCKDOWN / DATA_BLOCKED policy.
- Total exposure, alt exposure, single-position, risk-per-trade, daily-loss and new-position ceilings.
- Persistent policy/asset snapshots and a 15-minute isolated cycle.
- Signal Risk Gate persisted in risk_gate_decisions.
- Risk Manager runs before the product alert bridge.

Latest validation:

- Snapshot 3: coverage=13/13, market_state=NORMAL, market_stress=0/13.
- Ten low-turnover assets are individually blocked; structural illiquidity no longer causes a false global LOCKDOWN.
- BTC cap=10%, ETH cap=10%, WLD cap=5% in the observed policy.
- Full 9/9 cycle passed; Risk Manager correctly SKIPPED inside its 15-minute cadence.
- Forward evidence is SHADOW_ONLY: settled=5, hit=20.0%, Wilson lower=3.6%, directional return=-0.013%.
- No Forward product alert is permitted by current evidence.

Mandatory gate:

- settled >=30,
- hit rate >=60%,
- 95% Wilson lower bound >=50%,
- positive directional return,
- risk snapshot age <=30 minutes,
- global and asset permission.

Scope boundary:

- Risk limits are ceilings, not return predictions or trade advice.
- QVOL is a 24h spot-turnover proxy, not order-book depth.
- Risk BLOCK is a final veto and cannot be offset by other scores.

## Decision Engine v1.1.0

Working components:

- DECISION_POLICY_V1_SHADOW with explicit evidence maturity.
- Closed-1h technical and market research context for 13 risk-covered assets.
- Separate shadow_action and product_decision fields.
- Risk Manager BLOCK as a final veto.
- Persistent decision_engine_snapshots.
- Non-overlapping one-hour decision_shadow_predictions.
- AL/SAT directional-return settlement without automatic policy promotion.
- Fifteen-minute isolated cycle after Risk Manager and before the alert bridge.

Latest validation:

- Risk snapshot 4 was NORMAL and fresh.
- BTC produced SAT_ADAYI; WLD produced AL_ADAYI.
- Ten low-turnover assets were risk-blocked.
- All 13 product decisions remained BEKLE.
- Two one-hour shadow predictions opened with zero source errors.
- Full live cycle passed at 10/10; immediate rerun correctly skipped Decision Engine inside cadence.

Scope boundary:

- Shadow candidates are not alerts, trades or production decisions.
- PROVISIONAL, WARMING_UP, NO_DATA, MONITOR_ONLY, UNVERIFIED and SHADOW_ONLY evidence is never upgraded by score aggregation.
- One open prediction per symbol prevents overlapping sample inflation.
- The engine cannot leave SHADOW mode automatically.

## Report Generator v1.2.0

Working components:

- Fail-closed daily investment committee Markdown report.
- Latest Decision, Risk, Forward and Decision Shadow inputs.
- Separate shadow-research and effective product-decision columns.
- Production opportunity requires PRODUCTION + product AL + Risk ALLOW.
- Risk/Decision snapshots older than 30 minutes cause DATA_BLOCKED.
- Persistent idempotent committee_report_snapshots.
- Daily output at reports/daily_investment_committee_report.md.
- Once-per-Istanbul-day cadence after 11:00.
- Manual tests do not consume the scheduled daily slot.
- Integrated into the existing five-minute LaunchAgent cycle.

Latest validation:

- Report state SHADOW_ONLY; 13 coins; 10 risk blocks.
- Effective product action BEKLE; strongest opportunity YOK.
- BIOUSDT was the riskiest asset in the current risk snapshot.
- BTC SAT_ADAYI and WLD AL_ADAYI remained research-only.
- At 00:51 Europe/Istanbul, the daily cycle correctly waited for 11:00.
- Full live product cycle passed at 11/11.

Pending natural observation:

- Confirm the first unattended 11:00 generation and persisted daily snapshot.
- This observation does not block the completed product roadmap.

## Historical Decision replay

Completed:

- Point-in-time replay over 13 symbols and roughly 400 days of closed 1h data.
- 127,322 newly stored candles; 13/13 ready; zero fetch or time-gap errors.
- Next-candle-open entry; 1h/4h/24h outcomes; 10 bps round-trip cost.
- Chronological 60% LEARN / 20% VALIDATION / 20% EXAM.
- True Binance quote-volume persistence and migration.
- Deterministic future-leakage, cost, split, continuity and risk-veto tests.

Measured result:

- 9,576 decision timestamps produced 23,881 candidates.
- Current-policy EXAM net return was negative at 1h, 4h and 24h.
- A fixed family of 36 FOLLOW/CONTRARIAN challengers per horizon was tested
  with seven-day block confidence bounds.
- No challenger passed LEARN at any horizon: NO_LEARN_EDGE.

Decision:

- Current price-state rule family is rejected for production.
- Live thresholds and policies remain unchanged; product stays SHADOW_ONLY.
- Do not continue searching combinations of the same persistent-state rule.
- The final pre-registered sparse transition/breakout lab also returned
  NO_LEARN_EDGE at 1h, 4h and 24h.
- Transition data: 9,576 decision points, 68,616 risk-allowed contexts,
  55,872 risk blocks, zero point-in-time gaps and 146..2,107 events/config.
- STOP_PRICE_TECHNICAL_RULE_SEARCH is now final. Technical data may remain
  risk/context evidence, not a validated directional signal.

## News catalyst replay contract

- One independent, quality-gated GDELT news catalyst lab was added.
- It uses strict title-level coin evidence, canonical URL plus 72h title dedup,
  excludes approximate day-noon timestamps, delays availability by 60 minutes,
  enters at the next hourly open, applies 24h symbol cooldown and 10 bps cost.
- Sixteen fixed FOLLOW/CONTRARIAN configs are evaluated with seven-day block
  bounds through LEARN -> VALIDATION -> sealed EXAM.
- Technical direction is not a selection filter. Live Risk Manager remains
  final for any future product candidate.
- A fail-closed data-quality gate blocks candidate selection and EXAM if any
  timestamp, title, duplicate, source, span, price-match, sample or coin
  coverage requirement fails.
- Synthetic next-open, cost, cooldown and EXAM-blindness tests passed.
- The real Mac quality gate ran on 5,571 raw articles: valid timestamp format
  100%, usable titles 99.8%, but exact intraday timestamps 0%.
- Every record carries the archive-day 12:00 fallback rather than a known event
  time. All downstream zero counts are consequences of this root time gate.
- Result: DATA_QUALITY_BLOCKED. Candidate selection, VALIDATION and EXAM did
  not run.
- Do not impute historical news times or reopen this GDELT daily-archive event
  replay. News catalysts must accumulate exact published/collected timestamps
  in forward operation.
- Prior 77.6% NEWS BASE, ROBUST_B and provisional Champion labels came from the
  same day-only archive and are withdrawn as historical edge/product evidence.
- REGIME_AWARE_FORWARD_V2 remains only a frozen SHADOW hypothesis with zero
  historical credit; it can earn status only from exact-timestamp forward data.

## Notification policy

- Daily investment-committee briefing: 11:00 Europe/Istanbul.
- Critical-development watch: hourly and silent without decision-relevant events.
- ChatGPT task notifications stay enabled.
- Codex turn-completion push notifications are disabled.
- Telegram remains optional, not the primary user channel.

## Research discipline — DO NOT BREAK

- Never leak future, challenge or exam outcomes into model selection.
- Do not chase small-n hit rates.
- Frozen Forward Shadow parameters stay unchanged during forward evaluation.
- Low-confidence Social, immature On-chain, provisional Macro, UNVERIFIED Whale or SHADOW_ONLY Forward data must not drive alerts or trades.
- Risk Manager BLOCK is final; Decision Engine cannot outvote it.
- Decision Engine shadow candidates must remain separate from product decisions until independent forward evidence is sufficient.
- Parallel validation must not stall the main product roadmap.

## Next session priority

1. Do not rebuild completed modules from scratch; the core roadmap is complete through v1.2.0.
2. Do not reopen price/technical search or the day-only GDELT historical event replay.
3. Accumulate news-catalyst evidence only from exact published/collected forward timestamps.
4. Let Forward Shadow, Decision Engine validation, Social Sentiment, On-chain, Macro, Whale Tracker, Risk Manager and Report Generator continue automatically.
5. Observe the first unattended 11:00 Europe/Istanbul report and verify its daily snapshot.
6. Keep Decision Engine shadow candidates clearly separate from product decisions in every report.
7. Never let scoring or reporting upgrade immature evidence into a strong decision.
8. Keep Risk Manager BLOCK final and preserve frozen live policies while real samples grow.

## Working style

The assistant manages project direction and performs every inspection or edit it can do itself. Ask the user only for actions that genuinely require the user's local Mac. Give one local action at a time; test and verify before moving on.

## Suggested opening next session

**“Dostum, AG Crypto Analyst’a kaldığımız yerden devam edelim.”**


## Update 2026-08-16 — Forecast Core V2.1 Final Result

- Binance USD-M backfill is complete and auditable. Twelve of thirteen symbols
  support the frozen 400-day window; SLP is excluded only for insufficient span.
- Optional sparse account-ratio fields were removed before outcomes were opened.
  OI/funding/basis/kline taker-flow remain exact point-in-time inputs.
- Exact OI anchor lookup fixed gap amplification without imputing missing values.
  Final data quality passed with 52 skipped contexts and 2.4% worst-week gap.
- V2.1 was rejected on VALIDATION: 4h PRIMARY Brier skill +0.2%/MAE 0.0%; 24h
  SECONDARY +3.8%/-2.8% with 11.2% class ECE; 1h EXPLORATORY -1.0%/-0.2%.
  All horizons produced zero raw actions at the frozen 0.70 threshold.
- Conclusion is NO_VALIDATED_FUTURES_MICROSTRUCTURE_EDGE. Do not lower the
  threshold, reopen V2.1, or present abstention as success.
- The post-2026-05-28 observed diagnostic was not opened. Keep it sealed for the
  one bounded interaction-capable Forecast Core V3 campaign.
- Preserve Forecast Contract, data providers, validator, Risk veto and live
  SHADOW policy. V3 changes the model layer only. If V3 fails, stop model search
  on the same price+derivatives dataset and require new exact-time catalyst or
  fresh forward evidence.
