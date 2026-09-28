# Stage 4 — M5 independent-domain demand-calibration validation

## Result and scope

Stage 4 returns `STAGE_4_DEMAND_CALIBRATION_VALIDATION_PASS` and
`CROSS_DOMAIN_CALIBRATION_STATUS = SUPPORTED`. This is evidence that the
transparent calibration mechanism transfers to an independent retail domain:

`causal baseline -> relative uplift -> empirical quantile -> held-out coverage`.

It is not evidence that one numerical demand-risk parameter is universal. M5
produces its own q75, q90, and q95. Favorita observations, fitted baselines, and
quantile values are not pooled into M5 calibration and are used only in the
final descriptive comparison.

M5 validates the demand side only. It is not a second compound-risk inventory
case. Stage 4 does not recalibrate `delta_A`, run robust inventory optimization,
PRB, CCG, scenario generation, GenAI, an LLM, or deep learning. The Stage-3D
route-state mapping remains unchanged.

## Blocked state and data restoration

The initial Stage-4 run stopped as `STAGE_4_BLOCKED_MISSING_M5_DATA`. That
record is retained in `data_availability_audit_blocked_initial.json` and
`m5_schema_audit_blocked_initial.csv`. A later restoration attempt also failed
because `/mnt/data` was not mounted; commit `84c8f923` records that attempt.

The user subsequently supplied five files from a local M5 directory. They were
copied byte-for-byte to ignored local storage under `data/local/m5`. Source and
destination sizes and SHA-256 values are recorded in
`data_restoration_audit.json`. Raw M5 files are not committed.

The repeated availability audit returned `M5_DATA_AVAILABILITY_PASS` before any
calibration began. It inspected real headers and data rather than relying on a
remembered M5 schema. The two sales tables contain 30,490 series for 3,049
items, 10 stores, 3 states, 7 departments, and 3 categories. Validation covers
2011-01-29 through 2016-04-24; evaluation extends through 2016-05-22. Calendar
coverage is 2011-01-29 through 2016-06-19. The price table has 6,841,121 rows.

## Chronological protocol

Only complete seven-day M5 retail weeks are used. The split was frozen before
TEST demand was aggregated:

| Split | Dates | Weeks | Department-store rows |
|---|---|---:|---:|
| TRAIN | 2011-01-29 to 2014-12-26 | 204 | 14,280 |
| VALIDATION | 2015-01-03 to 2015-12-25 | 51 | 3,570 |
| TEST | 2016-01-02 to 2016-05-20 | 20 | 1,400 |

TRAIN and VALIDATION determine the unit, baseline, proxy rules, eligibility
threshold, and quantiles. TEST demand is first aggregated only after those
choices, the bootstrap design, and the 0.05 coverage tolerance are frozen.

## Calibration unit

The candidate-unit audit used TRAIN and VALIDATION only:

| Candidate | Observations | Zero-demand rate | Positive-demand rate |
|---|---:|---:|---:|
| store x item x day | 54,424,650 | 0.688861 | 0.311139 |
| store x department x week | 17,850 | 0.000000 | 1.000000 |
| store x category x week | 7,650 | 0.000000 | 1.000000 |

`store x department x week` is selected. Item-day is substantially sparse and
computationally large. Category-week is stable but discards more product detail
than necessary. The selected unit therefore retains interpretable department
heterogeneity without the item-day sparsity burden.

## Causal baselines and uplift

Every candidate uses past demand only:

- `B1_LAG_1`: previous complete week;
- `B2_MEAN_12`: `shift(1)` followed by a 12-week mean;
- `B3_MEDIAN_12`: `shift(1)` followed by a 12-week median, conceptually
  comparable to Favorita `B3_MEDIAN_12`;
- `B4_LAG_52`: the same M5 retail week in the preceding year.

VALIDATION MAE selects `B1_LAG_1` (MAE 344.6815; RMSE 654.5651; MedianAE
143.0). TEST is not used in selection. The rolling-value audit reports zero
mismatches and no centered rolling window.

The uplift is

`u_t = (actual_demand_t - baseline_demand_t) / baseline_demand_t`.

The minimum eligible baseline was frozen at 1 unit. No epsilon is added. In
TRAIN+VALIDATION, 17,780 of 17,850 rows are eligible; 8,961 have positive
uplift. Formal risk calibration uses positive uplift, matching the Stage-3A
sample definition. All eligible uplift is reported descriptively.

## Event and context proxies

M5 proxies are derived from M5 fields and are not relabeled as Favorita D1-D4:

- `M5_CALENDAR_EVENT`: either calendar event-name field is present during the
  week;
- `M5_SNAP_CONTEXT`: the store state's SNAP flag is active on at least four of
  seven days;
- `M5_PROMOTION_LIKE`: at least one listed item is priced below its store-item
  TRAIN maximum;
- `M5_EVENT_PROMOTION_OVERLAP`: calendar event and promotion-like price context
  overlap;
- `M5_GENERIC_EVENT_PROXY`: the union of calendar, SNAP, and promotion-like
  context.

SNAP, a named calendar event, and a price decrease are distinct economic
mechanisms. Their separate provenance is retained. The chosen promotion-like
rule is reproducible and TRAIN-anchored, but it activates broadly at the
department-week level. Consequently the generic proxy is broad; this is a
known limitation, not evidence that these mechanisms are equivalent.

## Independent empirical quantiles

For 8,961 positive TRAIN+VALIDATION observations under the generic proxy:

| Severity interface | Quantile | M5 value | 95% bootstrap CI |
|---|---:|---:|---:|
| MODERATE | q75 | 0.162791 | [0.158416, 0.167608] |
| HIGH | q90 | 0.288913 | [0.280539, 0.297297] |
| EXTREME | q95 | 0.394231 | [0.379829, 0.409341] |

Intervals use 10,000 resamples and fixed seeds. TEST never enters quantile or
bootstrap estimation.

## Held-out coverage

The generic-proxy TEST sample contains 742 positive eligible observations:

| Target | Observed | Direction | Absolute error | Frozen tolerance |
|---:|---:|---|---:|---:|
| 0.75 | 0.769542 | OVER_COVERAGE | 0.019542 | 0.05 |
| 0.90 | 0.913747 | OVER_COVERAGE | 0.013747 | 0.05 |
| 0.95 | 0.962264 | OVER_COVERAGE | 0.012264 | 0.05 |

Under-coverage means observed coverage is below the target; over-coverage means
it is above the target. Both are measured with absolute coverage error for the
gate. All three primary errors pass the preregistered tolerance.

## Heterogeneity and ML necessity

Store, state, category, and department diagnostics do not jointly satisfy the
frozen material-heterogeneity conditions. The simple global quantiles also do
not show material systematic miscalibration. The necessity gate therefore
returns `SIMPLE_CALIBRATION_RETAINED`; no ML model is trained and
`ml_training_dispatches = 0`.

If an ML diagnostic were run in a future authorized stage, improvement would
be `simple calibration error - ML calibration error`. Negative improvement
means the ML error is larger. It does not imply under-coverage; coverage
direction and absolute error remain separate quantities.

## Favorita versus M5

| Quantile | Favorita | M5 |
|---:|---:|---:|
| q75 | 0.295455 | 0.162791 |
| q90 | 0.733333 | 0.288913 |
| q95 | 1.352941 | 0.394231 |

Equality is neither expected nor a pass condition. The shared interface is
MODERATE -> q75, HIGH -> q90, EXTREME -> q95. The magnitudes remain
domain-specific empirical parameters.

## Gates and claims

All six gates pass: data integrity, no leakage, baseline validity, quantile
identifiability, held-out calibration, and completion of the independent M5
method chain.

Stage 4 supports the statement: *the empirical demand-risk calibration
mechanism shows independent-domain support on M5.* It does not support
`UNIVERSAL_PARAMETER_VALIDATED`, `UNIVERSAL_Q90_VALIDATED`, causal claims about
M5 context fields, route-risk validation, or full compound-risk inventory
validation.

## Integrity

Optimizer dispatches, GenAI dispatches, and ML training dispatches are all
zero. Stage 1, Stage 2, Stage 3A, Stage 3C, and Stage 3D artifact hashes are
unchanged. Paper-2 remains fixed at
`51aebd06edf8f5d6d124d0f3eebdbb901e63274f` with a clean working tree.
