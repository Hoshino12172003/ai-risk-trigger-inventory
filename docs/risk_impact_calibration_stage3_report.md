# Stage 3 risk-impact calibration report

## Scope and classifications

This is an exploratory, auditable calibration study for the Stage-2 impact
parameters `delta_D` and `delta_A`; it is not a final parameter selection or a
formal compound-risk experiment. No robust optimization model was solved.

- Stage 3A: `STAGE_3A_STATISTICAL_CALIBRATION_PASS`.
- ML necessity: `ML_NEEDED_FOR_CONDITIONAL_CALIBRATION`.
- Stage 3B value result: `STAGE_3_SIMPLE_CALIBRATION_PREFERRED`.
- Route side: `ROUTE_CALIBRATION_STATUS = STRESS_TEST_ONLY`.

The demand-side pass does not mean that the compound-risk calibration is
complete. In particular, Favorita contains no route-service loss observations.

## Data, provenance, and split

The manifest-verified cleaned Favorita daily panel has SHA-256
`08f9b8b221c5014abf8db3f936064af28777f26d23e707742230f005af0501ad`,
3,000,888 rows, and dates from 2013-01-01 through 2017-08-15. The analysis unit
is store number × product family × complete Monday-Sunday week. Incomplete and
split-boundary weeks are excluded.

| Split | Complete weekly rows | Actual included interval | Store-family pairs |
|---|---:|---|---:|
| TRAIN | 270,864 | 2013-01-07 to 2015-12-20 | 1,782 |
| VALIDATION | 89,100 | 2016-01-04 to 2016-12-18 | 1,782 |
| TEST | 57,024 | 2017-01-02 to 2017-08-13 | 1,782 |

Observed evidence consists of Favorita sales, promotions, dates, store/state
and product identifiers, and source holiday/event context. Weekly demand,
weekly holiday indicators, lagged demand, rolling baselines, proxy strata,
uplift, and quantiles are derived. Warehouses, warehouse capacities, route
networks, physical route capacity, and route-loss magnitudes are calibrated or
constructed quantities not observed in Favorita.

No missing values occur in the eight cleaned input columns used here. The
strict-causality audit found zero future-source rows, zero rolling-value
mismatches, and zero seasonal-lag date mismatches. No centered window was used.

## Baseline selection

All rolling candidates use `shift(1)`. B4 uses only an exact 52-week date key.
The common normal VALIDATION subset contains 14,747 observations and excludes
promotion and holiday/event weeks. TEST contributes zero rows to selection.

| Candidate | Validation MAE | Median absolute percentage error | Median relative bias | Selected |
|---|---:|---:|---:|---|
| B1 previous-4 median | 25.3643 | 0.217391 | -0.009524 | no |
| B2 previous-8 median | 24.5750 | 0.217391 | -0.005917 | no |
| B3 previous-12 median | 24.1527 | 0.218182 | 0.000000 | yes |
| B4 exact 52-week lag | 37.0766 | 0.350000 | 0.000000 | no |

The predeclared minimum-MAE rule therefore selects `B3_MEDIAN_12`. Initial
TRAIN history produces 21,384 missing B3 values; they remain missing and are
not filled from the future.

## Demand uplift evidence

Uplift is `(actual - baseline) / baseline` only when baseline is greater than
`1e-9`. The primary calibration population is positive uplift in retail proxy
P1, P2, or P3 using TRAIN+VALIDATION. It has 75,946 observations; the matching
held-out TEST population has 19,989. These proxies are not livestream labels.

### Overall empirical candidate range

| Statistic | Uplift | 95% bootstrap interval where computed | TEST coverage | Coverage error |
|---|---:|---:|---:|---:|
| q50 | 0.129108 | — | — | — |
| q75 | 0.295455 | [0.292035, 0.299256] | 0.763820 | +0.013820 |
| q90 | 0.733333 | [0.714286, 0.748986] | 0.922407 | +0.022407 |
| q95 | 1.352941 | [1.317757, 1.395473] | 0.965331 | +0.015331 |
| q99 | 7.975472 | — | — | — |

The q75/q90/q95 values are `CANDIDATE_DATA_SUPPORTED` demand-uplift inputs,
not final `delta_D` values. The very large upper tail is sensitive to small
positive baselines; this makes the high quantiles exploratory stress evidence,
not a claim about a universal real-world shock.

### Retail event-proxy evidence

| Proxy | Calibration n | q50 | q75 | q90 | q95 | q99 | TEST q90 coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| P1 promotion only | 37,086 | 0.097682 | 0.212121 | 0.461538 | 0.969228 | 9.525914 | 0.900305 |
| P2 holiday/event only | 20,156 | 0.250000 | 0.579072 | 1.326341 | 2.666667 | 8.565237 | 0.926856 |
| P3 promotion + holiday/event | 18,704 | 0.117911 | 0.250000 | 0.514705 | 0.912953 | 4.358001 | 0.863927 |

The bootstrap q90 intervals are [0.448806, 0.474149] for P1,
[1.275868, 1.375000] for P2, and [0.500000, 0.529505] for P3. P2 and P3 can
support exploratory demand-side candidates for a regional-emergency event, but
they do not identify its route component.

## Heterogeneity diagnostic

The global q90 does not describe all contexts uniformly. Among eligible
groups, product-family q90 ranges from 0.201521 for MEATS to 98.181382 for
PRODUCE, while state q90 ranges from 0.528821 for Azuay to 1.369231 for Santa
Elena. Store q90 ranges from 0.445508 (store 3) to 1.369231 (store 25).
Promotion-stratum q90 ranges from 0.321185 (HIGH) to 1.326341 (NONE), where the
NONE stratum in this event-only sample corresponds to holiday/event proxy P2.

Extreme product values are driven in part by ratios with small baselines and
must not be read as stable operational multipliers without additional scale
and context controls. The eligible product/state q90 interquartile range is
0.581210 uplift points. This exceeds the frozen 0.20 threshold and alone
triggers Stage 3B. The other necessity conditions do not trigger: median group
absolute q90 coverage error is 0.040811 (threshold strictly above 0.05), and
9.09% of eligible groups exceed 0.10 error (threshold 25%).

## Conditional ML diagnostic

Stage 3B was run only after the gate triggered. Models used strictly past-only
lags and rolling values plus known identifiers and calendar context. Current
promotion is included under the explicit assumption that scheduled promotion
is known at decision time. TRAIN fits the models, VALIDATION selects from the
small frozen candidate set, and TEST evaluates once.

The validation-average pinball loss selects linear quantile regression
(0.211311) over gradient-boosting quantile regression (0.605142). Quantile
crossings are reported without post-processing: 17 of 19,989 TEST rows for the
selected linear family, versus 966 for the tree family.

| TEST metric | Simple global quantile | Selected linear conditional model |
|---|---:|---:|
| q90 coverage | 0.922407 | 0.946170 |
| q90 absolute coverage error | 0.022407 | 0.046170 |
| Median group q90 absolute error | 0.053838 | 0.056457 |
| q95 coverage | 0.965331 | 0.981290 |
| q95 absolute coverage error | 0.015331 | 0.031290 |

The q90 overall-error improvement is -0.023763 and the median group-error
improvement is -0.002619; both fail the required +0.02 improvement. q95
worsening is 0.015959 and stays within the maximum 0.02 degradation. Because
all three conditions must hold, conditional ML value is not supported and no
ML-derived candidate is promoted. The correct classification is
`STAGE_3_SIMPLE_CALIBRATION_PREFERRED`.

## Calibration status

The core candidate table preserves provenance at component level:

- Overall flash-demand candidates are q75 = 0.295455, q90 = 0.733333, and
  q95 = 1.352941, all `CANDIDATE_DATA_SUPPORTED` from retail proxies.
- Regional-emergency demand-side candidates include P2 q90 = 1.326341 and P3
  q90 = 0.514705. They are retail-context candidates, not emergency-causal
  estimates.
- Stage-1 demand magnitudes 0.20, 0.40, and 0.60 remain
  `STRESS_TEST_ONLY`; Stage 3 does not retroactively relabel them.
- Every route-loss value 0.25, 0.50, 0.75, and 1.00 remains
  `STRESS_TEST_ONLY` for both regional and network events.
- External route evidence remains `PENDING_EXTERNAL_EVIDENCE`.

Thus a regional-emergency event currently has mixed provenance: exploratory
data-supported demand evidence and a stress-test-only route component. There is
no verified external logistics source in this run, and no route loss is
inferred from Favorita.

## Execution boundary and limitations

The run dispatched zero optimizers, did not call an LLM, did not use M5, and
did not train a deep model. It does not establish causal effects of promotions
or holidays, livestream-specific impacts, probabilities of event occurrence,
or physical route capacities. Stage-1 and Stage-2 artifacts remained byte-for-
byte unchanged, and the frozen Paper-2 checkout remained at
`51aebd06edf8f5d6d124d0f3eebdbb901e63274f` with a clean working tree.

Formal route calibration requires verified closure, weather, freight/service,
mobility, and warehouse-to-region evidence as listed in
`external_calibration_requirements.md`. Until that evidence exists, the full
compound-risk parameterization is not data-calibrated.
