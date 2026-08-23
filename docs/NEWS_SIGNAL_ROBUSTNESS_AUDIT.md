# News Signal Robustness Audit

This audit is PAPER_ONLY and cannot enable real orders.

The news direction is measured after 10 bps round-trip fees and 5 bps
round-trip slippage. Observations are split chronologically 70/30, keeping the
last 30% unseen. Impact and confidence bands are diagnostics only; the audit
does not select the best-looking band. The always-long baseline pays the same
cost.

Market regime is the trailing BTC 24-hour return, calculated only from candle
closes available at entry. Above +1% is UP, below -1% is DOWN, and the rest is
SIDEWAYS. A horizon is rejected unless unseen validation has at least 30 rows,
positive net return, positive edge over always-long, and at least two regimes
with 10 observations each.

Even when those evidence gates pass, the result remains
`PAPER_ONLY_OBSERVE`; `production_eligible` and `real_orders_allowed` are
always false. The JSON result is written to
`reports/news_signal_robustness_audit.json`.
