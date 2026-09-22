# Decision-Sensitivity Five-State Chain Report

Status: `CHAIN_PASS_COST_ONLY_WITH_SCALE_WARNING`

This is an exploratory five-state chain validation, not a formal experiment.
It is an **ex-post decision-sensitivity diagnostic**: `base_demand` is the
current realized weekly demand. The result describes the inventory
reconfiguration consequences of demand changes that have already occurred.
It is not a forecast, prospective trigger, real-time predictive policy, or
out-of-sample AI decision.

- Executed states: 5/5
- KEEP successes: 5/5
- Exact-certified REOPTIMIZE successes: 5/5
- Policy executions: 10
- Actual frozen solver callable dispatches: 15
- Batch runtime: 4.467079 seconds
- Decision value range: [42219.074902, 404641282330.185791]
- Inventory-change L1 range: [16496.030601, 241349.771647]
- Median recourse/first-stage ratio: 0.985337
- Maximum recourse/first-stage ratio: 1331309.227136

Every result used the same demand state, cost and service parameters, Gamma,
`lambda_R`, capacity, active network, and evaluation horizon for KEEP and
REOPTIMIZE. Frozen PRB-Benders reconfiguration friction is already included in
the REOPTIMIZE first-stage expenditure and was not charged again. All five
cost identities and dominance checks passed.

KEEP used only the exact Stage-1 LP block optima. REOPTIMIZE used frozen
PRB-Benders and each returned robust recourse cost matched an independent
cost-only evaluation within the preregistered tolerance. Reporting-QP-dependent
service and decomposition fields are deliberately unavailable.

## Descriptive chain observations

| state_id | relative demand shift | decision value | inventory change L1 |
|---|---:|---:|---:|
| pichincha-pichincha-context-2-20140106 | 0.403641 | 404641282330.185791 | 241349.771647 |
| pichincha-pichincha-context-1-20160321 | -0.109994 | 73740.539156 | 77621.620165 |
| pichincha-pichincha-context-2-20160502 | -0.032624 | 49348.503365 | 37123.938751 |
| guayas-guayas-context-2-20170109 | -0.186517 | 42219.074902 | 44441.131476 |
| guayas-guayas-context-2-20170501 | 0.070935 | 5880999106.448388 | 16496.030601 |

These five rows are shown only for visual inspection. They do not support a
statistical inference or a paper-level structural-signal classification. No
`STRONG_STRUCTURAL_SIGNAL`, `PARTIAL_STRUCTURAL_SIGNAL`, or
`WEAK_OR_NO_STRUCTURAL_SIGNAL` label is assigned because the preregistered
minimum of 20 executed states has not been reached.

## Required feasibility answers

1. Exact cost-only KEEP evaluation succeeded for 5/5 states.
2. Frozen REOPTIMIZE was exactly certified for 5/5 states.
3. Independent cost-only recourse identity passed for 5/5 states.
4. Decision-value dominance passed for 5/5 states.
5. The recourse/first-stage ratios are reported above and in
   `cost_scale_audit.csv`; they exceed the fixed scale-warning threshold of
   1000.
6. Whether service/shortage penalty calibration dominates is `INCONCLUSIVE`:
   penalty maxima and total exact recourse costs alone do not identify a
   tie-break-independent component decomposition.
7. Descriptively, `guayas-guayas-context-2-20170501` has a relatively small
   shift (0.070935) but decision value about 5.881e9, while
   `guayas-guayas-context-2-20170109` has a larger absolute shift (0.186517)
   but the batch-minimum decision value of about 4.222e4. The smallest and
   largest absolute-shift rows are `pichincha-pichincha-context-2-20160502`
   and `pichincha-pichincha-context-2-20140106` respectively. These are
   preliminary sample contrasts, not statistical inference.
8. Do not advance to the 20-state structural-signal pilot until the extreme
   cost scale has been reviewed. No calibration parameter was changed here.
