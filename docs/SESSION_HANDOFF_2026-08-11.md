# AG Crypto Analyst — Session Handoff (2026-08-11, v0.6.0)

## Current product state

- Product version: v0.6.0.
- Completed: Market Data, Market Analyzer, Technical Analysis, News Intelligence, Social Sentiment.
- Forward Shadow V2 remains locked: REGIME_AWARE_FORWARD_V2, cutoff=0.249, horizon=1h.
- Forward start seal: 2026-08-11T01:29:56.397166+00:00. INVALID_PRESTART_SEED records never count.
- Current genuine forward scoreboard: NEWS BASE settled=13, hit=23.1%, directional return=-0.050%; MULTIFACTOR settled=5, hit=20.0%, directional return=-0.013%.
- Sample is small and weak; do not modify the frozen model or approve a production Champion from these results.
- Mac LaunchAgent com.agca.forward-shadow runs the live cycle every 5 minutes.
- Live cycle: NEWS -> FORWARD SHADOW -> ALERT BRIDGE -> SOCIAL SENTIMENT.
- Social collection has a separate 15-minute cadence and safely skips intermediate cycles.

## Social Sentiment v0.6.0

Working components:

- Combined Reddit crypto-community RSS, up to 100 current posts per request.
- SQLite persistent observations with global external_id deduplication.
- Title + post-content analysis.
- Watchlist aliases plus dynamic Binance Spot USDT ticker discovery.
- Ambiguous ticker protections, including the LINK/common-word failure found in testing.
- Rolling 24h coin sentiment aggregation.
- Confidence based on sample size, directional share and positive/negative consensus.
- Low-confidence or contradictory social volume is not treated as a trade or alert signal.
- Rate-safe 15-minute cycle integrated without allowing social failures to stop News/Forward Shadow.

Latest validated social example:

- BTC: mentions=58, score=-0.052, NEUTRAL, confidence=10/100.
- ETH: mentions=11, score=-0.061, NEUTRAL, confidence=10/100.
- XRP: mentions=4, score=-0.250, NEGATIVE, confidence=10/100; too weak for production use.

## Notification policy

- Daily investment-committee briefing: 11:00 Europe/Istanbul.
- Critical-development watch: hourly and silent when nothing decision-relevant exists.
- ChatGPT task notifications stay enabled.
- Codex turn-completion push notifications were disabled to stop every development reply reaching the user's phone.
- Telegram is optional infrastructure, not the primary user channel.

## Research discipline — DO NOT BREAK

- Never leak future/challenge/exam outcomes into model selection.
- Do not chase small-n hit rates.
- Frozen Forward Shadow weights/cutoff stay unchanged during forward evaluation.
- Social confidence cannot be inferred from mention count alone.
- Low-confidence social data must not enter alerts, trading recommendations or Decision Engine decisions.
- Validation continues in parallel and must not stall the main product roadmap.

## Next session priority

1. Do not restart News/Forward or Social architecture work from scratch.
2. Let Forward Shadow and Social Sentiment continue collecting automatically.
3. Begin the next main product module: v0.7.0 On-chain Agent.
4. Build the smallest working on-chain collector first, test it with real data, then add interpretation.
5. Preserve compatibility with the current live cycle and notification rules.

## Working style

The assistant manages project direction and performs every inspection/edit it can do itself. Ask the user only for actions that genuinely require the user's local Mac. Give one local action at a time; test and verify before moving on.

## Suggested opening next session

**“Dostum, AG Crypto Analyst’a kaldığımız yerden devam edelim.”**
