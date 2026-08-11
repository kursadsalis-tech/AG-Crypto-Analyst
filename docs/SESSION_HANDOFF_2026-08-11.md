# AG Crypto Analyst — Session Handoff (2026-08-11, v1.2.0)

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
- The real Mac database run is pending; no historical result is claimed yet.

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
2. Run the single quality-gated news catalyst replay on the real Mac database.
3. If data quality is blocked, do not force a simulation; if it passes, record only the sealed LEARN/VALIDATION/EXAM result.
4. Never reopen price/technical threshold or indicator search on the same data.
5. Let Forward Shadow, Decision Engine validation, Social Sentiment, On-chain, Macro, Whale Tracker, Risk Manager and Report Generator continue automatically.
6. Observe the first unattended 11:00 Europe/Istanbul report and verify its daily snapshot.
7. Keep Decision Engine shadow candidates clearly separate from product decisions in every report.
8. Never let scoring or reporting upgrade immature evidence into a strong decision.
9. Keep Risk Manager BLOCK final and preserve frozen live policies while real samples grow.

## Working style

The assistant manages project direction and performs every inspection or edit it can do itself. Ask the user only for actions that genuinely require the user's local Mac. Give one local action at a time; test and verify before moving on.

## Suggested opening next session

**“Dostum, AG Crypto Analyst’a kaldığımız yerden devam edelim.”**
