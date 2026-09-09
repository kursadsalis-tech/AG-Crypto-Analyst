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
| Missing OKX Spot management instrument | Execution feasibility | Hard veto with `OKX_SPOT_MANAGEMENT_UNAVAILABLE` cancellation | A Binance Spot PAPER fill must not open unless the same instrument has a fresh, liquid, price-consistent OKX Spot route for continuous management; sizing cannot repair a missing management venue |
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
| Immediate causal PAPER entry | Execution realism | The minute worker attempts the fill in the same cycle as selection using an immutable, <=30-second-old OKX USDT-swap ticker cross-checked against Binance spot; unavailable or >2% divergent evidence fails closed | Waiting for the next hourly open was a research-era delay that discarded the AI timing advantage. The fixed forecast/research candle cohort is never an execution universe; entry capture retains a 10-minute causal limit so missing data cannot occupy capacity for hours |
| Real-time open-position management | Execution and loss containment | Every OPEN position must be attached to the dedicated OKX public WebSocket manager. Fresh ticker, books5 and trades events drive the PAPER policy; stale events, duplicate timestamps and missing visible liquidity fail closed | The former unconditional horizon wait is forbidden. Horizon is only a maximum timeout; protective stop, thesis invalidation, staged profit taking and trailing stop may act earlier |
| Protective stop | Loss containment | Entry risk snapshot creates a bounded 0.5%-5% initial PAPER stop before management begins | Missing entry risk evidence is visible and fail-closed; no default stop may be invented |
| Funding reconciliation | Accounting integrity | Exit execution never waits for the funding transport. Exact exposed interval and quantity are frozen, the position is temporarily excluded from learning, and funding is reconciled asynchronously before outcome review | A reporting dependency may not delay a protective exit, but missing cost may not silently become zero |
| Position event audit | Learning integrity | Stop initialization/change, opposing thesis evidence, partial realization, rejected exit and final close are immutable local SQLite events with source hash and `real_order_allowed=0` | Replayed or older market events cannot mutate cash twice |
| External causal event match | Strategy context | Event-linked setups remain available but are not mandatory | Endogenous `MARKET_STRUCTURE` samples and trades cover order-flow opportunities without reconnecting Unified Forecast |
| Independent signal routes | Learnable strategy context | A cost-positive, point-in-time PRICE_ACTION, MARKET_BEHAVIOR/order-flow, or causal external event hypothesis may open a small PAPER experiment by itself | Confluence is a sizing input, never a requirement that chart, news and book must all agree before learning can occur |
| PAPER leverage tier | Position sizing and risk learning | Outcome-blind independent directional sources plus promotion evidence select 1x-5x; one weak valid source starts at 1x and 0.5% equity margin, while five steps cap at 5x and 2% equity margin | Explicitly approved by the user on 2026-09-02. Leverage is PAPER-only; duplicate/opposing evidence cannot inflate it, gross exposure remains inside asset and 60% portfolio caps |
| Margin and gross exposure accounting | Accounting integrity | Cash reserves isolated margin plus entry fee; quantity, fees, spread, slippage and funding use gross exposure; close returns remaining margin plus P&L after costs | A leverage label without separate margin/exposure accounting is forbidden |
| Leveraged liquidation | Loss containment and learning | Each leveraged OPEN has an immutable maintenance-margin liquidation threshold; a fresh price crossing it closes at the threshold, charges the exchange liquidation fee and records the loss | Protective stops normally precede liquidation, but a gap may liquidate and must remain visible learning evidence rather than being hidden |

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
