# AG Crypto Analyst — Session Handoff (2026-08-11, v0.7.0)

## Current product state

- Product version: v0.7.0.
- Completed: Market Data, Market Analyzer, Technical Analysis, News Intelligence, Social Sentiment, On-chain.
- Next main module: v0.8.0 Macro Agent.
- Mac LaunchAgent com.agca.forward-shadow runs the live product cycle every 5 minutes.
- Live cycle: NEWS -> FORWARD SHADOW -> ALERT BRIDGE -> SOCIAL SENTIMENT -> ON-CHAIN.
- Social and On-chain collectors each have a 15-minute internal cadence and skip intermediate cycles.

## Forward Shadow — keep frozen

- Model: REGIME_AWARE_FORWARD_V2, cutoff=0.249, horizon=1h.
- Start seal: 2026-08-11T01:29:56.397166+00:00.
- INVALID_PRESTART_SEED records never count.
- Latest scoreboard: NEWS BASE settled=13, hit=23.1%, dir_return=-0.050%; MULTIFACTOR settled=5, hit=20.0%, dir_return=-0.013%.
- Sample is small and weak. Do not change the frozen model or approve a Champion from this evidence.

## Social Sentiment v0.6.0

- Combined Reddit crypto feed, SQLite deduplication, title+content analysis.
- Rolling 24h coin sentiment and evidence-based confidence.
- Low-confidence/contradictory social volume is not a trade or alert signal.
- Latest example: BTC 58 mentions, NEUTRAL, confidence 10/100.

## On-chain v0.7.0

Working components:

- Bitcoin mempool count, vsize, total fee and four fee levels from mempool.space.
- Chain TVL for Ethereum, Solana, BSC, Arbitrum, Base, Avalanche and Sui from DefiLlama.
- Persistent idempotent SQLite observations.
- Eight-chain, seven-day historical TVL bootstrap: 64 rows, zero errors.
- Approximate 24h analyzer with accepted span 18-30h.
- WARMING_UP for insufficient history; NO_DATA for zero/unusable data.
- Collector + analyzer run together in a 15-minute isolated cycle.
- Latest validation: seven chain TVLs STABLE; Bitcoin metrics WARMING_UP; Celestia NO_DATA.
- Automatic cycle: SUCCESS | metrics=14 | analyzed=15 | source_errors=0.

Scope boundary:

- v0.7.0 covers chain-level liquidity and Bitcoin network health.
- Token transfers, exchange inflow/outflow and large-wallet movements remain future expansion/Whale Tracker scope.
- WARMING_UP and NO_DATA metrics never enter decisions or alerts.
- RISING/FALLING/STABLE is operational context, not a standalone trade signal without validation.

## Notification policy

- Daily investment-committee briefing: 11:00 Europe/Istanbul.
- Critical-development watch: hourly and silent without decision-relevant events.
- ChatGPT task notifications stay enabled.
- Codex turn-completion push notifications are disabled.
- Telegram remains optional, not the primary user channel.

## Research discipline — DO NOT BREAK

- Never leak future/challenge/exam outcomes into model selection.
- Do not chase small-n hit rates.
- Frozen Forward Shadow parameters stay unchanged during forward evaluation.
- Low-confidence Social or immature On-chain data must not drive alerts/trades.
- Parallel validation must not stall the main product roadmap.

## Next session priority

1. Do not rebuild completed News, Social or On-chain modules from scratch.
2. Let Forward Shadow, Social Sentiment and On-chain continue collecting automatically.
3. Begin v0.8.0 Macro Agent with the smallest working keyless/official data collector.
4. Test real data before adding macro interpretation.
5. Preserve the current live cycle and notification policies.

## Working style

The assistant manages project direction and performs every inspection/edit it can do itself. Ask the user only for actions that genuinely require the user's local Mac. Give one local action at a time; test and verify before moving on.

## Suggested opening next session

**“Dostum, AG Crypto Analyst’a kaldığımız yerden devam edelim.”**
