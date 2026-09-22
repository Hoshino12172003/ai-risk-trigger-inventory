# KEEP Oracle Failure Root-Cause Diagnostic

This diagnostic is limited to
`pichincha-pichincha-context-2-20140106`. It does not change the frozen Paper-2
repository, run REOPTIMIZE, execute the other four states, or create a formal
oracle result.

## Frozen two-stage behavior

Static inspection of
`robust_inventory_reconfiguration.robust_service.evaluate_robust_service_detailed`
at commit `51aebd06edf8f5d6d124d0f3eebdbb901e63274f` confirms:

1. Stage 1 minimizes the sum of linear transportation, shortage, and service
   penalty costs over 48 independent product/shock blocks.
2. After Stage 1 is optimal, the function records each block optimum and adds
   `block_cost <= block_optimum + 1e-7`.
3. Stage 2 minimizes the quadratic reporting objective `sum(shortage^2)` to
   select a canonical allocation used for detailed reporting.

The observed failure is in Stage 2, not the core recourse-cost LP.

## Diagnostic result

Stage 1 was `OPTIMAL`. Its joint all-block objective was
`5,502,046,046,769.015`; composing the 48 exact block optima over the frozen
global Gamma scenarios gives an exact robust recourse cost of
`404,641,976,678.6842`. Maximum primal violation was approximately
`1.91e-11`, dual violation was `0`, Kappa was `12`, and KappaExact was `40`.

The original Stage-2 QP used the frozen profile and returned `SUBOPTIMAL`
after 136 barrier iterations. Dividing only the quadratic objective by
`6,626,660,791.128722` preserved every constraint and theoretical argmin but
also returned `SUBOPTIMAL`, after 165 barrier iterations. Objective scaling
reduced the reported maximum primal violation from approximately `49,490.94`
to `0.337`, but did not reach the frozen `1e-8` feasibility tolerance.

Therefore the permitted classification is:

`REPORTING_QP_UNRESOLVED`

The evidence is consistent with a numerical scaling problem, but does not
confirm that quadratic-objective scaling alone is the root cause. The Stage-2
matrix spans `1` to `2,689,410.38`, its RHS spans approximately `1,636.50` to
`346,201,736,512.05`, and Gurobi explicitly warned about the large RHS.

## Scale evidence

- Maximum possible region-product demand: `81,404.3045`
- Median possible region-product demand: `29,196`
- Theoretical maximum single shortage scale: `81,404.3045`
- Estimated single `shortage^2` scale: `6,626,660,791.128722`
- Maximum shortage penalty: `17`
- Maximum service penalty: `2,689,410.38`
- Largest/smallest nonzero cost-coefficient ratio: `2,689,410.38`
- Original mathematical QP coefficient: `1`
- Rescaled mathematical QP coefficient: approximately `1.509056e-10`

## Cost-only feasibility boundary

`COST_ONLY_KEEP_EVALUATOR_FEASIBLE`

The Stage-1 block optima are sufficient to calculate the robust recourse cost:
for every global Gamma scenario, select the corresponding per-product shock
block costs, sum them, and take the maximum. This quantity is the only Stage-2
output essential to the KEEP decision cost. The calibrated first-stage KEEP
cost is already independently defined, so the total KEEP cost does not
mathematically require the reporting QP.

The following remain reporting-only when degeneracy makes their allocation
depend on the tie-break:

- canonical shortage allocation;
- minimum fill rate;
- worst-region identity;
- transportation, shortage, and service-penalty decomposition.

This finding establishes feasibility only. The formal adapter was not changed,
and no cost-only bypass was used to generate a decision value.
