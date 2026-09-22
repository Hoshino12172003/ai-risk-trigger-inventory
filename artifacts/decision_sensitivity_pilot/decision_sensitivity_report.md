# Decision-Sensitivity Five-State Chain Report

Status: `CHAIN_FAIL`

This was an exploratory five-state chain validation, not a formal experiment.
It used an **ex-post decision-sensitivity diagnostic** definition in which
`base_demand` is current realized weekly demand. It must not be described as a
forecast, prospective trigger, real-time predictive policy, or out-of-sample
AI decision.

The all-state pre-solve consistency gate passed. Execution then stopped on the
first state exactly as required. Frozen
`evaluate_robust_service_detailed` reached Gurobi status 13 (`SUBOPTIMAL`) in
its recourse-reporting tie-break for
`pichincha-pichincha-context-2-20140106`. The frozen checkout remained clean.

- Fully executed states: 0/5
- KEEP successes: 0/5
- Exact-certified REOPTIMIZE successes: 0/5
- Actual frozen solver callable dispatches: 1
- Batch runtime: 1.315242 seconds

No KEEP result was returned and no REOPTIMIZE call was made. Therefore no
decision value, inventory-change result, descriptive counterexample, or
structural-signal classification exists for this run.
