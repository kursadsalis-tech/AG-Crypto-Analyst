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

## First real-data result and root cause

The first audit produced 289 chronological training rows and 124 validation
rows. Net validation edge versus always-long was -0.0089% at 1h, -0.5473% at
4h and -0.8990% at 24h. Validation contained 39 positive rows per horizon but
only 2-3 negative rows, no DOWN regime, and only 2-3 SIDEWAYS rows. The
negative slice was sharply adverse, while the positive slice had exactly zero
edge over always-long. Impact and confidence bands were not monotonic.

This matches the classifier implementation: `confidence_score` measures the
number of coin, event and keyword matches, not calibrated directional
accuracy. Direction is a positive-versus-negative keyword count, without
learned price-effect semantics or regime adjustment. Therefore high scores
must not be treated as stronger tradable direction.

The runtime forward gate is fail-closed with
`NEWS_DIRECTIONAL_ROBUSTNESS_REJECTED`. News direction remains observable for
research, but it cannot create a production-eligible action. Reopening requires
new chronological evidence with both directions, at least two adequately
sampled regimes, positive net return and positive always-long edge in both
train and validation. A reviewed code change is required; score thresholds
alone cannot reopen it.
