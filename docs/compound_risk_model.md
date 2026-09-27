# Event-driven compound-risk inventory reconfiguration model

## 1. Status and problem setting

This document formalizes the Stage-1 implementation as a general event-driven,
two-stage robust inventory reconfiguration model. It is a mathematical model
specification, not a new experiment. It does not train or call generative AI,
estimate impact magnitudes, implement column-and-constraint generation (CCG),
use M5, or claim that the Paper-2 solution method applies unchanged.

An enterprise begins with incumbent inventory `x^0`. Before risk realizes it
may change warehouse activation and inventory, paying reconfiguration friction.
Nature then selects a binary compound-event vector from a budgeted uncertainty
set. Finally, continuous shipment, shortage, and service-excess recourse adapts
to the realized events.

The decision timeline is:

> first-stage activation and inventory → Nature selects compound events →
> second-stage operational recourse.

## 2. Objects and terminology

| Object | Definition |
|---|---|
| Event atom | One risk cause `e`, with externally supplied demand and/or route impacts. |
| Event vector | `ξ = (ξ_e)`, the binary activation state of all atoms. |
| Scenario | One concrete realization of `ξ`; it is not the primitive model object. |
| Uncertainty set | All event vectors admissible under the global and optional type budgets. |

Future work must not use “event” and “scenario” interchangeably.

## 3. Sets

- `I`: inventory nodes or warehouses.
- `R`: demand regions.
- `J`: product families.
- `E`: all event atoms.
- `E_D`: `FLASH_DEMAND_SURGE` atoms.
- `E_R`: `REGIONAL_EMERGENCY_DISRUPTION` atoms.
- `E_N`: `TRANSPORT_NETWORK_DISRUPTION` atoms.

The event types form a partition:

`E = E_D ∪ E_R ∪ E_N`, with `E_D`, `E_R`, and `E_N` pairwise disjoint.

## 4. Parameters

| Symbol | Meaning |
|---|---|
| `x^0_ij ≥ 0` | incumbent inventory |
| `d̄_rj ≥ 0` | baseline demand |
| `δ^D_erj ≥ 0` | relative demand uplift caused by event `e` |
| `δ^A_eir ∈ [0,1]` | effective route-service loss caused by event `e` |
| `f_i ≥ 0` | warehouse fixed activation cost |
| `h_ij ≥ 0` | inventory unit cost; also weights inventory adjustment |
| `w_j ≥ 0` | product volume coefficient |
| `K_i ≥ 0` | warehouse volume capacity |
| `M_ij ≥ 0` | warehouse-product inventory upper bound |
| `B ≥ 0` | financial budget |
| `λ_R ≥ 0` | reconfiguration-friction multiplier |
| `c_irj ≥ 0` | transportation unit cost |
| `p_rj ≥ 0` | shortage unit cost |
| `α_j ∈ [0,1]` | aggregate product service target |
| `π_j ≥ 0` | penalty per unit of aggregate product service excess |

The impact tensors are calibrated or externally supplied model parameters. In
Stage 2 they are not LLM outputs and are not observed physical route
capacities. Stage-1 MAIN values are only preregistered exploratory stress-test
magnitudes; formal calibration belongs to Stage 3.

## 5. Variables

### First stage

- `y_i ∈ {0,1}`: warehouse activation.
- `x_ij ≥ 0`: pre-positioned inventory.
- `a^+_ij, a^-_ij ≥ 0`: inventory increases and decreases.

### Nature

- `ξ_e ∈ {0,1}`: event activation. Magnitudes reside in `δ^D` and `δ^A`, not
  in `ξ`.

### Second stage for a fixed `ξ`

- `q_irj(ξ) ≥ 0`: shipment from warehouse `i` to region `r` for product `j`.
- `u_rj(ξ) ≥ 0`: shortage.
- `e_j(ξ) ≥ 0`: aggregate product-level service excess.

The implementation has one `e_j` per product, not a regional `e_rj` or
`v_rj`. Consequently the model does not impose a regional service guarantee.

## 6. Event-driven budgeted uncertainty set

The general set is

```text
U(Γ, Γ_D, Γ_R, Γ_N) = {
  ξ ∈ {0,1}^|E| :
  Σ_{e∈E}   ξ_e ≤ Γ,
  Σ_{e∈E_D} ξ_e ≤ Γ_D       when Γ_D is enabled,
  Σ_{e∈E_R} ξ_e ≤ Γ_R       when Γ_R is enabled,
  Σ_{e∈E_N} ξ_e ≤ Γ_N       when Γ_N is enabled
}.
```

If a type-specific budget is unset, its constraint is absent. With all three
unset, only `Σ_e ξ_e ≤ Γ` applies.

`Γ` is not a probability parameter. For example, `Γ=2` does not state that two
events are most probable, nor assign a probability to two-event realizations.
It is a robustness budget: the maximum number of simultaneous event atoms that
Nature may activate. The set also does not claim that admissible combinations
are equally likely.

Without type caps, the number of event vectors is

`N(n,Γ) = Σ_{k=0}^{Γ} C(n,k)`.

With category caps, counting instead sums
`C(|E_D|,k_D) C(|E_R|,k_R) C(|E_N|,k_N)` over category counts satisfying every
enabled cap and `k_D+k_R+k_N≤Γ`.

## 7. Demand transformation

For a realized event vector,

```text
d_rj(ξ) = d̄_rj [1 + Σ_{e∈E} δ^D_erj ξ_e].
```

Shocks are additive on baseline. A +40% and a +20% event affecting the same
cell produce `1.60 d̄`, not `1.40×1.20 d̄`.

## 8. Route-service transformation

Raw route loss and effective availability are

```text
L_ir(ξ) = Σ_{e∈E} δ^A_eir ξ_e,
a_ir(ξ) = max{0, 1 - L_ir(ξ)}.
```

Thus losses 0, 0.50, 0.75, and at least 1 yield availability 1, 0.50, 0.25,
and 0. `a_ir(ξ)` is an effective service fraction, not observed physical
transport capacity in units/day.

## 9. First-stage model

Reconfiguration accounting and friction are

```text
x_ij - x^0_ij = a^+_ij - a^-_ij                         ∀i,j,
R(x;x^0) = λ_R Σ_{i∈I,j∈J} h_ij(a^+_ij+a^-_ij).
```

The Stage-1-compatible feasible set is

```text
Σ_j w_j x_ij ≤ K_i y_i                                  ∀i,
x_ij ≤ M_ij y_i                                          ∀i,j,
x_ij, a^+_ij, a^-_ij ≥ 0; y_i ∈ {0,1},
Σ_i f_i y_i + Σ_{i,j} h_ij x_ij + R(x;x^0) ≤ B.
```

The second constraint is both the product-specific inventory limit and the
fixed-cost activation linkage. No additional product-specific first-stage
limit exists in the current implementation.

Define the base first-stage cost, excluding friction, as

`C_first(x,y) = Σ_i f_i y_i + Σ_{i,j} h_ij x_ij`.

## 10. Second-stage recourse

For fixed `x` and `ξ`, Stage 1 implements demand **coverage**:

```text
Σ_i q_irj(ξ) + u_rj(ξ) ≥ d_rj(ξ)                         ∀r,j.   (DC)
```

This is deliberately an inequality, not an equality. Under the current
nonnegative objective coefficients—and strictly positive shipment/shortage
coefficients in the verified instance—an economic optimum is expected to have
no gratuitous over-coverage: excess positive `q` or `u` can be reduced until
(DC) binds, lowering cost without violating upper bounds. This is an
economic-optimality observation, not a statement that the feasible regions of
`≥` and `=` are identical. For example, any point with
`Σ_i q_irj+u_rj>d_rj` is feasible under `≥` and infeasible under `=`. Stage 2
retains `≥` to preserve exact implementation consistency.

Inventory availability is

```text
Σ_r q_irj(ξ) ≤ x_ij                                      ∀i,j.
```

Event-dependent route service is

```text
q_irj(ξ) ≤ a_ir(ξ) d_rj(ξ)                               ∀i,r,j.
```

At availability one, a single arc may serve the complete region-product
demand, so the nominal case adds no tighter route restriction. At zero the arc
is unavailable. This is an effective demand-share upper bound, not a physical
route-capacity measurement.

The actual aggregate service constraint is

```text
Σ_r u_rj(ξ) - e_j(ξ)
  ≤ (1-α_j) Σ_r d_rj(ξ)                                  ∀j.
```

Hence `e_j` absorbs total shortage for product `j` beyond its aggregate
allowance and is penalized once with coefficient `π_j`.

The economic recourse function is

```text
Q(x,ξ) = min_{q,u,e}
    Σ_{i,r,j} c_irj q_irj(ξ)
  + Σ_{r,j}   p_rj u_rj(ξ)
  + Σ_j       π_j e_j(ξ)
```

subject to the four recourse constraint families above and nonnegativity. The
linear shortage tie-break used for Stage-1 reporting is not part of `Q`.

## 11. Full robust model

The model is

```text
min_{y,x,a+,a-}  C_first(x,y) + R(x;x^0)
                  + max_{ξ∈U} Q(x,ξ)
subject to the first-stage constraints.
```

Equivalently, it has the `min`–`max`–`min` sequence:

```text
min first-stage activation/inventory/adjustment
max admissible compound-event vector ξ
min continuous shipment/shortage/service recourse.
```

For a finite realization list, Stage 1 introduces `θ` with
`θ ≥ recourse_cost(ξ)` for every realization and minimizes
`C_first + R + θ`.

## 12. KEEP, REOPTIMIZE, and reconfiguration value

KEEP freezes `x=x^0` and `y=y^0`. Its implemented incumbent first-stage cost is

`C_inc = Σ_i f_i y^0_i + Σ_{i,j} h_ij x^0_ij`.

No reconfiguration friction is charged, so

```text
KEEP(U) = C_inc + max_{ξ∈U} Q(x^0,ξ).
```

REOPTIMIZE chooses feasible `x,y,a^+,a^-` before Nature and pays friction once:

```text
REOPTIMIZE(U) = min [C_first(x,y)+R(x;x^0)+max_{ξ∈U}Q(x,ξ)].
```

The economic value of advance reconfiguration is

```text
RV(U)  = KEEP(U) - REOPTIMIZE(U),
RRV(U) = RV(U) / KEEP(U), when KEEP(U)>0.
```

## 13. Why this is a compound-risk model

Flash-demand atoms primarily have `δ^D>0, δ^A=0`. Network-disruption atoms
primarily have `δ^D=0, δ^A>0`. A regional-emergency atom may have both
`δ^D>0` and `δ^A>0`; the same `ξ_e` then changes demand and route service.
This event-induced coupling is not a mechanical addition of two independent
uncertainty sets.

## 14. Exact relationship with Stage 1

Stage 1 is the special case with `E={E1,…,E6}`, `Γ=2`, disabled type-specific
budgets, and preregistered MAIN stress-test impacts. Therefore

`|U| = Σ_{k=0}^2 C(6,k) = 1+6+15 = 22`.

The solver-free audit compares all 22 active-event sets and their generated
demand multipliers and route availabilities against both the Stage-1 generator
and committed scenario catalog. It does not rerun optimization.

## 15. Relationship with Paper 2

Paper 2 is demand-only robust inventory reconfiguration. Stage 2 retains its
incumbent inventory, first-stage inventory structure, reconfiguration friction,
transport recourse, shortage/service recourse, and robust worst-case logic.
The third-paper model adds event atoms, event budgets, event-linked demand,
route-service disruption, compound coupling, and optional type-specific
budgets. Paper-2 PRB is not assumed valid for compound risk. It remains a
possible benchmark for compatible demand-only special cases.

## 16. Assumptions and limitations

- **A1.** Event activation is binary.
- **A2.** Demand shocks are additive on baseline.
- **A3.** Route losses are additive and capped at 100%.
- **A4.** Event impacts are deterministic conditional on activation.
- **A5.** Impact magnitudes are externally calibrated, not learned in Stage 2.
- **A6.** Effective route fractions are calibrated constructs, not observed
  physical capacities.
- **A7.** Second-stage recourse is continuous.
- **A8.** Shortage recourse preserves feasibility unless another hard
  constraint prevents it.
- **A9.** `Γ` controls robustness/conservatism, not event probability.
- **A10.** The uncertainty set does not claim equal likelihood for admissible
  combinations.

Stage-1 magnitudes (+40%, +60%, +20%, +30%, 50%, 75%, and 100% losses) are
stress-test values, not empirically estimated, real-world-true, or data-driven
calibrations.

## 17. Future structured-event and CCG interfaces

A future GenAI component may produce structured event metadata. A separate
calibration module would map that metadata to `δ^D` and `δ^A`; the optimization
model accepts only the resulting calibrated parameters.

A future separation interface receives fixed first-stage `x` and seeks
`max_{ξ∈U} Q(x,ξ)`. Stage 2 does not implement it. Open algorithmic issues are:

1. event variables jointly affect demand and routes;
2. one event may affect many products, regions, and routes;
3. `a_ir(ξ)d_rj(ξ)` is piecewise and contains event-indicator interactions or
   a bilinear expansion in an adversarial formulation;
4. type-specific budgets alter adversarial structure; and
5. Paper-2 product-risk decomposition may no longer apply.

These are explicit future CCG/separation TODOs; Stage 2 does not change the
route-service semantics to avoid them.
