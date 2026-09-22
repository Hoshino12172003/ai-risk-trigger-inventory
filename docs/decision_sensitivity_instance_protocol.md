# Decision-Sensitivity Feasibility Instance Protocol

## Status and boundary

This is an exploratory feasibility pilot, not a formal experiment. All code,
data contracts, derived features, and future learning work live in
`Hoshino12172003/ai-risk-trigger-inventory`. The repository
`Hoshino12172003/budget-inventory-benders` is a read-only optimization oracle,
frozen at commit `51aebd06edf8f5d6d124d0f3eebdbb901e63274f`.

The pilot does not modify the robust model or PRB-Benders. Favorita stores are
treated as demand regions; they are not described as real warehouses.

## Research questions

The pilot tests, without presuming any answer:

1. Whether demand-shift magnitude is sufficient to explain reconfiguration value.
2. Whether similar shifts can have different decision impacts under different
   network or incumbent-inventory states.
3. Whether inventory slack, capacity slack, transport substitutability, demand
   concentration, promotions, and events add explanatory value.

If demand shift already explains most decision value and structural variables
add little, the result is `WEAK_OR_NO_STRUCTURAL_SIGNAL`.

## Local input contract

No Kaggle download is performed by this repository. Inputs are supplied through
`--data-dir` or explicit local paths. The expected cleaned products are:

- `favorita_store_family_day.csv.gz`: primary construction input. Required
  columns are `date`, `state`, `store_nbr`, `family`, `sales`, `onpromotion`,
  `holiday_flag`, and `transactions`.
- `favorita_state_family_week_states.csv.gz`: optional upstream audit product;
  it is not used to replace store-level construction.
- `state_network_contexts.csv`: required context input with one or more rows per
  state and columns `state`, `network_id`, `transport_cost_cv`, and
  `transport_substitutability_proxy`.
- `oracle_pilot_candidates.csv`: optional upstream candidate audit product. It
  is not treated as an oracle result.

Only Pichincha and Guayas are admitted. The family priority is `GROCERY I`,
`BEVERAGES`, `PRODUCE`, `CLEANING`, `DAIRY`, and `BREAD/BAKERY`; each first-round
instance uses the first three available priority families, never more than three.

## Parameter provenance

### OBSERVED

- calendar date and weekly sales
- state, store identifier, and product family
- promotion counts
- holiday/event indicator
- transactions

No observed inventory is claimed.

### DERIVED

- Monday `week_start`
- current weekly demand
- four-prior-week mean baseline demand
- absolute, relative, positive, and negative demand shift
- promotion intensity and four-week-relative transactions change
- demand concentration and incumbent-inventory concentration
- inventory-to-demand and capacity-slack ratios

A store-family-week is eligible only when all four prior weeks exist and its
baseline is at least `1.0` unit. Smaller denominators are filtered; they are
never divided into relative shift.

### CALIBRATED

- nominal incumbent inventory: `x0 = 1.10 * four-week baseline demand`
- capacity: the larger of `x0` and `1.20 * trailing-eight-week peak demand`
- transport cost variation and substitutability proxies supplied by the frozen
  local network-context construction

Every row labels incumbent inventory as `CALIBRATED`, not observed. These
calibrations are exploratory and cannot be reinterpreted as empirical inventory.

## Deterministic small networks

For each state, stores are ranked by total historical demand over the six
priority families. Five stores are selected at evenly spaced ranks from low to
high demand; if only four are available, all four are used. For additional
`network_id` rows, the same demand-rank pattern is deterministically rotated.
Ties break by store identifier. This yields multiple deterministic 4--6-store
demand-region networks without random selection.

## State construction and dry-run gate

Candidates must contain every selected store-family cell and valid lagged
baselines. Selection covers, where available, normal, promotion-heavy,
holiday/event, high-positive-shift, low-positive-shift, and moderate-shift
weeks, then fills deterministically across time and both states.

The first gate writes exactly five states and invokes no solver:

```bash
python experiments/decision_sensitivity_pilot/build_instances.py \
  --data-dir /local/path/to/cleaned/favorita --limit 5
python experiments/decision_sensitivity_pilot/run_pilot.py \
  artifacts/decision_sensitivity_pilot/decision_states.csv --dry-run
```

The gate checks mapping, baselines, store/family selection, calibrated `x0`,
scaling, and absence of post-solve leakage. Only after a five-state PASS may a
20-state batch be constructed. Expansion beyond 20, up to 50, requires all 20
oracle states to complete stably.

## Frozen oracle contract

Each state eventually requires two evaluations:

- KEEP: evaluate calibrated incumbent `x0` under current demand, producing
  `C_keep`.
- REOPTIMIZE: use the frozen robust reconfiguration oracle to produce `x_star`
  and `C_reopt`.

`decision_value = C_keep - C_reopt`. `C_reopt` must already contain the Paper 2
reconfiguration friction; the adapter must not add it again. Required audit
outputs are inventory-change L1, changed inventory pairs, shortage, transport,
service-violation, and total costs for KEEP and REOPTIMIZE.

The frozen commit currently exposes no verified decision-sensitivity dispatch
interface, so `BlockedOracleAdapter` makes zero calls and raises `BLOCKED`.
No oracle result may be synthesized to bypass this boundary.

## Analysis and preregistered thresholds

Models A and B regress decision value on relative and positive demand shift,
respectively. Model C uses relative demand shift plus the eight registered
structural/event features. Each records R2, adjusted R2, and MAE; Pearson and
Spearman correlations are also recorded.

Matched pairs require absolute relative-shift difference at most `0.05` and
either a decision-value ratio of at least `2.0` or an absolute gap of at least
`0.50 * median positive decision value`.

`STRONG_STRUCTURAL_SIGNAL` requires at least 20 executed states, best demand-only
R2 at most `0.70`, adjusted-R2 gain at least `0.15`, at least three matched
pairs, both states, and at least two family sets. `PARTIAL_STRUCTURAL_SIGNAL`
requires adjusted-R2 gain at least `0.05` or at least one matched pair but does
not meet all strong conditions. `WEAK_OR_NO_STRUCTURAL_SIGNAL` applies when
demand-only R2 is at least `0.80`, structural gain is below `0.05`, and there
are no matched pairs, or when neither partial nor strong evidence exists.

These thresholds are implemented in
`src/ai_risk_trigger_inventory/evaluation/decision_sensitivity.py` and must not
be changed after inspecting pilot results.
