# Stage 5B-2 risk-corpus pipeline status

This document records the deterministic screening, demand-risk GKG evidence, and non-destructive corpus views produced after Stage 5B-1/1R. These are data-engineering and evidence-audit outputs, not final causal or optimization results.

## Demand-risk GKG matcher freeze

GKG 2.1 uses zero-based field index 3 for `SourceCommonName` and index 4 for `DocumentIdentifier`. The frozen matcher therefore reads document URLs from index 4. Reliable matching follows this order:

1. exact URL;
2. normalized URL;
3. canonical domain and path.

Title similarity is review-only and cannot establish a reliable match.

The six audited demand-risk target records were searched across every 15-minute GKG update on their five target dates. All six had exact URL matches on date offset 0. The acquisition retained 480 raw ZIP archives. No adjacent-date expansion was needed.

The selected GKG evidence changed the six post-GKG assessments to five `FALSE_POSITIVE` records and one `PLAUSIBLE_BUT_INSUFFICIENT` record. No target became confirmed flash-demand evidence solely because its URL was present in GKG.

## Unified corpus views

The unified-corpus layer indexes eight standardized source/record-type tables:

- GDELT Event records;
- USGS events;
- GDACS events;
- DesInventar events;
- Copernicus EMS events;
- ReliefWeb disasters;
- ReliefWeb reports as document evidence;
- selected GKG rows as document evidence.

The layer contains 2,794,826 records. It uses a logical `REFERENCE_ONLY` directory map: no source or processed file is moved, renamed, copied, or deleted. Source SHA-256 values are checked before and after processing.

Geographic harmonization preserves source geography while normalizing deterministic Ecuador, Colombia, and Peru codes to `ECU`, `COL`, and `PER`. It produces:

- an Ecuador view with 484,884 records;
- an Andean view (`ECU`, `COL`, or `PER`) with 2,793,821 records.

No placebo subset is generated until its geography and exclusion rule are explicitly preregistered. Non-Andean records are not silently relabeled as placebo.

## Boundaries

This stage does not perform GenAI classification, cross-source event linkage, canonical-event construction, severity calibration, scenario generation, optimization, Gurobi, CCG, or Paper-2 modification.
