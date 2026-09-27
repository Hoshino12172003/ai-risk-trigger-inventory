# Stage 3 risk-impact calibration protocol

## Status and scope

This protocol is frozen before examining Stage-3 calibration results. Stage 3
calibrates candidate demand-impact ranges and audits route-impact provenance; it
does not select final parameters, rerun the robust optimizer, train deep
learning, call an LLM, use M5, or modify Stage-1/Stage-2 artifacts.

Stage 3A is causal statistical calibration. Stage 3B is executed only if the
preregistered ML-necessity gate fires. Candidate values are never labeled
`FINAL_CALIBRATION`.

## Data and provenance

The input is the manifest-verified Favorita cleaned daily store-family panel.
Observed fields are sales, promotion counts, dates, store/city/state metadata,
and source retail context. Holiday flags, cleaned transactions, calendar fields,
weekly aggregation, lags, rolling baselines, uplift, and quantiles are derived.
Warehouses, inventory, capacities, routes, physical route capacity, and route
loss are `CALIBRATED_NOT_OBSERVED`.

Favorita is not warehouse-network data. It provides observed retail
demand-surge proxies, not livestream labels or logistics-disruption outcomes.

## Unit of analysis and complete-week rule

The unit is `store_nbr × product family × Monday-start week`. Daily sales and
promotion counts are summed; holiday flags are the weekly maximum. A row is
retained only when the source contains all seven dates for that store-family
week. Partial boundary weeks and incomplete weeks are excluded. This preserves
the previously verified store-family aggregation level while preventing
partial-week demand from being interpreted as a shock.

## Chronological split

- TRAIN: source dates 2013-01-01 through 2015-12-31.
- VALIDATION: 2016-01-01 through 2016-12-31.
- TEST: 2017-01-01 through 2017-08-15.

A weekly observation is assigned only if its entire Monday-Sunday interval lies
inside one split. Boundary-crossing weeks are excluded. TEST is unavailable to
baseline choice, promotion-stratum thresholds, quantile estimation, model
selection, and hyperparameter choice.

## Strictly causal baseline candidates

For every store-family series, sorted by week:

- B1: median of the previous 4 complete weekly observations.
- B2: median of the previous 8 complete weekly observations.
- B3: median of the previous 12 complete weekly observations.
- B4: demand at exactly `t-52 weeks`, enabled only when an exact 364-day key
  match is available; missing seasonal matches remain missing.

B1–B3 are implemented as `shift(1)` followed by a trailing rolling median.
No centered window, future interpolation, backward fill, or full-series local
baseline is permitted. A leakage audit compares every nonmissing rolling value
with an independently calculated median from dates strictly before `t`.

## Baseline selection

The normal-validation subset is fixed as:

- VALIDATION only;
- no weekly holiday/event flag;
- zero weekly promotion count;
- positive actual demand;
- candidate baseline greater than epsilon (`1e-9`);
- all enabled candidates nonmissing, so methods use an identical comparison
  sample.

For each candidate report MAE, median absolute percentage error, and median
relative bias `(actual-baseline)/baseline`. Select the lowest validation MAE.
Exact ties within `1e-12` use B1, B2, B3, then B4, which favors the shorter
rolling method. TEST is not read by the selector.

## Uplift and event proxies

For selected baseline greater than epsilon:

`uplift = (actual demand - baseline demand) / baseline demand`.

The mutually exclusive observed retail proxies are:

- P1: promotion active and no holiday/event;
- P2: holiday/event active and no promotion;
- P3: promotion and holiday/event overlap;
- P4: neither promotion nor holiday/event (normal).

These are retail demand-surge proxies, not livestream events. Promotion-active
observations are additionally classified low/medium/high using the 33rd and
67th percentiles of positive weekly promotion intensity in TRAIN only. The
thresholds are then frozen for VALIDATION and TEST.

The primary demand-surge sample is positive uplift among P1/P2/P3 observations.
P4 is reported as a normal comparator. Calibration uses TRAIN+VALIDATION only;
TEST is held out.

## Empirical quantiles and uncertainty

Report q50, q75, q90, q95, and q99 for the primary overall sample and by product
family, store, state/region, event proxy, promotion intensity, and holiday
status. Quantile level is never interpreted as uplift magnitude.

- `n < 30`: `INSUFFICIENT_SAMPLE`; quantiles are not filled.
- `30 ≤ n < 100`: exploratory group quantile.
- `n ≥ 100`: stable group summary.

For q75, q90, and q95, compute percentile bootstrap 95% confidence intervals
for the overall and event-proxy calibration groups using 1,000 resamples and
fixed seed `20260927`. Bootstrap groups and their seed offsets are sorted
deterministically.

## Held-out coverage

For every sufficient TRAIN+VALIDATION group with corresponding TEST data,
evaluate q75/q90/q95 thresholds on the same TEST group:

`coverage = fraction(TEST uplift ≤ calibration estimate)` and
`coverage_error = coverage - target_quantile`.

The primary coverage population uses positive TEST uplift in event proxies
P1/P2/P3. TEST never changes the threshold.

## Heterogeneity and ML-necessity gate

For product, store/state, promotion intensity, holiday status, and event-proxy
groups, report group q90/q95 minus the global q90/q95 and coverage of the global
threshold in TEST. ML-gate groups require `n_test ≥ 30`.

Stage 3B is required if any condition holds:

1. median group absolute global-q90 coverage error > 0.05; or
2. at least 25% of eligible groups have absolute error > 0.10; or
3. the interquartile range of eligible product/state group q90 values is at
   least 0.20 uplift points.

Otherwise the result is `SIMPLE_QUANTILES_ADEQUATE` and no ML model is trained.

## Conditional ML protocol, only if triggered

No neural network, Transformer, VAE, GAN, or diffusion model is allowed. The
first comparison is a linear `QuantileRegressor` and one sklearn tree-based
quantile model. Models are fit on TRAIN, a small fixed candidate grid is chosen
on VALIDATION by pinball loss, and TEST is used once for final evaluation.

Allowed features are strictly causal lag demand, shifted rolling statistics,
store/state and family identifiers, current calendar fields, observed current
holiday flags, and current promotion fields under the explicit business
assumption that scheduled promotions are known at decision time. Current sales,
future sales/context, TEST-derived thresholds, and future rolling statistics are
forbidden. Feature availability timing is recorded.

Evaluate pinball loss, overall and group coverage, coverage error, and quantile
crossing. Crossing is reported and not silently sorted.

Conditional ML value is supported only if, versus the global empirical
quantile, TEST simultaneously shows:

1. q90 overall absolute coverage error improvement at least 0.02;
2. median group absolute q90 error improvement at least 0.02; and
3. q95 absolute coverage error does not worsen by more than 0.02.

Otherwise simple statistical calibration is preferred.

## Route and regional-event status

Favorita has no warehouse-route capacity, closure, road, freight, or logistics
loss history. Route-loss candidates 25%, 50%, 75%, and 100% remain
`STRESS_TEST_ONLY` unless separately verified external evidence exists. None is
assumed in this run.

Regional-emergency demand may receive `CANDIDATE_DATA_SUPPORTED` evidence from
P2/P3. Its route component remains `STRESS_TEST_ONLY`, producing explicitly
mixed provenance rather than a “fully data-driven” claim.

## Frozen labels and execution boundary

Allowed candidate statuses are `CANDIDATE_DATA_SUPPORTED`,
`CANDIDATE_ML_SUPPORTED`, `STRESS_TEST_ONLY`, `INSUFFICIENT_SAMPLE`, and
`PENDING_EXTERNAL_EVIDENCE`. Optimizer dispatches must remain zero.
