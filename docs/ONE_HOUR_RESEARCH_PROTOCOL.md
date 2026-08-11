# AG Crypto Analyst — 1h Research Protocol

Status: LOCKED BEFORE SECOND HOLDOUT DATA IS COLLECTED

## Confirmed reference results

- Development benchmark (2026-05-12 onward): 1h hit rate 69.3%, directional return +0.261%.
- First locked validation (2026-02-11 through 2026-05-11): 1h hit rate 60.0%, directional return +0.129%.
- 4h and 24h are not production-grade based on the first locked validation and must remain research-only.

## Research objective

Improve the 1h News Intelligence signal without contaminating independent validation data. The goal is not to maximize an in-sample score; the goal is to find rules that preserve positive edge in unseen periods.

## Locked rules

1. The first validation window (2026-02-11 through 2026-05-11) is spent and will never again be used to choose or tune policy parameters.
2. The previous development benchmark (2026-05-12 onward) remains a historical reference and is not relabeled as independent validation.
3. A new, older historical block must be collected before any further policy tuning is accepted as validated.
4. The next unseen holdout block will be the newly collected oldest 90 successful archive days. Its outcomes must not be inspected or used for parameter selection before the candidate 1h policy is frozen.
5. Candidate research may use only the already-seen periods and training data that existed before this protocol was locked.
6. 1h is the primary optimization horizon. 4h and 24h must be analyzed separately and may not influence 1h policy selection.
7. Any candidate policy must preserve leakage control: only outcomes completed before each decision time may be used for training.
8. Small-sample candidate rules require shrinkage/reliability penalties and minimum observation counts.
9. Final acceptance requires positive directional return and a meaningful hit-rate edge in the second untouched holdout. A single attractive backtest is insufficient.
10. Tracking-list coins remain priority monitoring targets, not a restriction on the opportunity universe. New or previously unseen coins may qualify when data supports a signal.

## Next data step

Collect 90 additional successful GDELT archive days immediately before the current oldest stored archive day. These 90 days are reserved as SECOND HOLDOUT and must remain untouched by policy tuning until the candidate 1h policy is frozen.

## Second holdout handling

After collection:
- historical news may be ingested and mechanically classified by the already-existing analyzer;
- market data may be downloaded;
- but no exploratory performance analysis, policy search, or threshold tuning may be performed on those 90 days before the candidate policy is frozen;
- the second holdout backtest will then run once with the frozen policy and report hit rate, directional return, sample count, and confidence intervals where practical.
