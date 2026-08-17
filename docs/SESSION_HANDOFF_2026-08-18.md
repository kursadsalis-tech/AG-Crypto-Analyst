# AG Crypto Analyst — Session Handoff 2026-08-18

## Session close state

- User ended the session intentionally to rest.
- Do not restart architecture discovery or ask the user to repeat the project history.
- Opening phrase remains: “Dostum, AG Crypto Analyst’a kaldığımız yerden devam edelim.”
- The next session must begin from the cloud-runtime decision below and the Crypto Profit Agent report status.

## Product and infrastructure state

- Unified Forecast Engine and Crypto Profit Agent exist and remain `SHADOW_ONLY` / `PAPER_ONLY`.
- Hourly forecast work currently runs with the Mac LaunchAgent when that machine and user session are available.
- Supabase is the remote operational data store/mirror; it is not the always-on Python compute host.
- Provider-neutral Ubuntu/systemd cloud runtime code has already been prepared. Do not rebuild the architecture.
- The unresolved task is selecting and activating an always-on compute provider without silently expanding privacy, marketing-consent or payment obligations.

## Oracle Cloud decision — closed

On 2026-08-18 the Oracle Cloud signup flow was stopped.

Observed facts:

- With home-region confirmation, Oracle terms and international data-transfer acknowledgement selected, the Continue button remained disabled while marketing consent was unchecked.
- The flow then requested address, telephone and payment-card information.
- The user does not consent to accepting marketing solely to unlock signup and does not want to provide personal/payment data under that condition.

Binding decision:

- Do not send the user back through the same Oracle signup flow.
- Do not ask for or expose address, phone, card or other credentials in chat/screenshots.
- Before proposing another provider, verify its actual free-tier duration, compute suitability, sleep limits, billing/card requirement, marketing consent and privacy requirements.
- Distinguish clearly between free storage and always-on compute.
- Prefer the smallest provider-neutral deployment step; do not redesign the product.

## Crypto Profit Agent governance

- The main assistant/orchestrator remains the system owner. Authority is not delegated to the Agent.
- The Agent is allowed to learn, explain, challenge with evidence and propose changes, but it cannot promote itself, alter production policy or place real orders.
- Trading remains fully virtual. No real-money execution without explicit user approval and a separate promotion decision.
- The simulation must stay close to executable exchange reality: exact timestamps, bid/ask or declared spread proxy, maker/taker fees, slippage, latency, partial/rejected fills, liquidity, funding/borrow where applicable, exchange filters and fail-closed missing data.
- A single loss must not trigger arbitrary weight changes. Changes require root-cause evidence, a frozen challenger, historical validation and fresh forward evidence.

## Daily 03:00 report contract

The Crypto Profit Agent’s Europe/Istanbul 03:00 report must include at minimum:

- starting balance, cash, open-position value and ending equity;
- realized and unrealized P&L, both amount and percentage;
- every opened/closed position with symbol, side, size, signal/decision time, intended and filled entry, exit, holding period and reason;
- gross P&L, fees, spread/slippage, funding/borrow and net P&L;
- rejected/partial fills and data/source errors;
- trade count, win/loss count, exposure, drawdown and risk-limit events;
- short evidence-based self-review and proposed lesson; no autonomous model promotion.

The user receives only a very short summary after the report actually arrives:
current virtual budget/equity, daily P&L, trades, total fees and the single most important risk/lesson.
Never invent missing report values.

A temporary report watch is scheduled from 03:05 Europe/Istanbul for the 2026-08-18 report. It checks hourly for a limited window, stays silent until the report is actually available, and must not summarize the same report twice.

## Next-session first actions

1. Check whether the 2026-08-18 03:00 Crypto Profit Agent report arrived and whether its delivery path worked without the Mac.
2. If present, audit it against the report contract and give the user the promised very short summary.
3. If absent, diagnose the delivery/runtime dependency as a product fault; do not claim that the Agent worked.
4. Continue the existing cloud-runtime deployment by comparing only viable providers against the user’s privacy/payment constraints.
5. Do not reopen rejected model searches or create another architecture before the always-on runtime and reporting path are operational.
