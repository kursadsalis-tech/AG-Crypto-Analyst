# AG Crypto Analyst — Session Handoff (2026-08-11)

## Current product state

- Historical news dataset expanded back to 2025-08-15.
- Leakage-safe rolling research laboratory completed.
- Frozen 1h news policy established: confidence>=70, direction=negative.
- Weighted multifactor research reached 82.0% frozen-EXAM hit rate (n=50) vs NEWS BASE 77.6% (n=76), robustness status ROBUST_B.
- Adaptive dual-champion experiment completed; multifactor remains research/shadow candidate, not blindly promoted to production.
- Forward Shadow Validation V2 is locked at REGIME_AWARE_FORWARD_V2, cutoff=0.249, horizon=1h.
- V2 forward start seal: 2026-08-11T01:29:56.397166+00:00. Pre-start V1 predictions are invalid and must never be mixed into V2 results.
- `analysis.forward_shadow_cycle` runs automatically on macOS LaunchAgent every 5 minutes (`com.agca.forward-shadow`). LaunchAgent was verified: runs occurred and last exit code was 0.
- Live cycle performs: news collection -> news analysis -> forward shadow validation.
- Coin recognition includes watchlist aliases plus dynamic Binance spot-USDT ticker discovery.
- Product bridge exists for Forward Shadow output. Telegram was explored but is NOT the primary notification channel; ChatGPT scheduled notifications remain the intended user-facing report/alarm channel.

## ChatGPT notification policy

Two ChatGPT tasks were re-enabled on 2026-08-11:

1. Daily crypto briefing at 11:00 Europe/Istanbul for watchlist: ARKM, FIDA, WLD, TIA, ETHFI, BIO, LQTY, ILV, SLP, UMA, IO. Forward Shadow results should be included when available.
2. Critical development watch, hourly condition check. It must remain silent when nothing decision-relevant exists. Alert only for material events such as major unlock/security/listing/delist/whale/on-chain/market-structure events or a genuinely high-confidence AGCA/Forward Shadow signal.

Important limitation: ChatGPT critical-development checks are hourly, so user-facing notification is not guaranteed within minutes. The local AGCA Forward Shadow collector itself runs every 5 minutes.

## Research discipline — DO NOT BREAK

- Never leak challenge/exam/future outcomes into model selection.
- Learn -> unseen challenge -> promote/reject -> frozen exam -> roll forward.
- User proposed adaptive learning principle: if performance is below target, weights/parameters may be changed and re-evaluated inside the allowed learning/challenge research process; performance targets may progressively tighten. This must never contaminate frozen unseen evaluation.
- Do not chase isolated high hit rates or small-n results.
- Prefer repeated frozen-EXAM advantage, robustness, sample size and regime stability.
- Existing architecture and prior decisions must be reused; do not keep redesigning the product or delay shipping through unnecessary architecture work.

## Next session priority

Do NOT restart historical research from scratch. Let Forward Shadow V2 accumulate genuinely new post-seal observations.

Next work should be product-oriented:
1. Verify that Forward Shadow V2 has begun recording genuine post-start predictions/results once qualifying new news appears.
2. Inspect scoreboard/sample growth and data integrity without changing the locked model.
3. Ensure the daily briefing and critical alarm logic consume meaningful Forward Shadow evidence without creating noisy alerts.
4. Continue the broader AG Crypto Analyst product roadmap only after preserving these working components.

## Working style

Guide the user one terminal/action step at a time. Do not give several technical steps at once. The assistant manages the project direction; the user executes local terminal steps and returns output. Preserve old decisions and compatibility between modules.

## Suggested opening next session

User can simply say: **“AG Crypto Analyst’a kaldığımız yerden devam edelim.”**

At that point, read this handoff/current repo state first and continue from the Next session priority rather than asking the user to reconstruct the project history.
