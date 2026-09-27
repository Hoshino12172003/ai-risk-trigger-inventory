# Stage 3D literature-anchored route-risk calibration

## Result and boundary

Stage 3D freezes a simple route-state lookup for the current Stage-2/Stage-3C
model. Its classification is `STAGE_3D_ROUTE_CALIBRATION_PASS`, and the current
event-parameter interface is `COMPLETE_FOR_CURRENT_MODEL`.

This does not mean `FULL_REAL_WORLD_CALIBRATION_COMPLETE`. The route values are
literature-anchored cross-region modeling parameters, not Ecuador-specific
empirical estimates. No GenAI, machine learning, traffic simulation, duration
correction, alternative-route correction, geographic transfer model, or robust
optimization is introduced.

## Formal route-loss definition

For event `e` and warehouse-region arc `(i,r)`, `delta_A[e,i,r]` is the
planning-period effective route-service loss fraction relative to normal
service. Availability remains exactly the Stage-2 quantity:

`a_ir(xi) = max(0, 1 - sum_e delta_A[e,i,r] xi_e)`.

`delta_A` is reduced-form. It is not an exact observed physical road capacity,
speed reduction, travel-time increase, or lane-count reduction. Those metrics
can provide external anchors, but they do not have the same mathematical
meaning as the model parameter.

## Frozen route states

No additional state is introduced.

| Route state | Interval | Main | Evidence status |
|---|---:|---:|---|
| NORMAL | [0.00, 0.00] | 0.00 | DEFINITION_SUPPORTED |
| MODERATE_DISRUPTION | [0.10, 0.40] | 0.25 | LITERATURE_ANCHORED |
| SEVERE_DISRUPTION | [0.40, 0.80] | 0.60 | LITERATURE_ANCHORED |
| CLOSURE | [1.00, 1.00] | 1.00 | DEFINITION_SUPPORTED |

NORMAL and CLOSURE are definition-supported endpoints. A main value of 0.25
or 0.60 is simply an interior representative of its literature-anchored range;
it is not a maximum-likelihood estimate, a true physical capacity loss, or an
Ecuador-specific observed parameter.

Closure means the affected warehouse-region arc is confirmed unavailable for
the planning period. It does not mean the entire warehouse fails.

## Evidence interpretation

The evidence hierarchy and individual records are documented in
`route_risk_external_evidence.md` and `evidence_table.csv`.

- Ecuador records establish local operational relevance and the realism of
  partial, severe, and closure states.
- Andean/Latin American evidence supports regional transfer and shows how
  closure and low redundancy affect service.
- International quantitative evidence anchors broad lower- and higher-loss
  regimes using route-failure, vehicle-flow, and network-function metrics.

Because these metrics differ from one another and from `delta_A`, the ranges
are triangulated across evidence. No single paper controls the interval.

## Frozen sensitivity grid

| Scenario | Moderate | Severe | Closure |
|---|---:|---:|---:|
| LOW | 0.10 | 0.40 | 1.00 |
| MAIN | 0.25 | 0.60 | 1.00 |
| HIGH | 0.40 | 0.80 | 1.00 |

The grid is reserved for future sensitivity experiments. Stage 3D runs no
optimizer and does not select a better-looking row after observing decision
results.

## Severity compatibility and closure precedence

The Stage-3C demand interface remains unchanged:

- MODERATE → q75;
- HIGH → q90;
- EXTREME → q95.

The route interface is:

- MODERATE → MODERATE_DISRUPTION → main 0.25;
- HIGH → SEVERE_DISRUPTION → main 0.60;
- EXTREME → SEVERE_DISRUPTION → main 0.60.

EXTREME does not automatically mean CLOSURE. CLOSURE overrides the severity
mapping only when subtype metadata explicitly indicates full closure,
confirmed closure, a road completely closed, or an unavailable route. This
preserves the distinction between an intense event and verified loss of the
entire arc service.

## Regional-emergency mapping

The demand component remains the Stage-3C generic indirect retail proxy. The
route component now uses the Stage-3D main lookup:

| Severity | Demand mapping | delta_D | Route mapping | Main delta_A |
|---|---|---:|---|---:|
| MODERATE | D4 → q75 | 0.295455 | MODERATE_DISRUPTION | 0.25 |
| HIGH | D4 → q90 | 0.733333 | SEVERE_DISRUPTION | 0.60 |
| EXTREME | D4 → q95 | 1.352941 | SEVERE_DISRUPTION | 0.60 |

Demand provenance remains `INDIRECT_RETAIL_PROXY`; route provenance is
`LITERATURE_ANCHORED_CROSS_REGION`. Explicit closure evidence changes only the
route component to CLOSURE and 1.00. It does not replace the demand severity.

## Transport-network-disruption mapping

Transport-network disruption has `delta_D = 0`. Its route side is:

| Severity | Route state | Interval | Main delta_A |
|---|---|---:|---:|
| MODERATE | MODERATE_DISRUPTION | [0.10, 0.40] | 0.25 |
| HIGH | SEVERE_DISRUPTION | [0.40, 0.80] | 0.60 |
| EXTREME | SEVERE_DISRUPTION | [0.40, 0.80] | 0.60 |

Explicit closure evidence overrides the route side to CLOSURE and 1.00.

## Future metadata interface

A future GenAI component may identify structured risk type, severity, affected
region, affected warehouse-region arcs, and explicit closure evidence. It must
not generate `delta_A`. The deterministic Stage-3D lookup remains the sole
mapping from those fields to lower, main, and upper route-loss parameters.

## Limitations

Cross-region transfer cannot remove differences in road design, hazard
intensity, response practices, network redundancy, planning-period length, or
service definition. The model intentionally does not correct for duration,
alternative routes, geography, or traffic flows in this stage. The intervals
therefore support transparent sensitivity analysis, not claims of exact local
truth.
