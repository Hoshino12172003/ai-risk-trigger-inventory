# Decision-Sensitivity Feasibility Report

Status: `DRY_RUN_PASS`

This is an exploratory feasibility setup, not a formal experiment. The four
specified cleaned Favorita products were audited as local, gitignored inputs.
Static inspection found reusable frozen Paper 2 Python callables, but neither
callable was executed.

- Local input audit: `PASS`
- Frozen oracle interface audit: `PASS`
- Five-state construction: `PASS`
- Solver calls: 0
- Executed states: 0
- Empirical classification: not assigned; optimization is not authorized

`decision_states.csv` contains five solver-free pre-solve states.
`oracle_results.csv` remains header-only and contains no oracle outputs.

## Required questions

1. Demand magnitude alone: not estimable without real oracle results.
2. Improvement from structural features: not estimable.
3. Small-shift/high-value states: not tested.
4. Large-shift/low-value states: not tested.
5. Candidate explanatory variables are preregistered, but none is ranked yet.
6. Cross-state evidence for Pichincha and Guayas: not tested.
7. Whether to proceed to a formal study: no decision is supported by this
   blocked feasibility setup.

No `STRONG_STRUCTURAL_SIGNAL`, `PARTIAL_STRUCTURAL_SIGNAL`, or
`WEAK_OR_NO_STRUCTURAL_SIGNAL` label is assigned because doing so with zero
executed states would fabricate evidence.
