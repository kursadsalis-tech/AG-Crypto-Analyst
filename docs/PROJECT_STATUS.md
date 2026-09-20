# AG Crypto Analyst — Current Product Status

## Product objective

AGCA is a live PAPER trading agent. Its job is not to maintain a fixed coin list,
produce scheduled committee reports, or maximize backtest output. While the market
is moving it must continuously:

1. observe current market behaviour and executable liquidity,
2. discover opportunities dynamically across the eligible market universe,
3. interpret evidence and risk at the time it forms,
4. decide and execute only in `PAPER_ONLY`,
5. manage open positions from live market state,
6. measure realised net economic outcome,
7. learn from eligible forward evidence and improve behaviour without silently
   granting itself real-order authority.

The economic objective is better risk-adjusted net trading performance. Research,
historical data and challengers are supporting evidence only; they are never a
parallel runtime authority.

## Canonical runtime

- Canonical branch: `main`
- Execution authority: `PAPER_ONLY`
- Real orders: disabled
- Live lifecycle authority: `runtime/paper_realtime_worker.py`
- Dynamic opportunity selection: `paper_trading/crypto_profit_agent.py`
- Executable-price contract: bid/ask + freshness + visible liquidity, fail closed
- Position management: `paper_trading/position_management.py`
- Learning: `paper_trading/trade_learning.py`
- Allocation: `paper_trading/strategy_allocator.py`
- Experience memory: `paper_trading/experience_memory.py`
- Risk tiering: `paper_trading/risk_tiering.py`
- Runtime persistence/mirror: `database/runtime_store.py`

No fixed coin cohort is product authority. The observed universe is formed
dynamically from eligible market behaviour plus symbols required to manage open
positions.

## Retired product authorities

The following paths/concepts are no longer runtime authorities and must not be
reintroduced as competing control loops:

- daily investment committee report generation,
- Telegram notification authority,
- scheduled hourly forecast job,
- Forward Shadow scheduled runtime/validator,
- Decision Shadow validator as a parallel decision authority,
- fixed-cohort launchd schedulers,
- legacy report-cycle orchestration.

Historical forecast/backtest/research modules may remain only when they provide
bounded evidence or regression value. They do not select trades, mutate live
policy, or block the realtime agent merely because an old reporting contract is
missing.

## Validation baseline

On 2026-09-20 the cleaned canonical product line passed GitHub Actions:

- focused PAPER integration: 83/83
- broad offline regression: 331/331
- total: 414/414
- canonical `main` workflow: SUCCESS

This is the current code-validation baseline, not a claim of live profitability
or external deployment health.

## Current priorities

1. Keep one realtime PAPER authority and remove remaining obsolete parallel
   runtime/reporting dependencies.
2. Verify opportunity discovery remains dynamic and is not constrained by legacy
   fixed watchlists.
3. Preserve fail-closed executable quote, liquidity, funding, stop and risk
   behaviour.
4. Evaluate learning/allocation only from clean, economically meaningful forward
   PAPER outcomes.
5. Promote no capability merely because a backtest looks good; require natural
   forward evidence and net-after-cost improvement.
6. Keep documentation limited to current product truth; history belongs in Git
   history/CHANGELOG rather than operational instructions.

## Explicit non-claims

A green CI run does not prove that an external VM, Supabase mirror, exchange
connection, or market-data source is currently healthy. Those states must be
verified from current runtime evidence before being described as operational.

AGCA remains PAPER-only until a separate, explicit real-trading authorization and
safety review is made.
