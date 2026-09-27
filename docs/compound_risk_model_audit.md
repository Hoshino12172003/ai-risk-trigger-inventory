# Compound-risk model implementation audit

## Scope and result

This audit maps the formal Stage-2 equations to the committed Stage-1 source at
`bcc1ec5a84c2fa756911bff3282443897603f8d2`. It is source inspection plus a
solver-free uncertainty-set equivalence check. No optimization experiment was
rerun. Paper-2 remained read-only at
`51aebd06edf8f5d6d124d0f3eebdbb901e63274f`.

## Code-name mapping

| Implementation | Mathematics | Indexing / role | Match |
|---|---|---|---|
| `y[i]` | `y_i` | warehouse activation | EXACT_MATCH |
| `x[i,j]` | `x_ij` | warehouse-product inventory | EXACT_MATCH |
| `plus[i,j]`, `minus[i,j]` | `a^+_ij`, `a^-_ij` | inventory adjustment | EXACT_MATCH |
| event indicator | `ξ_e` | one binary value per event | EXACT_MATCH |
| `q[i,r,j]` | `q_irj(ξ)` | shipment | EXACT_MATCH |
| `u[r,j]` | `u_rj(ξ)` | shortage | EXACT_MATCH |
| `e[j]` | `e_j(ξ)` | one aggregate service-excess variable per product | EXACT_MATCH |
| `theta` | `θ` | worst-case recourse epigraph | EXACT_MATCH |

## First-stage equation audit

| Equation | Stage-1 implementation | Formal equation | Status |
|---|---|---|---|
| Warehouse volume/capacity | `solve_reoptimize`: `quicksum(product_volume[j]*x[i,j]) <= capacity[i]*y[i]` | `Σ_j w_jx_ij ≤ K_iy_i` | EXACT_MATCH |
| Inventory bound and activation linkage | `x[i,j] <= inventory_upper_bound[i][j]*y[i]` | `x_ij ≤ M_ijy_i` | EXACT_MATCH |
| Inventory feasibility | `x`, `plus`, `minus` created with lower bound zero; `y` binary | stated domains | EXACT_MATCH |
| Reconfiguration balance | `x[i,j]-x0[i][j] == plus[i,j]-minus[i,j]` | `x_ij-x^0_ij=a^+_ij-a^-_ij` | EXACT_MATCH |
| Base first-stage cost | fixed-depot expression plus inventory-cost expression | `Σ_i f_iy_i+Σ_ij h_ijx_ij` | EXACT_MATCH |
| Friction | `lambda_r * Σ inventory_cost*(plus+minus)` | `λ_RΣ_ij h_ij(a^+_ij+a^-_ij)` | EXACT_MATCH |
| Financial budget | `first_stage = base_first_stage + friction`; `first_stage <= budget` | `C_first+R≤B` | EXACT_MATCH |
| Product-specific limits | only `x_ij≤M_ijy_i`; product volume also enters warehouse capacity | same | EXACT_MATCH |
| Fixed-cost linkage | fixed cost is in objective/budget; `x_ij≤M_ijy_i` prevents inventory at inactive warehouse | same | EXACT_MATCH |

The objective later uses `base_first_stage + friction + theta`. Because
`base_first_stage` excludes friction, friction is counted exactly once.

## Demand-coverage audit (required item A)

| Location | Implemented relation | Documented relation | Status |
|---|---|---|---|
| `evaluate_exact_recourse` cost-only evaluator | `Σ_i q_irj + u_rj >= d_rj` | `Σ_i q_irj(ξ)+u_rj(ξ) ≥ d_rj(ξ)` | EXACT_MATCH |
| `solve_reoptimize` extensive form | `Σ_i q_irj + u_rj >= d_rj` | `Σ_i q_irj(ξ)+u_rj(ξ) ≥ d_rj(ξ)` | EXACT_MATCH |

The `≥` feasible region strictly contains the equality feasible region. With
the current positive economic costs, gratuitous shipment or shortage can be
reduced until coverage binds, so an economic optimum is expected to be tight.
That observation does not justify silently substituting equality; Stage 2 keeps
the implemented inequality.

## Service-aggregation audit (required item B)

| Equation / item | Implementation | Formal model | Status |
|---|---|---|---|
| Inventory availability | `Σ_r q[i,r,j] <= x[i,j]` in both recourse paths | `Σ_r q_irj(ξ)≤x_ij` | EXACT_MATCH |
| Route service | `q[i,r,j] <= route_availability[i][r]*demand[r][j]` | `q_irj(ξ)≤a_ir(ξ)d_rj(ξ)` | EXACT_MATCH |
| Service variable | `e = addVars(products, lb=0)` | `e_j(ξ)≥0` | EXACT_MATCH |
| Aggregation level | one `e[j]`; shortage summed across all regions for product `j` | aggregate product, not region-product | EXACT_MATCH |
| Service constraint | `Σ_r u[r,j]-e[j] <= (1-service_level[j])*Σ_r demand[r][j]` | `Σ_r u_rj(ξ)-e_j(ξ)≤(1-α_j)Σ_r d_rj(ξ)` | EXACT_MATCH |
| Objective coefficient | `service_penalty[j] * e[j]` | `π_j e_j(ξ)` | EXACT_MATCH |

This is not a regional service guarantee. The Stage-1 cost-only evaluator and
REOPTIMIZE extensive form use the same aggregate expression.

## Transformation and objective audit

| Equation | Stage-1 implementation | Stage-2 specification | Status |
|---|---|---|---|
| Demand | initialize multiplier 1, then add every active event shock | `d̄_rj(1+Σ_eδ^D_erjξ_e)` | EXACT_MATCH |
| Raw route loss | add active event losses | `L_ir=Σ_eδ^A_eirξ_e` | EXACT_MATCH |
| Availability | cap cumulative loss at one, then `1-loss` | `max{0,1-L_ir}` | EXACT_MATCH |
| Transport cost | `Σ c_irj q_irj` | same | EXACT_MATCH |
| Shortage cost | `Σ p_rj u_rj` | same | EXACT_MATCH |
| Service penalty | `Σ π_j e_j` | same | EXACT_MATCH |
| Economic recourse | sum of the preceding three components | `Q(x,ξ)` | EXACT_MATCH |
| Reporting tie-break | second LP minimizes total shortage subject to cost-optimal tolerance | excluded from economic `Q` | EXACT_MATCH |
| Robust epigraph | one `theta >= scenario_cost` per realization | `θ≥Q-realization cost` finite form | EXACT_MATCH |
| REOPTIMIZE objective | base first stage + friction + theta | `C_first+R+max Q` | EXACT_MATCH |
| KEEP first stage | `Σ f_i y^0_i + Σ h_ijx^0_ij`; friction zero | `C_inc`; friction zero | EXACT_MATCH |

## Stage-1 finite-special-case audit

The executable audit uses the same six MAIN events with `Γ=2` and disabled
type-specific caps. It compares Stage-2 output with both the Stage-1 generator
and committed `scenario_catalog.csv`.

| Check | Required | Result |
|---|---:|---:|
| Active-event combinations | 22 | 22/22 exact |
| Demand multipliers | 22 | 22/22 exact |
| Route availabilities | 22 | 22/22 exact |
| Stage-1 artifacts modified | no | no |
| Optimizer dispatches | 0 | 0 |

## Known approximations and unresolved issues

- The effective route fraction is a calibrated construct, not physical route
  capacity. This is a modeling assumption, not an implementation mismatch.
- Stage-1 stress magnitudes are not a formal calibration. Stage 3 must supply a
  defensible calibration method.
- The finite-scenario epigraph is exact for enumerated small sets; Stage 2 does
  not provide a scalable adversarial algorithm.
- In future separation, `a_ir(ξ)d_rj(ξ)` introduces piecewise/event-interaction
  structure. It is deliberately unresolved.
- Optional type budgets are formally supported and solver-free audited, but no
  optimization experiment uses them in Stage 2.
- Paper-2 PRB is not claimed to remain valid outside compatible demand-only
  special cases.

No code-to-math inconsistency was found after the explicit decision to retain
the demand-coverage inequality and aggregate product service formulation.
