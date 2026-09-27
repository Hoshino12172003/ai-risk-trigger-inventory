# Stage 3C event-to-severity-to-parameter mapping

## Scope

Stage 3C is a solver-free mapping layer between structured event metadata and
the calibrated impact parameters accepted by the Stage-2 model. It does not
re-estimate a quantile, train a model, solve an inventory problem, or alter the
Stage-3A evidence. Its auditable chain is:

`event metadata → severity → calibration stratum → frozen quantile → delta_D / delta_A`.

The current result is `STAGE_3C_MAPPING_PASS_WITH_ROUTE_GAP`. Demand mappings
are complete for the defined retail proxy strata, while intermediate route-loss
magnitudes remain unresolved pending external evidence. This is not
`FULL_CALIBRATION_COMPLETE`.

## Severity semantics

The severity-to-quantile rule is fixed:

| Severity | Stage-3A quantile | Interpretation |
|---|---:|---|
| MODERATE | q75 | High but non-extreme historical positive retail uplift |
| HIGH | q90 | Approximately the upper 10% region of the historical uplift distribution |
| EXTREME | q95 | Approximately the upper 5% region of the historical uplift distribution |

A quantile level is not an event probability. `HIGH → q90` means “use the
90th-percentile magnitude of the selected historical uplift distribution.” It
does not mean that the event has a 90% probability of occurring. `UNKNOWN`
severity is accepted in metadata but produces no parameter and returns
`CALIBRATION_UNRESOLVED` or `UNRESOLVED`.

q99 remains a descriptive extreme-tail statistic. It is not mapped because the
observed tail is very heavy and small positive baselines can inflate relative
uplift. EXTREME is fixed at q95.

## Event metadata schema

Every event records:

- `event_id`;
- `risk_type`;
- `event_subtype`;
- `region_scope`;
- `severity` (`MODERATE`, `HIGH`, `EXTREME`, or `UNKNOWN`);
- `start_time`;
- `expected_duration`;
- `evidence_source`;
- zero or more affected warehouse-region arcs.

An affected arc records `warehouse_id`, `region_id`, and
`impact_scope = WAREHOUSE_REGION_ARC`. A route event may affect one or multiple
arcs. Stage 3C does not construct or select new routes.

`expected_duration` is metadata only. The current optimization semantics fold
an event into one planning-period effective service fraction. Duration may
later support evidence calibration or a multi-period extension, but it is not a
separate optimization dimension here.

## Demand calibration strata and precedence

No product-family-specific quantile enters the mapping. This avoids promoting
the unstable product tails observed in Stage 3A and follows the Stage-3 result
that simple calibration is preferred. The four permitted strata are:

| Stratum | Frozen Stage-3A population |
|---|---|
| D1_PROMOTION | P1 promotion-only proxy |
| D2_HOLIDAY_EVENT | P2 holiday/event-only proxy |
| D3_PROMOTION_HOLIDAY_OVERLAP | P3 promotion + holiday/event proxy |
| D4_GENERIC_EVENT_PROXY | pooled P1/P2/P3 population |

For `FLASH_DEMAND_SURGE`, subtype matching uses the fixed precedence:

1. promotion plus holiday/event overlap → D3;
2. promotion, flash promotion, campaign, or sale → D1;
3. holiday, public event, scheduled large event, or festival → D2;
4. otherwise → D4.

The stratum is never selected by comparing resulting parameter values.
Promotion and holiday/event records are observed retail proxies, not the formal
risk event itself and not livestream labels.

## Frozen demand mappings

All numbers below are read programmatically from the committed Stage-3A
`empirical_quantiles.csv`, `quantile_bootstrap_ci.csv`, and
`heldout_coverage.csv`. The mapping code does not estimate or hard-code these
values.

| Stratum | MODERATE q75 | HIGH q90 | EXTREME q95 |
|---|---:|---:|---:|
| D1_PROMOTION | 0.212121 | 0.461538 | 0.969228 |
| D2_HOLIDAY_EVENT | 0.579072 | 1.326341 | 2.666667 |
| D3_PROMOTION_HOLIDAY_OVERLAP | 0.250000 | 0.514705 | 0.912953 |
| D4_GENERIC_EVENT_PROXY | 0.295455 | 0.733333 | 1.352941 |

The artifact also carries sample size, bootstrap interval, held-out coverage,
source, provenance, and status for every cell.

## Regional-emergency mapping

A regional emergency is not equated with a Favorita holiday. Favorita has no
natural-disaster, public-health-emergency, or emergency-logistics demand label.
Its demand component therefore always uses D4 with provenance
`INDIRECT_RETAIL_PROXY` and status `INDIRECT_PROXY`:

| Severity | Demand quantile | delta_D | Route state | delta_A |
|---|---:|---:|---|---:|
| MODERATE | q75 | 0.295455 | MODERATE_DISRUPTION | unresolved |
| HIGH | q90 | 0.733333 | SEVERE_DISRUPTION | unresolved |
| EXTREME | q95 | 1.352941 | SEVERE_DISRUPTION | unresolved |

EXTREME metadata does not by itself prove a full route closure. CLOSURE is used
only when the event subtype supplies confirmed full-closure evidence. Thus a
regional event may legitimately have a data-supported indirect demand
candidate and `PENDING_EXTERNAL_EVIDENCE` on the route side.

## Route-risk definition and states

`TRANSPORT_NETWORK_DISRUPTION` means that road, logistics-channel, transport-
resource, or regional traffic conditions reduce effective service ability on a
warehouse `i` → region `r` arc. It does not mean that an entire warehouse fails.

| Route state | delta_A | Status |
|---|---:|---|
| NORMAL | 0 | DEFINITION_SUPPORTED |
| MODERATE_DISRUPTION | unresolved | PENDING_EXTERNAL_EVIDENCE |
| SEVERE_DISRUPTION | unresolved | PENDING_EXTERNAL_EVIDENCE |
| CLOSURE | 1 | DEFINITION_SUPPORTED |

NORMAL and CLOSURE are definition-based endpoint states. The intermediate
states intentionally have blank values. Stage-1 values 0.25, 0.50, and 0.75
are retained in the route artifact only as `STRESS_TEST_REFERENCE`; they do not
populate a formal severity state and are not upgraded to calibration evidence.

## Examples

- A HIGH flash promotion maps to D1 → q90 → `delta_D = 0.461538`.
- An EXTREME scheduled public event maps to D2 → q95 →
  `delta_D = 2.666667`.
- A HIGH regional emergency maps demand through indirect D4 → q90 →
  `delta_D = 0.733333`; each listed affected arc maps to
  SEVERE_DISRUPTION with unresolved `delta_A`.
- A confirmed full road closure maps its listed warehouse-region arc to
  CLOSURE → `delta_A = 1.0`.

The complete machine-readable examples retain event ID, region scope, time,
duration, evidence source, and affected arcs.

## Future external-evidence interface

An external route-calibration module may later supply evidence-backed partial
losses using verified closure, weather, freight-service, mobility, and
warehouse-to-region data. It must preserve the affected-arc keys, units,
baseline service definition, event time, duration, geography, uncertainty, and
source provenance. Only then may MODERATE_DISRUPTION or SEVERE_DISRUPTION move
from `PENDING_EXTERNAL_EVIDENCE` to an evidence-supported numeric `delta_A`.

Similarly, verified emergency-demand data could replace the generic indirect
retail proxy for regional events. Neither upgrade is performed in Stage 3C.
