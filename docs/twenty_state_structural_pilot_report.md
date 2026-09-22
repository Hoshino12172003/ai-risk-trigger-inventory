# Twenty-State Structural Pilot Report

Status: exploratory pilot; not a formal paper experiment.

- MAIN calibration: `UNIT_COST_BASED` (`PRIMARY_PILOT_CALIBRATION`)
- Sensitivity calibration: `TRANSPORT_BASED`
- MAIN classification: `PARTIAL_STRUCTURAL_SIGNAL`
- MAIN matched pairs: 21
- Calibration decision-value rank Spearman: 1.000000
- Persistent matched pairs: 21
- Top-five overlap: 5/5
- Bottom-five overlap: 5/5
- Small-shift / large-consequence states: ['pichincha-pichincha-context-1-20161205']
- Large-shift / small-consequence states: none

| model | R2 | adjusted R2 | MAE | Pearson | Spearman |
|---|---:|---:|---:|---:|---:|
| A relative shift | 0.208450 | 0.164475 | 442530.208003 | 0.456563 | 0.584962 |
| B positive shift | 0.575735 | 0.552165 | 245044.681891 | n/a | n/a |
| C demand + structure | 0.793879 | 0.608370 | 210674.793219 | n/a | n/a |

Best demand-only R2 is 0.575735; the structural adjusted-R2 gain
is 0.056205. Model C's design matrix rank is
10 and its condition number is
486.666686.

The classification uses only the 20 MAIN rows and the unchanged preregistered
thresholds. Sensitivity rows are used only for ranking, top/bottom stability,
and matched-pair persistence. `UNIT_COST_BASED` remains a pilot calibration,
not a final paper parameter. Favorita inventory nodes, inventory, capacity,
transport, shortage, and reconfiguration quantities remain calibrated rather
than observed.
