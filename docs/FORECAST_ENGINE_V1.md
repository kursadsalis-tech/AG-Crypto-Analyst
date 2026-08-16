# Unified Forecast Engine V1

## Product objective

The runtime now produces a real probabilistic forecast bundle for every tracked
symbol at 1h, 4h and 24h: last closed price, median target price, 80% prediction
interval, net UP/DOWN/NEUTRAL probabilities, a shadow AL/SAT/BEKLE opinion and a
separate Risk Manager veto.

This closes the missing product loop. Research confidence, risk permission and
price forecasts are no longer conflated. A valid forecast is always persisted;
risk can veto its action but cannot alter its target or probabilities.

## Frozen first runtime model

`POOLED_RIDGE_DISTRIBUTION_RUNTIME_V1_FIXED` reuses the audited point-in-time
price/distribution implementation from `analysis.forecast_core_lab`. It is
bootstrapped once from chronological FIT/CAL data, sealed with SHA-256 and held
for 30 days. The model is historically unvalidated and therefore starts in
`FORWARD_SHADOW`; it is not represented as a successful or production model.

The purpose of this first runtime artifact is to create the evidence stream
needed to improve or replace it without repeatedly changing architecture.
V2/V3/options/catalyst experiments do not block the runtime and do not silently
become its champion.

## Runtime invariants

- 13 symbols x 3 horizons = 39 forecast rows, or an explicit `NO_FORECAST`
  reason for every missing row.
- Features use only exact closed 1h candles at or before `as_of`.
- Probability means a move beyond 10 bps round-trip cost plus 5 bps minimum
  meaningful movement.
- Target is the q50 price; q10-q90 is the 80% prediction interval.
- `SAT` means reduce/exit/avoid for spot holdings, never an assumed short.
- Risk may only change AL/SAT to BEKLE.
- Every due forecast is settled with entry, exit, actual class, target error,
  interval hit, Brier score, direction hit and action utility.
- Product alerts remain disabled until the existing forward promotion gates are
  met independently for a horizon.

## Commands

First run (trains and seals the model, then opens 39 forecasts):

```bash
python3 -m decision_engine.forecast_cycle
```

Subsequent full live cycle:

```bash
python3 -m analysis.forward_shadow_cycle
```

Force a new artifact only after an explicit model-version decision:

```bash
python3 -m decision_engine.forecast_engine --retrain
```


## Learning and report loop (v1.1)

Every new forecast now persists the exact named model-feature vector, scale and
market regime. The most recent Decision Engine evidence from news, social,
on-chain, macro, whale and risk agents is stored as `OBSERVED_ONLY`; it is
explicitly not treated as a model input until a separate causal challenger is
frozen and validated.

At settlement the engine writes a structured diagnosis: `TARGET_ALIGNED`,
`DIRECTION_CLASS_MISS`, `TARGET_MAGNITUDE_MISS`,
`INTERVAL_MISS_UP` or `INTERVAL_MISS_DOWN`, plus volatility, volume, BTC shock,
regime and abstention tags. The daily investment committee report v1.3 shows
the latest 39 forecasts, latest settled result for each coin/horizon, target
error, diagnosis counts and horizon-level forward performance.


## Evidence-gated self-correction (v1.2)

`analysis.forecast_learning_cycle` reviews settled forecasts without blacklisting
coins or mutating weights after a single miss. It groups errors by horizon,
regime, structured diagnosis and point-in-time context tags. A pattern is
eligible for a bounded challenger hypothesis only after at least 30 events
across three independent ISO weeks; the learning review remains
`COLLECTING_EVIDENCE` until at least 100 settled forecasts exist.

The review describes statistical association, never an invented causal claim.
It may propose a direction-calibration, target-scale, interval-recalibration or
context-feature challenger. It cannot replace the frozen champion. A real
correction must be trained separately, evaluated against the unchanged champion
on untouched forward evidence and promoted only if it demonstrably improves
calibration, target error and cost-adjusted utility. News/social/agent evidence
remains observed-only until such a challenger passes.


## Automatic hourly operation

The product does not depend on manual terminal runs. The macOS LaunchAgent
`com.agca.hourly-forecast` runs at minutes 02, 07 and 12 of every hour. It
refreshes Risk Manager, settles every exactly-due 1h/4h/24h forecast, opens one
idempotent 39-row bundle for the fresh hourly close and runs the Forecast
Learning Review. The later two attempts are safe retries for delayed exchange
data; an already-opened hourly bundle is not duplicated.

One-time installation:

```bash
python3 -m scripts.install_hourly_forecast_launchd
```

The Mac must be awake and the user logged in. Output is written to
`reports/hourly_forecast_launchd.log`; failures are written to
`reports/hourly_forecast_launchd_error.log`.
