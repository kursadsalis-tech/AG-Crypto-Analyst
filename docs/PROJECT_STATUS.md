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

On 2026-09-22 the first mandatory correctness package (P0) was merged to
canonical `main` through five isolated pull requests. The final P0 pull request
passed GitHub Actions with:

- focused PAPER integration: 107/107
- broad offline regression: 384/384
- canonical `main` head after P0: `42645f47a196224f0ce860dd55f61c30f2c90851`

The P0 package corrects five execution/economic defects without granting new
real-order authority:

1. volatility stop evidence is carried from the asset-risk snapshot into the
   position path and missing stop evidence fails closed;
2. an evidence-mature matching pattern is suspended from new exposure when at
   least 10 own closed PAPER trades across at least three independent days leave
   calibrated net expectancy non-positive;
3. adverse same-setup re-entry requires materially new evidence or at least a
   one-risk-unit price reset instead of a new decision id alone;
4. `PENDING_ENTRY` routes remain subscribed so a 1x LONG selected for SPOT can
   receive its own executable Spot quote, and cloud attestation now records the
   deployed git SHA/dirty state;
5. selection, risk sizing, reservation and simulated fills share one
   venue-specific taker-fee contract.

P0 live acceptance was completed on 2026-09-22/23 after two operational defects
found during deployment were also fixed: the installer now re-execs after its own
git update, cloud attestation is bound to the running worker process source (not
checkout state alone), and realtime housekeeping expires stale pending entries
without waiting for a market callback.

Canonical VM evidence at acceptance:
- checkout/process SHA: `483a8b5d9c818cf60d5f717665d14e124b8be69b`
- realtime worker: `CONNECTED`
- execution mode: `PAPER_ONLY`
- real-order authority: false
- tracked checkout dirty state: false
- stale IOSTUSDT SPOT pending entry was cancelled by runtime housekeeping
- no failed systemd units were observed and canonical timers were active

The local SQLite database remains authoritative. The Supabase mirror was still
lagging by a few minutes at the acceptance checkpoint, so remote-mirror freshness
must be treated as a separate operational concern rather than proof of runtime
failure.

## Current priorities

1. Verify the first natural post-P0 selections use the intended stop evidence,
   venue, fee contract and re-entry/suspension gates.
3. Audit opportunity discovery so the lightweight broad market universe is not
   collapsed into the small deep-analysis slot set.
3. Reuse existing causal technical and multi-timeframe capabilities in the active
   decision path only after latency/resource benchmarks prove they do not damage
   realtime operation; 4h evidence is context, not automatic direction authority.
4. Extend learning from underperformance detection toward bounded challenger
   generation/evaluation without creating a second live decision authority.
5. Keep high-frequency raw evidence in memory/local compact storage by default;
   do not expand Supabase/cloud cost without measured product benefit.

## Explicit non-claims

A green CI run does not prove that an external VM, Supabase mirror, exchange
connection, or market-data source is currently healthy. Those states must be
verified from current runtime evidence before being described as operational.

AGCA remains PAPER-only until a separate, explicit real-trading authorization and
safety review is made.
