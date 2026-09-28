# Stage 4A — M5 event-proxy overlap audit and promotion refinement

## Result and boundary

Stage 4A returns `STAGE_4A_PROXY_REFINEMENT_PASS`.

- `OLD_PROMOTION_PROXY_STATUS = OVERBROAD_LEGACY_PROXY`
- `NEW_PRICE_PROMOTION_PROXY_STATUS = DISTINGUISHABLE_DERIVED_PROXY`

Stage 4A audits proxy semantics only. It does not alter the completed Stage-4
result `STAGE_4_DEMAND_CALIBRATION_VALIDATION_PASS`, the cross-domain status
`SUPPORTED`, the selected unit, baseline, original quantiles, or original
held-out coverage. No route calibration, robust optimization, PRB, CCG,
scenario generation, GenAI, LLM, ML, or deep learning is run.

## Why the legacy price proxy is overbroad

The legacy Stage-4 rule labels an item-week when its price is below the
store-item maximum observed anywhere in TRAIN. This is a derived proxy, not an
official M5 promotion field. A single historical maximum can remain above the
usual price for a long period, and the department aggregation labels the whole
department-week when only one item satisfies the rule. The rule therefore
captures broad price variation rather than a reasonably localized promotion
context.

Across 6,018,884 priced TRAIN+VALIDATION item-weeks, 2,357,505 (39.1685%) are
labeled. The store-item labeled-fraction quartiles are 0.0000, 0.2096, and
0.6759; the 90th percentile is 0.8980. There are 4,555 store-items with more
than 80% of priced weeks labeled and 18,124 for which every non-maximum week is
labeled.

At the positive-uplift department-week level the saturation is complete:

| Legacy proxy | Count |
|---|---:|
| Calendar event | 4,080 |
| SNAP context | 4,032 |
| Promotion-like | 8,961 |
| Generic event proxy | 8,961 |

`P(Promotion|Calendar)`, `P(Promotion|SNAP)`,
`P(Promotion|Generic)`, `P(Generic|Promotion)` are all 1.0. The legacy generic
set is therefore identical to the legacy promotion-like set. Calendar and
promotion intersect in 4,080 observations, calendar and SNAP in 2,210,
promotion and SNAP in 4,032, and all three in 2,210. The Calendar–Promotion,
Calendar–SNAP, and Promotion–SNAP Jaccard similarities are respectively
0.4553, 0.3744, and 0.4499. These values do not mean calendar and SNAP are
promotions; they show that the overbroad price proxy contains both sets.

## Refined taxonomy

Stage 4A distinguishes three mechanisms:

- `M5_CALENDAR_EVENT_V2`: an observed calendar-based external demand context;
- `M5_PRICE_PROMOTION_EVENT_V2`: a derived price-based promotion-like context;
- `M5_SNAP_CONTEXT_V2`: a state-level SNAP context, not a promotion or holiday.

Their union is `M5_GENERIC_DEMAND_STIMULATION_V2`. It is not called a generic
promotion. Calendar events, price promotions, and SNAP are distinct contextual
demand-stimulation mechanisms even when their observed weeks overlap.

## Preregistered V2 price rule

For store-item `(s,i)` and retail week `w`, the reference is the median of the
preceding 12 *available* price weeks:

`P_ref(s,i,w) = median(P(s,i,w-12), ..., P(s,i,w-1))`.

Implementation applies `shift(1)` before the 12-observation rolling median, so
the current price never enters its own reference. An item is promotion-like
only when `P_ref > 0` and

`(P_ref - P_current) / P_ref >= 0.10`.

At store-department-week level, the event activates only when at least 10 valid
priced items exist and at least 10% of those items satisfy the item rule. The
10% item discount and 10% department share are the preregistered primary rule;
TEST coverage did not select them.

TRAIN+VALIDATION sensitivity covers item discounts 5%, 10%, and 15% crossed
with department shares 5%, 10%, and 20%. The primary 10%/10% rule activates
101 of 17,850 department-weeks (0.5658%); 17,010 department-weeks have usable
price history. Sensitivity is descriptive and does not replace the primary
rule.

## V2 overlap and distinguishability

Among positive eligible TRAIN+VALIDATION uplift observations:

| V2 proxy | Count | Atomic-exclusive count |
|---|---:|---:|
| Calendar | 4,080 | 1,862 |
| SNAP | 4,032 | 1,819 |
| Price promotion | 50 | 16 |
| Calendar-price overlap | 31 | — |
| Generic demand stimulation | 5,918 | — |

Calendar–Price, Calendar–SNAP, and Price–SNAP intersections are 31, 2,210, and
26; their Jaccard similarities are 0.00756, 0.37445, and 0.00641. The
three-way intersection is 23.

`P(Price|Calendar)=0.00760`, `P(Calendar|Price)=0.62`,
`P(Price|SNAP)=0.00645`, and `P(SNAP|Price)=0.52`.
`P(Price|Generic)=0.00845`. Generic V2 is not equal to Price V2. The maximum
price-coverage saturation measure falls from 1.0 to 0.00845, a reduction of
0.99155. The frozen distinguishability gate therefore passes.

## Refined quantiles and diagnostic TEST check

V2 estimates are separate `STAGE4A_PROXY_REFINED_CALIBRATION` results and do
not overwrite Stage-4 values.

| V2 proxy | n | q75 | q90 | q95 |
|---|---:|---:|---:|---:|
| Calendar | 4,080 | 0.171387 | 0.298031 | 0.402819 |
| SNAP | 4,032 | 0.188471 | 0.327192 | 0.446700 |
| Generic demand stimulation | 5,918 | 0.174828 | 0.303202 | 0.416228 |

Price V2 has 50 positive observations and calendar-price overlap has 31, below
the frozen 100-observation reporting minimum. Their quantiles are explicitly
marked `INSUFFICIENT_SAMPLE_FOR_QUANTILE`; no values are fabricated.

After all V2 definitions and TRAIN+VALIDATION estimates were frozen, TEST was
used only for a `POST_STAGE4_DIAGNOSTIC_HELDOUT_CHECK`. Generic V2 has 502 TEST
observations, with coverage 0.788845, 0.914343, and 0.956175 for q75, q90, and
q95. These are diagnostic results, not a new untouched confirmatory gate.

The original broad Stage-4 generic q75/q90/q95 values
`0.162791/0.288913/0.394231` and coverages
`0.769542/0.913747/0.962264` remain valid as the preserved Stage-4 broad
cross-domain calibration evidence. Stage 4A refines event-subtype semantics;
it does not invalidate the empirical-quantile method.

## Integrity

Stage 1, Stage 2, Stage 3A, Stage 3C, Stage 3D, and every original Stage-4
artifact are hash-identical before and after the audit. Paper-2 remains at
`51aebd06edf8f5d6d124d0f3eebdbb901e63274f` with a clean working tree.
Optimizer, GenAI, and ML training dispatch counts are all zero.
