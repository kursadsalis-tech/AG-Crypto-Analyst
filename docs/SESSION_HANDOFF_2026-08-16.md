# AG Crypto Analyst — Session Handoff 2026-08-16

## Current product state

- Product remains `SHADOW_ONLY`.
- Live Decision Engine, Risk Manager and alert policy were not changed.
- V1 price-only, V2 futures/microstructure and V3 interaction challengers were rejected.
- The stop rule remains active: do not reopen indicator, threshold or model-grid search on the same price/derivatives history.

## Exact-time catalyst result

The frozen Binance listing/delisting event study completed on 82 independent
articles and 172 usable event rows. The fixed rule was listing=`AL`,
delisting=`SAT/REDUCE/AVOID`, with 10 bps cost and a chronological 70/30 split.

Validation results:

- 1h: 56.0% article hit, +1.510% average utility, weekly clustered lower bound -0.895% — rejected.
- 4h primary: 60.0% article hit, +3.609% average utility, weekly clustered lower bound -0.594% — rejected.
- 24h: 44.0% article hit, +1.430% average utility, weekly clustered lower bound -5.478% — rejected.

Interpretation: 4h is an interesting historical observation, not validated edge.
The average is positive but not stable across time clusters. No threshold,
event subtype or model search will be opened on these same outcomes.

## Next bounded research decision

The next single research path is a market-implied option distribution for BTC
and ETH. It is intentionally narrow:

- source: Deribit public option instruments and current book summaries;
- first supported horizon: 24h primary;
- outputs, if later built: median target, 80% range and cost-aware up/down probabilities;
- history: forward-only unless a separate point-in-time historical surface is proven;
- no claim that an implied distribution is forecasting edge before forward calibration;
- no expansion to altcoins, 4h or 1h before BTC/ETH 24h earns evidence.

`data.options_implied_feasibility` was added as a source/liquidity audit only.
It reads no future outcome and computes no probability, target, return or
backtest. Passing it authorizes only a forward collector design; failing it
ends this option-source path without architecture churn.

The first Mac run reached neither BTC nor ETH on Deribit and timed out. This
was a provider connectivity failure, not a liquidity/data verdict. Version
1.1 therefore keeps Deribit as the first source and automatically falls back
to Bybit's unauthenticated V5 option instruments/tickers endpoints. Both
providers are audited under the same frozen BTC/ETH liquidity gates; a partial
surface cannot pass.

The Bybit fallback was reachable only up to TLS verification on the first Mac
run; Python's framework trust store lacked the issuer chain. Version 1.1.1
uses the installed `certifi` CA bundle when available and otherwise retains
the standard verified SSL context. Certificate verification is never disabled.

## Next command

```bash
git pull origin main && python3 -m data.options_implied_feasibility
```

Send the complete terminal output. The next decision will be made from the
reported BTC/ETH active contracts, expiries, two-sided quotes and liquid
strikes near 24 hours.
