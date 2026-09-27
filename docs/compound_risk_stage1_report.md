# Compound-risk Stage 1 report

## Status

This is an exploratory feasibility prototype, not a formal paper experiment or
final paper evidence. MAIN magnitudes, targets, Gamma, base state, and gate
thresholds were preregistered and were not tuned after results. No GenAI, CCG,
M5, Paper-2 source modification, PRB compound solve, or merge was used.

Overall classification: **COMPOUND_RISK_FEASIBILITY_GO** (5/5 gates).

## Base state

- state ID: `pichincha-pichincha-context-2-20151012`
- date/state: `2015-10-12` / `Pichincha`
- relative demand shift: -0.040351
- regions: demand_region_store_11, demand_region_store_49, demand_region_store_10, demand_region_store_4, demand_region_store_7
- products: CLEANING, DAIRY, BREAD/BAKERY
- calibration: `PRIMARY_PILOT_CALIBRATION / UNIT_COST_BASED`
- selection: existing normal week, then abs(relative_demand_shift), state lexical, date ascending, stable ID

The state was selected ex ante from existing normal weeks, before any compound
result was computed.

## Construction

- E1 `FLASH_DEMAND_SURGE`: highest baseline-demand region-product pair
- E2 `FLASH_DEMAND_SURGE`: highest baseline-demand cell with region and product different from E1
- E3 `REGIONAL_EMERGENCY_DISRUPTION`: first two lexically sorted stable region IDs and their assigned routes
- E4 `REGIONAL_EMERGENCY_DISRUPTION`: last two lexically sorted stable region IDs and their assigned routes
- E5 `TRANSPORT_NETWORK_DISRUPTION`: assigned route of the middle lexically sorted region
- E6 `TRANSPORT_NETWORK_DISRUPTION`: first stable different-warehouse/different-region route avoiding E3/E4 assigned routes where possible

The full set contains 22 programmatically enumerated scenarios. Regime counts
are 1 for nominal, 4 for each two-event-type-only regime, 11 for each four-event
combined regime, and 22 for full compound. Overlapping demand shocks add on the
baseline; route losses add and cap at 100%.

Effective route capacity is an exploratory service fraction, not observed
physical units/day capacity. The constraint is
`shipment_irj_s <= availability_ir(s) * demand_rj(s)`; nominal availability is
one and therefore does not restrict an arc below full region-product demand.

## Exact solver audit

- optimizer dispatches: 61
- exact/optimal certifications: 61
- failures: 0
- nominal local/frozen cost-only identity: PASS
- Paper-2 ending SHA/clean: `51aebd06edf8f5d6d124d0f3eebdbb901e63274f` / `True`

Demand-only percentage events are not isomorphic to Paper-2's original
region-product deviation cardinality set, so this report does not claim an
external demand-only solution identity. Internal fixed-x primal/cost identities
and nominal semantics passed.

## Main objectives

| regime | KEEP total | REOPTIMIZE total |
|---|---:|---:|
| R0_NOMINAL | 232620.542950 | 218458.779710 |
| R1_DEMAND_ONLY | 401541.219200 | 246588.754710 |
| R2_REGIONAL_ONLY | 1572998.372250 | 671083.440173 |
| R3_NETWORK_ONLY | 246236.102950 | 223143.486585 |
| R7_FULL_COMPOUND | 2066437.585375 | 1282043.410181 |

FULL_COMPOUND KEEP vs REOPTIMIZE decision value is
784394.175194
(37.959%).
The FULL_COMPOUND versus DEMAND_ONLY reoptimized normalized inventory L1
difference is 33.872%.
FULL_COMPOUND KEEP worst events are `E4|E5` and
REOPTIMIZE worst events are `E1|E4;E2|E4;E4|E5`. Demand-only
REOPTIMIZE worst events are `E1|E2`.

The incumbent KEEP worst-shortage relative change from demand-only to full
compound is 600.022%;
the minimum-fill-rate drop is
0.380914.
Fill and shortage values use a certified linear tie-break constrained to the
exact cost optimum; economic conclusions use the cost-only optimum.

## Frozen gates

- gate_1_inventory_configuration_change: **PASS**
- gate_2_cross_risk_interaction: **PASS**
- gate_3_reconfiguration_value: **PASS**
- gate_4_critical_scenario_adaptation: **PASS**
- gate_5_service_shortage_impact: **PASS**

Gate 2 reports an incremental interaction amount of
`520761.0690923005`;
it does not require or claim strict superadditivity. Worst sets use relative
tolerance `1e-6`, and top-3 comparisons expand ties.

## Sensitivity after MAIN

| magnitude | gate direction persistent | inventory ranking stable | critical scenario stable | classification |
|---|---|---|---|---|
| LOW | False | False | False | COMPOUND_RISK_FEASIBILITY_GO |
| HIGH | True | False | False | COMPOUND_RISK_FEASIBILITY_GO |

LOW and HIGH were run only after MAIN completed. Neither replaces MAIN, and no
shock magnitude or target was selected based on favorable output.

## Interpretation boundary

These results answer only whether this one verified small Favorita instance
shows feasibility signals under the preregistered event construction. They do
not establish generality, statistical significance, real route capacities, or
a formal paper conclusion.
