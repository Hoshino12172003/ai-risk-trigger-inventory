# Compound-risk Stage 1 protocol

## Scope and status

This is a preregistered exploratory feasibility prototype, not a formal paper
experiment and not statistical evidence. It does not train or call generative
AI, implement column-and-constraint generation (CCG), use M5, modify the frozen
Paper-2 repository, or invoke Paper-2 PRB for compound risk.

- third-paper base commit: `2f0ea2d0af2af5424cf947866a6b1e9ca121859c`
- frozen Paper-2 commit: `51aebd06edf8f5d6d124d0f3eebdbb901e63274f`
- calibration role: `PRIMARY_PILOT_CALIBRATION`
- calibration scheme: `UNIT_COST_BASED`
- uncertainty budget: `Gamma = 2`

## Ex-ante base-state selection

The source is the existing 20-state Favorita pilot. A normal week is defined by
the existing construction rule: `holiday_flag == false` and promotion intensity
not above the median promotion intensity of the 20 candidates. Within those
rows, selection sorts by:

1. `abs(relative_demand_shift)` ascending;
2. state name lexically;
3. date ascending;
4. stable `state_id` lexically.

This rule selects `pichincha-pichincha-context-2-20151012`; it is fixed before
any compound-risk result is computed.

## Event and route selection

Exactly six event atoms are constructed. `E1` targets the largest baseline
region-product demand cell. `E2` targets the largest cell with a different
region and, where feasible, a different product; ties use stable region ID then
product ID. Main flash shocks are +40% and +60%.

Stable region IDs are sorted lexically. `E3` affects the first two regions with
+20% demand for all products and 50% loss on each region's assigned route.
`E4` affects the last two, disjoint regions with +30% demand and the same route
loss. The middle region is the non-regional target. The assigned warehouse is
the minimum-transport-cost warehouse in the calibrated instance, with stable
warehouse ID as tie-break; this is the existing deterministic nearest/assigned
node proxy, not an observed physical assignment.

`E5` applies 75% loss to the assigned route of the middle region. `E6` applies
100% loss to the first stable route having a different warehouse and region
from E5 while avoiding the exact assigned routes used by E3/E4 where possible.
Candidate routes are ordered by warehouse ID then region ID. Routes are never
selected from optimization results.

## Scenario composition

Each event has binary indicator `xi_e`. Scenarios are enumerated
programmatically for `sum_e xi_e <= Gamma`. Full compound therefore has 1 empty,
6 singleton, and 15 two-event scenarios (22 total). Demand shocks that overlap
are additive on baseline demand. Route loss fractions are additive and capped
at 1; availability is one minus capped loss. Effects are applied exactly once.

The eight regimes are `NOMINAL`, `DEMAND_ONLY`, `REGIONAL_ONLY`, `NETWORK_ONLY`,
`DEMAND_PLUS_REGIONAL`, `DEMAND_PLUS_NETWORK`, `REGIONAL_PLUS_NETWORK`, and
`FULL_COMPOUND`. Each uses `min(2, number of available events)`; nominal contains
only the empty scenario. Counts are 1 for nominal, 4 for each two-event regime,
11 for each four-event regime, and 22 for full compound.

## Effective route capacity

Paper 2 has no observed physical route capacities. This prototype therefore
uses an exploratory effective route service fraction `a_ir(s)` in `[0,1]`:

`shipment_irj_s <= a_ir(s) * demand_rj(s)`.

Nominal availability is 1, so one warehouse-region arc can carry the region's
entire product demand and the original model is not artificially restricted.
Availability 0.25 represents 75% effective loss and 0 represents closure. These
are scenario service fractions, not claims about real units/day capacity.

## Exact benchmark and metrics

KEEP fixes inventory exactly to incumbent `x0` and solves exact cost-only linear
recourse. REOPTIMIZE is a local finite-scenario extensive form with Paper-2
compatible depot activation, inventory, volume capacity, inventory bounds,
financial budget, inventory conservation, shortage accounting, and service
logic. Its objective is first-stage fixed plus inventory cost, reconfiguration
friction exactly once, and worst-case recourse `theta`. Every scenario has its
own shipment, shortage, and service-excess variables plus the effective-route
constraint. All solves must report exact `OPTIMAL` status.

Economic recourse cost is the primary result. A linear second pass minimizes
total shortage while holding every scenario at its exact cost optimum; fill
rate is reported only when this pass is optimal. Network metrics are:

- rerouted volume proxy: shipment on a region's non-assigned warehouse arcs;
- backup warehouse utilization proxy: rerouted volume divided by total served
  demand (zero when no demand is served);
- route-loss exposure: sum over arcs/products of
  `(1 - availability_ir) * demand_rj`;
- served demand under disrupted arcs: shipment on arcs with availability below
  one.

Worst ties use relative tolerance `1e-6`. A top-3 set includes every scenario
tied with the third-ranked cost.

## Frozen gates

The following thresholds are frozen before execution and must not be changed
after observing results.

1. **Inventory configuration change:** FULL_COMPOUND versus DEMAND_ONLY
   reoptimized inventory normalized L1 difference is at least 5%, where the
   denominator is `max(sum(x_demand), epsilon)`.
2. **Cross-risk interaction:** a tied FULL_COMPOUND worst scenario contains at
   least two event types and, at the same fixed reoptimized inventory, its
   recourse cost is at least 5% above the worse matching singleton. Incremental
   amount is also reported; strict superadditivity is not required.
3. **Reconfiguration value:** FULL_COMPOUND KEEP cost exceeds REOPTIMIZE and
   `(KEEP - REOPTIMIZE) / KEEP >= 1%`.
4. **Critical-scenario adaptation:** tied worst active-event sets differ between
   KEEP and REOPTIMIZE, or their tie-expanded top-3 sets/compositions differ.
5. **Service/shortage impact:** at incumbent KEEP, FULL_COMPOUND versus
   DEMAND_ONLY worst shortage rises at least 10%, or certified minimum fill rate
   drops at least 0.02. If fill rate is unavailable, only shortage is used.

Classification is `COMPOUND_RISK_FEASIBILITY_GO` for 4-5 passes,
`COMPOUND_RISK_FEASIBILITY_PARTIAL` for 2-3, and
`COMPOUND_RISK_FEASIBILITY_WEAK` for 0-1. This classification remains
exploratory.

## Magnitude sensitivity

Sensitivity is run only after MAIN finishes. Targets, Gamma, and gates do not
change. LOW uses flash +20%/+30%, regional +10%/+15% and 25% route loss, and
network 50%/75% loss. HIGH uses flash +60%/+80%, regional +30%/+40% and 75%
route loss, and network 100%/100% loss. It evaluates gate-direction persistence,
inventory ranking stability, and critical-scenario stability; it cannot replace
MAIN.

## Stop rules

Execution stops without tuning if scenario counts or validation fail, route
availability leaves `[0,1]`, a model is unexpectedly infeasible or non-optimal,
cost identities fail, reconfiguration friction is double-counted, nominal
semantics do not reproduce the baseline, or the frozen checkout changes.
