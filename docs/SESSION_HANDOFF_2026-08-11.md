# AG Crypto Analyst — Session Handoff (2026-08-11, v1.0.0)

## Current product state

- Product version: v1.0.0.
- Completed: Market Data, Market Analyzer, Technical Analysis, News Intelligence, Social Sentiment, On-chain, Macro, Whale Tracker, Risk Manager.
- Next main module: v1.1.0 Decision Engine.
- Mac LaunchAgent com.agca.forward-shadow runs the live product cycle every 5 minutes.
- Live cycle: NEWS -> FORWARD SHADOW -> RISK MANAGER -> ALERT BRIDGE -> SOCIAL SENTIMENT -> ON-CHAIN -> MACRO -> WHALE TRACKER.
- Full live cycle passed at 9/9 steps.
- Social, On-chain and Risk Manager use 15-minute internal cadences; Macro uses 6 hours; Whale Tracker uses 5 minutes.

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
- Risk Manager BLOCK is final; the future Decision Engine cannot outvote it.
- Parallel validation must not stall the main product roadmap.

## Next session priority

1. Do not rebuild completed modules from scratch.
2. Let Forward Shadow, Social Sentiment, On-chain, Macro, Whale Tracker and Risk Manager continue automatically.
3. Begin v1.1.0 Decision Engine with explicit evidence maturity and veto handling.
4. Never let scoring convert PROVISIONAL, WARMING_UP, NO_DATA, UNVERIFIED or SHADOW_ONLY evidence into a strong decision.
5. Keep Risk Manager BLOCK final and preserve the frozen Forward Shadow policy while its real sample grows.

## Working style

The assistant manages project direction and performs every inspection or edit it can do itself. Ask the user only for actions that genuinely require the user's local Mac. Give one local action at a time; test and verify before moving on.

## Suggested opening next session

**“Dostum, AG Crypto Analyst’a kaldığımız yerden devam edelim.”**
