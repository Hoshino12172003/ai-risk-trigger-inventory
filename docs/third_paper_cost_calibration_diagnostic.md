# Third-Paper Cost Calibration Diagnostic

This is an exploratory calibration diagnostic over the existing five states.
It does not change the frozen Paper-2 model, uncertainty set, Gamma,
`lambda_R`, incumbent inventory, capacity, budget, or any formal parameter.

Classification: `STRUCTURAL_SIGNAL_ROBUST_TO_CALIBRATION`

Formal-calibration candidate status:
`CANDIDATE_FOR_FORMAL_CALIBRATION`

## CURRENT worst-scenario decomposition

| state_id | transport cost | transport share | shortage cost | shortage share | service cost | service share | exact recourse |
|---|---:|---:|---:|---:|---:|---:|---:|
| pichincha-pichincha-context-2-20140106 | 268029.621407 | 0.000001 | 2708610.120738 | 0.000007 | 404639000038.942078 | 0.999993 | 404641976678.684204 |
| guayas-guayas-context-2-20170501 | 323296.383479 | 0.000055 | 133835.465450 | 0.000023 | 5880893137.813797 | 0.999922 | 5881350269.662726 |

These components come from the Stage-1 LP solution selected by Gurobi. Their
sum equals the exact robust recourse total, but component uniqueness across
alternative Stage-1 optima is not established. No reporting tie-break QP was
used.

## Recourse scale

| scheme | median recourse/first-stage | maximum recourse/first-stage |
|---|---:|---:|
| CURRENT | 0.985337 | 1331309.227136 |
| UNIT_COST_BASED | 0.985337 | 50.795133 |
| TRANSPORT_BASED | 0.985337 | 47.427135 |

## Decision-value rank stability

| comparison | Spearman | left top-1 | right top-1 |
|---|---:|---|---|
| CURRENT_vs_UNIT_COST_BASED | 1.000000 | pichincha-pichincha-context-2-20140106 | pichincha-pichincha-context-2-20140106 |
| CURRENT_vs_TRANSPORT_BASED | 1.000000 | pichincha-pichincha-context-2-20140106 | pichincha-pichincha-context-2-20140106 |
| UNIT_COST_BASED_vs_TRANSPORT_BASED | 1.000000 | pichincha-pichincha-context-2-20140106 | pichincha-pichincha-context-2-20140106 |

## Diagnostic answers

1. CURRENT service-penalty dominance for both anomalous states:
   `True`.
2. B/C significant extreme-ratio reduction:
   `True`.
3. The maximum ratio falls below 100 under both B and C, so the CURRENT
   10^4--10^6 extreme scale is removed by both preregistered probes.
4. Decision-value ordering stability is reported by the three Spearman values.
5. The descriptive pattern screen is:

| scheme | small shift / large decision | large shift / small decision |
|---|---|---|
| CURRENT | guayas-guayas-context-2-20170501 | guayas-guayas-context-2-20170109 |
| UNIT_COST_BASED | guayas-guayas-context-2-20170501 | guayas-guayas-context-2-20170109 |
| TRANSPORT_BASED | guayas-guayas-context-2-20170501 | guayas-guayas-context-2-20170109 |

6. Both patterns persist under B and C. This is descriptive only for five
   states and is not a statistical inference.
7. Inventory-change ordering: `{'CURRENT': ['pichincha-pichincha-context-2-20140106', 'pichincha-pichincha-context-1-20160321', 'guayas-guayas-context-2-20170109', 'pichincha-pichincha-context-2-20160502', 'guayas-guayas-context-2-20170501'], 'UNIT_COST_BASED': ['pichincha-pichincha-context-2-20140106', 'pichincha-pichincha-context-1-20160321', 'guayas-guayas-context-2-20170109', 'pichincha-pichincha-context-2-20160502', 'guayas-guayas-context-2-20170501'], 'TRANSPORT_BASED': ['pichincha-pichincha-context-2-20140106', 'pichincha-pichincha-context-1-20160321', 'guayas-guayas-context-2-20170109', 'pichincha-pichincha-context-2-20160502', 'guayas-guayas-context-2-20170501']}`.
8. States extreme only under CURRENT: `['guayas-guayas-context-2-20170501', 'pichincha-pichincha-context-2-20140106']`.

For both anomalous CURRENT states, the service term contributes more than
99.99% of exact recourse. Because that term is the unit service-penalty
coefficient multiplied by the service-violation variable, the decomposition
supports coefficient-times-violation amplification as the proximate numerical
mechanism. It does not establish that calibration as the only possible cause.

This classification is not a paper conclusion. A candidate still requires
literature support, economic interpretation, and formal sensitivity analysis.
