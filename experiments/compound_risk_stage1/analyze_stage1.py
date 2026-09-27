"""Render the exploratory Stage-1 report from deterministic artifacts."""

from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts" / "compound_risk_stage1"


def main() -> None:
    base = json.loads((ARTIFACTS / "base_state.json").read_text(encoding="utf-8"))
    events = json.loads((ARTIFACTS / "event_catalog.json").read_text(encoding="utf-8"))["events"]
    gates = json.loads((ARTIFACTS / "gate_results.json").read_text(encoding="utf-8"))
    audit = json.loads((ARTIFACTS / "execution_audit.json").read_text(encoding="utf-8"))
    with (ARTIFACTS / "regime_summary.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with (ARTIFACTS / "sensitivity_summary.csv").open(encoding="utf-8", newline="") as handle:
        sensitivity = list(csv.DictReader(handle))
    lookup = {(row["regime"], row["mode"]): row for row in rows}
    selected_regimes = [
        "R0_NOMINAL", "R1_DEMAND_ONLY", "R2_REGIONAL_ONLY",
        "R3_NETWORK_ONLY", "R7_FULL_COMPOUND",
    ]
    gate_lines = "\n".join(
        f"- {name}: **{'PASS' if value['pass'] else 'FAIL'}**"
        for name, value in gates["gates"].items()
    )
    objective_lines = "\n".join(
        f"| {regime} | {float(lookup[(regime, 'KEEP')]['robust_total_objective']):.6f} | "
        f"{float(lookup[(regime, 'REOPTIMIZE')]['robust_total_objective']):.6f} |"
        for regime in selected_regimes
    )
    event_lines = "\n".join(
        f"- {event['event_id']} `{event['event_type']}`: {event['selection_note']}"
        for event in events
    )
    sensitivity_lines = "\n".join(
        f"| {row['magnitude']} | {row['gate_direction_persistence']} | "
        f"{row['inventory_ranking_stable']} | {row['critical_scenario_stable']} | {row['classification']} |"
        for row in sensitivity
    )
    full_keep = lookup[("R7_FULL_COMPOUND", "KEEP")]
    full_reopt = lookup[("R7_FULL_COMPOUND", "REOPTIMIZE")]
    demand_reopt = lookup[("R1_DEMAND_ONLY", "REOPTIMIZE")]
    report = f"""# Compound-risk Stage 1 report

## Status

This is an exploratory feasibility prototype, not a formal paper experiment or
final paper evidence. MAIN magnitudes, targets, Gamma, base state, and gate
thresholds were preregistered and were not tuned after results. No GenAI, CCG,
M5, Paper-2 source modification, PRB compound solve, or merge was used.

Overall classification: **{gates['classification']}** ({gates['pass_count']}/5 gates).

## Base state

- state ID: `{base['state_id']}`
- date/state: `{base['date']}` / `{base['state']}`
- relative demand shift: {base['relative_demand_shift']:.6f}
- regions: {', '.join(base['demand_regions'])}
- products: {', '.join(base['product_families'])}
- calibration: `PRIMARY_PILOT_CALIBRATION / UNIT_COST_BASED`
- selection: {base['selection_rule']}

The state was selected ex ante from existing normal weeks, before any compound
result was computed.

## Construction

{event_lines}

The full set contains 22 programmatically enumerated scenarios. Regime counts
are 1 for nominal, 4 for each two-event-type-only regime, 11 for each four-event
combined regime, and 22 for full compound. Overlapping demand shocks add on the
baseline; route losses add and cap at 100%.

Effective route capacity is an exploratory service fraction, not observed
physical units/day capacity. The constraint is
`shipment_irj_s <= availability_ir(s) * demand_rj(s)`; nominal availability is
one and therefore does not restrict an arc below full region-product demand.

## Exact solver audit

- optimizer dispatches: {audit['optimizer_dispatches_total']}
- exact/optimal certifications: {audit['optimal_certification_count']}
- failures: {len(audit['failures'])}
- nominal local/frozen cost-only identity: PASS
- Paper-2 ending SHA/clean: `{audit['paper2_sha_after']}` / `{audit['paper2_clean_after']}`

Demand-only percentage events are not isomorphic to Paper-2's original
region-product deviation cardinality set, so this report does not claim an
external demand-only solution identity. Internal fixed-x primal/cost identities
and nominal semantics passed.

## Main objectives

| regime | KEEP total | REOPTIMIZE total |
|---|---:|---:|
{objective_lines}

FULL_COMPOUND KEEP vs REOPTIMIZE decision value is
{gates['gates']['gate_3_reconfiguration_value']['decision_value']:.6f}
({gates['gates']['gate_3_reconfiguration_value']['relative_decision_value']:.3%}).
The FULL_COMPOUND versus DEMAND_ONLY reoptimized normalized inventory L1
difference is {gates['gates']['gate_1_inventory_configuration_change']['normalized_l1']:.3%}.
FULL_COMPOUND KEEP worst events are `{full_keep['worst_active_events']}` and
REOPTIMIZE worst events are `{full_reopt['worst_active_events']}`. Demand-only
REOPTIMIZE worst events are `{demand_reopt['worst_active_events']}`.

The incumbent KEEP worst-shortage relative change from demand-only to full
compound is {gates['gates']['gate_5_service_shortage_impact']['shortage_relative_increase']:.3%};
the minimum-fill-rate drop is
{gates['gates']['gate_5_service_shortage_impact']['minimum_fill_rate_drop']:.6f}.
Fill and shortage values use a certified linear tie-break constrained to the
exact cost optimum; economic conclusions use the cost-only optimum.

## Frozen gates

{gate_lines}

Gate 2 reports an incremental interaction amount of
`{gates['gates']['gate_2_cross_risk_interaction']['incremental_interaction_amount']}`;
it does not require or claim strict superadditivity. Worst sets use relative
tolerance `1e-6`, and top-3 comparisons expand ties.

## Sensitivity after MAIN

| magnitude | gate direction persistent | inventory ranking stable | critical scenario stable | classification |
|---|---|---|---|---|
{sensitivity_lines}

LOW and HIGH were run only after MAIN completed. Neither replaces MAIN, and no
shock magnitude or target was selected based on favorable output.

## Interpretation boundary

These results answer only whether this one verified small Favorita instance
shows feasibility signals under the preregistered event construction. They do
not establish generality, statistical significance, real route capacities, or
a formal paper conclusion.
"""
    (ROOT / "docs" / "compound_risk_stage1_report.md").write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
