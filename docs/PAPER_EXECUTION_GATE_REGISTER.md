# AGCA PAPER Execution Gate Register

This register is binding for the active behavior-based PAPER path. A gate may
not silently move between categories. New hard vetoes require explicit evidence,
a regression test, and an explanation of why sizing cannot contain the risk.

| Gate | Category | Current effect | Product judgment |
|---|---|---|---|
| PAPER_ONLY / real-order authority | Real-capital safety | Real orders impossible | Permanent until separate user approval and evidence gates |
| Corrupt, missing, future or stale evidence | Evidence integrity | Hard veto | Required; fabricated or unavailable evidence cannot form a trade |
| Missing dynamic asset risk record | Evidence integrity | Hard veto plus missed-opportunity record | Required until valid risk measurements exist |
| Unexecutable liquidity / exchange minimum | Execution feasibility | Hard veto | Required; a fill that could not execute is not a PAPER trade |
| Missing executable source | Execution feasibility | Binance Spot timestamped ticker WebSocket and Binance USD-M bookTicker are the execution sources; unavailable source blocks fill without OKX/last/candle fallback | The checkpoint already routes realtime Spot management through Binance; the former OKX-Spot management check is obsolete. USD-M OKX events remain policy triggers, with fresh Binance execution quotes required before fills |
| Volatility, range, drawdown, recent direction | Position sizing | Reduces cap; valid liquid extremes receive a small cap | Must never become a context-blind PAPER asset veto |
| Global market tail stress | Portfolio sizing | At most 2% total, 1% alt, 0.25% per position and one new position | Valid data under stress remains learnable; only `DATA_BLOCKED` stops all PAPER entries |
| Five closed samples, one UTC day, positive cost-after mean | Hypothesis formation | Allows small exploration, not promotion | Not a safety claim; must be reviewed from accumulated PAPER outcomes |
| 30 observed samples plus 10 own closed PAPER trades across three days, 55% calibrated wins, +0.05% calibrated mean edge | Promotion evidence | Only changes trusted/promoted status and size | Own cost-after execution must confirm observational evidence; this must never block the smaller exploration path |
| Own closed PAPER execution feedback | Adaptive ranking and sizing | Bayesian-shrunk cost-after results rerank matching event/pattern/direction candidates and adjust the next experiment size within 0.5x-1.25x | A small losing sample reduces exposure without becoming a hard veto; future, open, ineligible, or non-PAPER outcomes cannot enter calibration |
| Independent minute PAPER cycle | Runtime availability | Selection/fill/close/learning runs from `agca-paper-selection.timer`, outside the heavy product and Unified Forecast process | The cloud worker must not also invoke selection; no new close means expensive aggregate maintenance is skipped, while outcome calibration remains active |
| Book/taker relationship | Learnable market context | Aligned and opposing/absorption patterns can both be learned | Opposing signs are not automatically discarded |
| Minimum PAPER notional | Execution feasibility | Fixed 10 USDT floor | The former 1% equity floor was an invalid hidden learning veto and is removed |
| Six open positions / three new per cycle / 60% exposure | Portfolio sizing | Defers additional candidates | Capacity control, not a statement that the opportunity is invalid |
| Existing active symbol / already selected decision | Idempotency | Does not duplicate exposure or thesis | Required for one-agent, one-portfolio accounting |
| Immediate causal PAPER entry | Execution realism | Each fill attempt acquires a new timestamped executable quote: BUY at ask, SELL at bid; quote and selected-side liquidity must be <=2 seconds old, finite, positive and not future-dated. The existing 10-minute entry deadline remains | Decision 026: last/candle/mark prices and old immutable observations cannot authorize fills. Sizing cannot repair absent executable evidence. Missing exchange rules also fail closed |
| Real-time open-position management | Execution and loss containment | Existing Binance Spot and OKX swap streams drive policy. Quote and liquidity timestamps are separate from trade timestamps; a fresh trade cannot renew old executable evidence. Due legacy closes use the same manager/accounting path | Protective stop, thesis invalidation, partial profit taking, trailing stop and horizon timeout remain unchanged. Every fill, including liquidation, requires executable evidence |
| Protective stop | Loss containment | Entry risk snapshot creates a bounded 0.5%-5% initial PAPER stop before management begins | Missing entry risk evidence is visible and fail-closed; no default stop may be invented |
| Funding reconciliation | Accounting integrity | Exit execution never waits for the funding transport. Exact exposed interval and quantity are frozen, the position is temporarily excluded from learning, and funding is reconciled asynchronously before outcome review | A reporting dependency may not delay a protective exit, but missing cost may not silently become zero |
| Position event audit | Learning integrity | Stop initialization/change, opposing thesis evidence, partial realization, rejected exit and final close are immutable local SQLite events with source hash and `real_order_allowed=0` | Replayed or older market events cannot mutate cash twice |
| External causal event match | Strategy context | Event-linked setups remain available but are not mandatory | Endogenous `MARKET_STRUCTURE` samples and trades cover order-flow opportunities without reconnecting Unified Forecast |
| Independent signal routes | Learnable strategy context | A cost-positive, point-in-time PRICE_ACTION, MARKET_BEHAVIOR/order-flow, or causal external event hypothesis may open a small PAPER experiment by itself | Confluence is a sizing input, never a requirement that chart, news and book must all agree before learning can occur |
| PAPER leverage tier | Position sizing and risk learning | Outcome-blind independent directional sources plus promotion evidence select 1x-5x; one weak valid source starts at 1x and 0.5% equity margin, while five steps cap at 5x and 2% equity margin | Explicitly approved by the user on 2026-09-02. Leverage is PAPER-only; duplicate/opposing evidence cannot inflate it, gross exposure remains inside asset and 60% portfolio caps |
| Margin and gross exposure accounting | Accounting integrity | Cash reserves isolated margin plus entry fee; quantity, fees, spread, slippage and funding use gross exposure; close returns remaining margin plus P&L after costs | A leverage label without separate margin/exposure accounting is forbidden |
| Leveraged liquidation | Loss containment and learning | A threshold breach triggers liquidation intent; its PAPER fill still requires fresh side-aware bid/ask and visible liquidity, with adverse slippage and liquidation fees. Partial fills retain remaining exposure | The threshold is a trigger, never a fabricated fill price. Missing/stale quotes or liquidity produce a durable rejection, including during liquidation |

Every selection cycle records its core status, candidate count, selected count,
capacity, available exposure, and rejection reason counts locally in
`paper_behavior_selection_runs`. A zero-trade cycle without an explicit recorded
cause is an operational failure, not a healthy outcome.

Canonical timestamps remain UTC for deterministic joins and audit. Every
operator-facing PAPER time must render Istanbul time first and include UTC in
parentheses; an unlabeled UTC timestamp is not an acceptable user display.

Unified Forecast is baseline-only. This register does not authorize a second
agent, a second portfolio, automatic policy promotion, capital changes, or real
orders. The 1x-5x authority applies only to simulated isolated-margin PAPER
positions.

## Decision 026 execution evidence (2026-09-19)

`paper_position_events.evidence_json` stores the quote sides/source, market,
observation/decision and quote timestamps, quote age, selected side/reference,
actual fill, quantity/notional, fee/slippage, partial/rejected status and reason.
The existing SHA-256 covers that JSON; `real_order_allowed=0` remains mandatory.
The additive column is local SQLite only; no remote mirror schema is changed.
Old events without JSON remain historical evidence and are not rewritten.

Actual fills apply no synthetic spread on top of bid/ask. Real spread is recorded
as evidence; adverse slippage/latency plus tick rounding may only worsen the
selected side. Selected-side visible base quantity and participation remain
limits. Selection retains its existing rule-feasibility estimate in an explicitly
non-executing helper that cannot return a fill; learning/ranking/risk policy is
unchanged.

USD-M OKX analysis sizes are contracts. They are not treated as Binance base-unit
liquidity: an attempted exit requires fresh Binance bookTicker evidence, as does
entry, consistent with the existing Binance exchange-rule/funding contract.
If that endpoint is inaccessible (including HTTP 451), the result is a rejection,
not a provider/price fallback. No deployment or live-source availability claim is
made by the local regression suite.

Public field provenance:
- [Binance USD-M bookTicker](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Symbol-Order-Book-Ticker)
- [Binance Spot ticker WebSocket](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md#individual-symbol-ticker-streams)
