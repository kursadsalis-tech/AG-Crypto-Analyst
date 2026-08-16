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
