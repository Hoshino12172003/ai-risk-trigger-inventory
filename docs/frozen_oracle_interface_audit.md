# Frozen Oracle Interface Audit

## Result

`PASS` by static inspection only. No solver was called.

- Repository: `Hoshino12172003/budget-inventory-benders`
- Frozen commit: `51aebd06edf8f5d6d124d0f3eebdbb901e63274f`
- Source modification required: no
- Recommended integration: import the frozen Python callables from a detached,
  clean checkout after verifying its commit and worktree status
- Generic CLI available: no. `experiments/run_e2_policy_local.py` is bound to
  authorized Renault case IDs and committed identity artifacts.

## Input schema

The frozen input contract is
`robust_inventory_reconfiguration.instance.InventoryInstance`. Its fields are:

- identifiers: `name`, `depot_ids`, `region_ids`, `product_ids`
- demand: `base_demand[R][J]`, `demand_deviation[R][J]`
- recourse costs: `transport_cost[I][R][J]`,
  `shortage_penalty[R][J]`, `service_penalty[J]`
- service: `service_level[J]`
- inventory system: `capacity[I]`, `inventory_upper_bound[I][J]`,
  `fixed_depot_cost[I]`, `inventory_cost[I][J]`, `product_volume[J]`
- provenance fields: `initial_inventory`,
  `reconfiguration_cost_multiplier`, `provenance`

For this pilot, `base_demand` is the oracle `demand_bar` and
`demand_deviation` is `demand_hat`. `Gamma` is not stored in the instance; it is
passed as the `gamma` argument to both evaluation callables. Incumbent inventory
`x0[I][J]`, financial `budget`, and `lambda_r` are also explicit call arguments.

## KEEP

Exact callable:

```python
robust_inventory_reconfiguration.robust_service.evaluate_robust_service_detailed(
    instance, x0, gamma
)
```

This callable fixes supply constraints to the provided inventory matrix, so it
supports exact recourse evaluation with `x=x0` without changing frozen source.
It returns `UnifiedServiceResult`, including robust recourse cost and the
worst-recourse transportation, shortage, service-penalty, shortage-quantity,
and fill-rate decomposition.

KEEP first-stage cost uses the frozen helper
`first_stage_expenditure_value(instance, y0, x0, zeros, zeros, lambda_r)`.
The zero adjustment matrices make KEEP reconfiguration friction exactly zero.

## REOPTIMIZE

Exact callable:

```python
robust_inventory_reconfiguration.product_risk_budget_benders.solve_prb_benders(
    instance, x0, budget, gamma, lambda_r
)
```

The callable returns `PRBBendersResult`. A usable result must have
`status == "OPTIMAL"`, `exact_certification_pass == True`, and
`global_risk_budget_coupling_pass == True`. Its `solution` is a
`ReconfigurationSolution` containing objective, first-stage expenditure,
robust recourse cost, `y`, `x`, `a_plus`, `a_minus`, and reconfiguration cost.

## Objective identity and friction

The frozen PRB master defines:

```text
first_stage
= fixed depot cost
+ inventory cost
+ lambda_r * inventory_cost * (a_plus + a_minus)

objective
= first_stage
+ robust recourse cost
```

Therefore `solution.objective` already contains reconfiguration friction once.
The third-paper adapter must use this objective directly and must not add or
subtract the friction term again.

## KEEP/REOPTIMIZE comparability gate

Payloads must be byte-for-byte equivalent after removing only
`decision_mode`. The shared fields are instance/demand, costs, service levels,
Gamma, capacity, active network identity, evaluation horizon, x0, budget, and
lambda. Thus the only policy difference is fixed incumbent evaluation versus
allowed reoptimization.

The frozen checkout was clean before and after inspection. This audit did not
import Gurobi, build a model, or execute optimization.
