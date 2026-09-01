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
| Volatility, range, drawdown, recent direction | Position sizing | Reduces cap; valid liquid extremes receive a small cap | Must never become a context-blind PAPER asset veto |
| Global market tail stress | Portfolio sizing | At most 2% total, 1% alt, 0.25% per position and one new position | Valid data under stress remains learnable; only `DATA_BLOCKED` stops all PAPER entries |
| Five closed samples, one UTC day, positive cost-after mean | Hypothesis formation | Allows small exploration, not promotion | Not a safety claim; must be reviewed from accumulated PAPER outcomes |
| 30 samples, three days, 55% wins, +0.05% mean edge | Promotion evidence | Only changes trusted/promoted status and size | Must never block the smaller exploration path |
| Book/taker relationship | Learnable market context | Aligned and opposing/absorption patterns can both be learned | Opposing signs are not automatically discarded |
| Minimum PAPER notional | Execution feasibility | Fixed 10 USDT floor | The former 1% equity floor was an invalid hidden learning veto and is removed |
| Six open positions / three new per cycle / 60% exposure | Portfolio sizing | Defers additional candidates | Capacity control, not a statement that the opportunity is invalid |
| Existing active symbol / already selected decision | Idempotency | Does not duplicate exposure or thesis | Required for one-agent, one-portfolio accounting |
| Exact next hourly open | Execution realism | Selected thesis waits for a reproducible future fill | Active latency limitation; must be measured and replaced by causal minute execution if it materially loses setups |
| External causal event match | Strategy context | Event-linked setups remain available but are not mandatory | Endogenous `MARKET_STRUCTURE` samples and trades cover order-flow opportunities without reconnecting Unified Forecast |

Every selection cycle records its core status, candidate count, selected count,
capacity, available exposure, and rejection reason counts locally in
`paper_behavior_selection_runs`. A zero-trade cycle without an explicit recorded
cause is an operational failure, not a healthy outcome.

Canonical timestamps remain UTC for deterministic joins and audit. Every
operator-facing PAPER time must render Istanbul time first and include UTC in
parentheses; an unlabeled UTC timestamp is not an acceptable user display.

Unified Forecast is baseline-only. This register does not authorize a second
agent, a second portfolio, policy promotion, capital changes, or real orders.
