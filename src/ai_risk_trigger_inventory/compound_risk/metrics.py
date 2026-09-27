"""Frozen Stage-1 gate and worst-scenario calculations."""

from __future__ import annotations

from typing import Any

from .extensive_form import PolicyResult, ScenarioRecourse


RELATIVE_TIE_TOLERANCE = 1e-6
GATE_THRESHOLDS = {
    "inventory_normalized_l1": 0.05,
    "interaction_relative_increment": 0.05,
    "relative_decision_value": 0.01,
    "shortage_relative_increase": 0.10,
    "fill_rate_drop": 0.02,
}


def tied_worst(rows: tuple[ScenarioRecourse, ...]) -> list[ScenarioRecourse]:
    maximum = max(row.total_recourse_cost for row in rows)
    tolerance = RELATIVE_TIE_TOLERANCE * max(1.0, abs(maximum))
    return sorted(
        [row for row in rows if maximum - row.total_recourse_cost <= tolerance],
        key=lambda row: row.scenario_id,
    )


def top_k_with_ties(rows: tuple[ScenarioRecourse, ...], k: int = 3) -> list[ScenarioRecourse]:
    ordered = sorted(rows, key=lambda row: (-row.total_recourse_cost, row.scenario_id))
    cutoff = ordered[min(k, len(ordered)) - 1].total_recourse_cost
    tolerance = RELATIVE_TIE_TOLERANCE * max(1.0, abs(cutoff))
    return sorted(
        [row for row in rows if row.total_recourse_cost >= cutoff - tolerance],
        key=lambda row: row.scenario_id,
    )


def inventory_l1(left: list[list[float]], right: list[list[float]]) -> float:
    return sum(
        abs(left[i][j] - right[i][j])
        for i in range(len(left)) for j in range(len(left[i]))
    )


def evaluate_gates(
    demand_keep: PolicyResult,
    demand_reopt: PolicyResult,
    full_keep: PolicyResult,
    full_reopt: PolicyResult,
) -> dict[str, Any]:
    denominator = max(sum(map(sum, demand_reopt.x)), 1e-12)
    normalized = inventory_l1(full_reopt.x, demand_reopt.x) / denominator
    gate1 = normalized >= GATE_THRESHOLDS["inventory_normalized_l1"]

    full_by_events = {row.active_events: row for row in full_reopt.scenario_results}
    interaction_candidates = []
    for row in tied_worst(full_reopt.scenario_results):
        if len(row.active_events) != 2 or len(row.event_types) < 2:
            continue
        singles = [full_by_events[(event,)] for event in row.active_events]
        singleton_worst = max(item.total_recourse_cost for item in singles)
        increment = row.total_recourse_cost - singleton_worst
        relative = increment / max(abs(singleton_worst), 1e-12)
        interaction_candidates.append((relative, increment, row, singleton_worst))
    best_interaction = max(interaction_candidates, default=None, key=lambda value: value[0])
    gate2 = bool(
        best_interaction
        and best_interaction[0] >= GATE_THRESHOLDS["interaction_relative_increment"]
    )

    decision_value = full_keep.total_objective - full_reopt.total_objective
    relative_value = decision_value / max(abs(full_keep.total_objective), 1e-12)
    gate3 = decision_value > 0 and relative_value >= GATE_THRESHOLDS["relative_decision_value"]

    keep_worst = {row.active_events for row in tied_worst(full_keep.scenario_results)}
    reopt_worst = {row.active_events for row in tied_worst(full_reopt.scenario_results)}
    keep_top = {(row.active_events, row.event_types) for row in top_k_with_ties(full_keep.scenario_results)}
    reopt_top = {(row.active_events, row.event_types) for row in top_k_with_ties(full_reopt.scenario_results)}
    gate4 = keep_worst != reopt_worst or keep_top != reopt_top

    demand_shortage = max(row.total_shortage for row in tied_worst(demand_keep.scenario_results))
    full_shortage = max(row.total_shortage for row in tied_worst(full_keep.scenario_results))
    shortage_increase = (full_shortage - demand_shortage) / max(abs(demand_shortage), 1e-12)
    demand_fill = min(row.minimum_fill_rate for row in tied_worst(demand_keep.scenario_results))
    full_fill = min(row.minimum_fill_rate for row in tied_worst(full_keep.scenario_results))
    fill_drop = demand_fill - full_fill
    gate5 = (
        shortage_increase >= GATE_THRESHOLDS["shortage_relative_increase"]
        or fill_drop >= GATE_THRESHOLDS["fill_rate_drop"]
    )

    gates = {
        "gate_1_inventory_configuration_change": {
            "pass": gate1, "normalized_l1": normalized,
            "threshold": GATE_THRESHOLDS["inventory_normalized_l1"],
        },
        "gate_2_cross_risk_interaction": {
            "pass": gate2,
            "worst_pair": None if best_interaction is None else list(best_interaction[2].active_events),
            "relative_increment": None if best_interaction is None else best_interaction[0],
            "incremental_interaction_amount": None if best_interaction is None else best_interaction[1],
            "worse_singleton_cost": None if best_interaction is None else best_interaction[3],
            "threshold": GATE_THRESHOLDS["interaction_relative_increment"],
        },
        "gate_3_reconfiguration_value": {
            "pass": gate3, "decision_value": decision_value,
            "relative_decision_value": relative_value,
            "threshold": GATE_THRESHOLDS["relative_decision_value"],
        },
        "gate_4_critical_scenario_adaptation": {
            "pass": gate4,
            "keep_tied_worst": [list(value) for value in sorted(keep_worst)],
            "reopt_tied_worst": [list(value) for value in sorted(reopt_worst)],
            "keep_top3_tie_expanded": [list(row.active_events) for row in top_k_with_ties(full_keep.scenario_results)],
            "reopt_top3_tie_expanded": [list(row.active_events) for row in top_k_with_ties(full_reopt.scenario_results)],
        },
        "gate_5_service_shortage_impact": {
            "pass": gate5, "demand_only_worst_shortage": demand_shortage,
            "full_compound_worst_shortage": full_shortage,
            "shortage_relative_increase": shortage_increase,
            "minimum_fill_rate_drop": fill_drop,
            "shortage_threshold": GATE_THRESHOLDS["shortage_relative_increase"],
            "fill_rate_threshold": GATE_THRESHOLDS["fill_rate_drop"],
        },
    }
    passed = sum(value["pass"] for value in gates.values())
    classification = (
        "COMPOUND_RISK_FEASIBILITY_GO" if passed >= 4
        else "COMPOUND_RISK_FEASIBILITY_PARTIAL" if passed >= 2
        else "COMPOUND_RISK_FEASIBILITY_WEAK"
    )
    return {"gates": gates, "pass_count": passed, "classification": classification}
