"""Solver-free proof that Stage 1 is the six-event Stage-2 special case."""

from __future__ import annotations

import csv
from hashlib import sha256
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_risk_trigger_inventory.compound_risk.events import build_event_catalog
from ai_risk_trigger_inventory.compound_risk.model_spec import EventImpactParameters
from ai_risk_trigger_inventory.compound_risk.scenarios import enumerate_scenarios
from ai_risk_trigger_inventory.compound_risk.uncertainty_set import EventBudgetSpec, scenario_count


STAGE1_COMMIT = "bcc1ec5a84c2fa756911bff3282443897603f8d2"
PAPER2_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main() -> None:
    stage1_dir = ROOT / "artifacts" / "compound_risk_stage1"
    stage1_hashes_before = {
        path.name: _digest(path) for path in sorted(stage1_dir.iterdir()) if path.is_file()
    }
    base = json.loads((stage1_dir / "base_state.json").read_text(encoding="utf-8"))
    instance = base["instance"]
    events = build_event_catalog(instance, "MAIN")
    stage1 = enumerate_scenarios(instance, events, gamma=2)
    impacts = EventImpactParameters.from_stage1(instance, events)
    budget = EventBudgetSpec(gamma=2)
    vectors = budget.enumerate(impacts.event_type_map)

    with (stage1_dir / "scenario_catalog.csv").open(encoding="utf-8", newline="") as handle:
        artifact_rows = list(csv.DictReader(handle))
    artifact_by_events = {
        tuple(filter(None, row["active_events"].split("|"))): row
        for row in artifact_rows
    }
    stage1_by_events = {scenario.active_events: scenario for scenario in stage1}
    stage2_by_events = {
        vector.active_events: {
            "demand_multiplier": impacts.demand_multiplier(vector),
            "route_availability": impacts.route_availability(vector),
        }
        for vector in vectors
    }
    keys_match = set(stage1_by_events) == set(stage2_by_events) == set(artifact_by_events)
    demand_matches = []
    route_matches = []
    for active in sorted(stage1_by_events, key=lambda value: (len(value), value)):
        stage1_scenario = stage1_by_events[active]
        stage2_scenario = stage2_by_events[active]
        artifact = artifact_by_events[active]
        demand_matches.append(
            stage1_scenario.demand_multiplier == stage2_scenario["demand_multiplier"]
            and [list(row) for row in stage1_scenario.demand_multiplier]
            == json.loads(artifact["demand_multiplier_matrix"])
        )
        route_matches.append(
            stage1_scenario.route_availability == stage2_scenario["route_availability"]
            and [list(row) for row in stage1_scenario.route_availability]
            == json.loads(artifact["route_availability_matrix"])
        )
    stage1_hashes_after = {
        path.name: _digest(path) for path in sorted(stage1_dir.iterdir()) if path.is_file()
    }
    toy_types = {
        "D1": "FLASH_DEMAND_SURGE", "D2": "FLASH_DEMAND_SURGE",
        "R1": "REGIONAL_EMERGENCY_DISRUPTION", "R2": "REGIONAL_EMERGENCY_DISRUPTION",
        "N1": "TRANSPORT_NETWORK_DISRUPTION", "N2": "TRANSPORT_NETWORK_DISRUPTION",
    }
    toy_capped = EventBudgetSpec(3, gamma_d=1, gamma_r=1, gamma_n=1)
    toy_vectors = toy_capped.enumerate(toy_types)
    toy_disabled = EventBudgetSpec(3)
    audit = {
        "status": "PASS" if keys_match and all(demand_matches) and all(route_matches) else "FAIL",
        "stage1_commit": STAGE1_COMMIT,
        "paper2_frozen_sha": PAPER2_SHA,
        "optimizer_dispatches": 0,
        "event_count": len(events),
        "gamma": 2,
        "type_specific_budgets": "DISABLED",
        "closed_form_count": scenario_count(6, 2),
        "stage1_count": len(stage1),
        "stage2_count": len(vectors),
        "artifact_count": len(artifact_rows),
        "active_event_sets_matched": len(stage1_by_events) if keys_match else 0,
        "active_event_sets_exact_match": keys_match,
        "demand_multiplier_matches": sum(demand_matches),
        "demand_multiplier_exact_match": all(demand_matches),
        "route_availability_matches": sum(route_matches),
        "route_availability_exact_match": all(route_matches),
        "event_type_partition": {
            event_type: sum(event.event_type == event_type for event in events)
            for event_type in sorted(set(event.event_type for event in events))
        },
        "combinatorial_count_checks": {
            "n6_gamma2": scenario_count(6, 2),
            "n100_gamma2": scenario_count(100, 2),
            "n100_gamma3": scenario_count(100, 3),
            "n100_gamma4": scenario_count(100, 4),
        },
        "type_budget_toy_audit": {
            "event_type_sizes": {"D": 2, "R": 2, "N": 2},
            "gamma": 3, "gamma_d": 1, "gamma_r": 1, "gamma_n": 1,
            "analytical_count": toy_capped.count(toy_types),
            "enumerated_count": len(toy_vectors),
            "all_vectors_admissible": all(
                toy_capped.is_admissible(toy_types, vector) for vector in toy_vectors
            ),
            "disabled_caps_count": toy_disabled.count(toy_types),
            "ordinary_cardinality_count": scenario_count(6, 3),
            "disabled_caps_recover_ordinary_set": (
                toy_disabled.count(toy_types) == scenario_count(6, 3)
            ),
        },
        "stage1_artifact_hashes_before": stage1_hashes_before,
        "stage1_artifact_hashes_after": stage1_hashes_after,
        "stage1_artifacts_unchanged": stage1_hashes_before == stage1_hashes_after,
    }
    if audit["status"] != "PASS" or not audit["stage1_artifacts_unchanged"]:
        raise SystemExit("STOP: Stage-1/Stage-2 equivalence audit failed")
    output = ROOT / "artifacts" / "compound_risk_stage2"
    output.mkdir(parents=True, exist_ok=True)
    (output / "stage1_equivalence_audit.json").write_text(
        json.dumps(audit, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
