from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from ai_risk_trigger_inventory.compound_risk.events import EVENT_TYPES, build_event_catalog
from ai_risk_trigger_inventory.compound_risk.model_spec import EventImpactParameters
from ai_risk_trigger_inventory.compound_risk.scenarios import enumerate_scenarios
from ai_risk_trigger_inventory.compound_risk.uncertainty_set import (
    EventBudgetSpec,
    EventVector,
    scenario_count,
)


ROOT = Path(__file__).resolve().parents[1]
STAGE1_COMMIT = "bcc1ec5a84c2fa756911bff3282443897603f8d2"
PAPER2 = Path(
    r"C:\Users\Hu Jiaxin\Documents\Codex\2026-09-20\files-pasted-by-the-user-github\work\budget-inventory-benders-frozen-51aebd0"
)


@pytest.fixture(scope="module")
def base() -> dict:
    return json.loads(
        (ROOT / "artifacts" / "compound_risk_stage1" / "base_state.json").read_text(encoding="utf-8")
    )


@pytest.fixture(scope="module")
def events(base):
    return build_event_catalog(base["instance"], "MAIN")


@pytest.fixture(scope="module")
def impacts(base, events):
    return EventImpactParameters.from_stage1(base["instance"], events)


def test_closed_form_scenario_counts_without_enumeration() -> None:
    assert scenario_count(6, 2) == 22
    assert scenario_count(100, 2) == 5051
    assert scenario_count(100, 3) == 166751
    assert scenario_count(100, 4) == 4087976


def test_invalid_gamma_is_rejected() -> None:
    with pytest.raises(ValueError):
        scenario_count(6, -1)
    with pytest.raises(ValueError):
        scenario_count(6, 7)
    with pytest.raises(ValueError):
        EventBudgetSpec(-1)
    with pytest.raises(ValueError):
        EventBudgetSpec(7).count({f"E{i}": EVENT_TYPES[i % 3] for i in range(6)})


def test_category_budgets_are_enforced() -> None:
    types = {
        "D1": EVENT_TYPES[0], "D2": EVENT_TYPES[0],
        "R1": EVENT_TYPES[1], "R2": EVENT_TYPES[1],
        "N1": EVENT_TYPES[2], "N2": EVENT_TYPES[2],
    }
    spec = EventBudgetSpec(3, gamma_d=1, gamma_r=1, gamma_n=1)
    vectors = spec.enumerate(types)
    assert spec.count(types) == len(vectors) == 27
    for vector in vectors:
        active_types = [types[event_id] for event_id in vector.active_events]
        assert len(vector.active_events) <= 3
        assert all(active_types.count(event_type) <= 1 for event_type in EVENT_TYPES)


def test_disabling_category_budgets_recovers_cardinality_set() -> None:
    types = {
        "D1": EVENT_TYPES[0], "D2": EVENT_TYPES[0],
        "R1": EVENT_TYPES[1], "R2": EVENT_TYPES[1],
        "N1": EVENT_TYPES[2], "N2": EVENT_TYPES[2],
    }
    spec = EventBudgetSpec(3)
    assert spec.count(types) == scenario_count(6, 3) == 42
    assert len(spec.enumerate(types)) == 42


def test_xi_must_be_binary(impacts) -> None:
    with pytest.raises(ValueError):
        EventVector(impacts.event_ids, (0, 0, 0, 0, 0, 2))
    with pytest.raises(ValueError):
        EventBudgetSpec(2).is_admissible(
            impacts.event_type_map, [0, 0, 0, 0, 0, 0.5]
        )


def test_impact_dimensions_and_route_ranges_are_checked(impacts) -> None:
    with pytest.raises(ValueError, match="demand impact dimension"):
        replace(impacts, demand_impacts=impacts.demand_impacts[:-1])
    route = [[list(row) for row in matrix] for matrix in impacts.route_loss_impacts]
    route[0][0][0] = 1.01
    with pytest.raises(ValueError, match="route-loss"):
        replace(
            impacts,
            route_loss_impacts=tuple(
                tuple(tuple(row) for row in matrix) for matrix in route
            ),
        )


def test_additive_demand_rule_matches_stage1(impacts, base, events) -> None:
    stage1 = enumerate_scenarios(base["instance"], events, 2)
    scenario = next(row for row in stage1 if row.active_events == ("E2", "E3"))
    xi = {event_id: int(event_id in scenario.active_events) for event_id in impacts.event_ids}
    assert impacts.demand_multiplier(xi) == scenario.demand_multiplier
    e2 = impacts.event_ids.index("E2")
    e3 = impacts.event_ids.index("E3")
    overlap = next(
        (r, j) for r in range(len(impacts.region_ids)) for j in range(len(impacts.product_ids))
        if impacts.demand_impacts[e2][r][j] and impacts.demand_impacts[e3][r][j]
    )
    r, j = overlap
    assert scenario.demand_multiplier[r][j] == pytest.approx(
        1 + impacts.demand_impacts[e2][r][j] + impacts.demand_impacts[e3][r][j]
    )


def test_capped_route_loss_rule(impacts) -> None:
    route = [[list(row) for row in matrix] for matrix in impacts.route_loss_impacts]
    route[0][0][0] = 0.75
    route[1][0][0] = 0.75
    capped = replace(
        impacts,
        route_loss_impacts=tuple(tuple(tuple(row) for row in matrix) for matrix in route),
    )
    assert capped.route_availability([1, 1, 0, 0, 0, 0])[0][0] == 0.0


def test_stage2_exactly_recovers_stage1_realizations(base, events, impacts) -> None:
    stage1 = {row.active_events: row for row in enumerate_scenarios(base["instance"], events, 2)}
    stage2 = EventBudgetSpec(2).enumerate(impacts.event_type_map)
    assert len(stage1) == len(stage2) == 22
    assert set(stage1) == {vector.active_events for vector in stage2}
    for vector in stage2:
        assert impacts.demand_multiplier(vector) == stage1[vector.active_events].demand_multiplier
        assert impacts.route_availability(vector) == stage1[vector.active_events].route_availability


def test_event_type_partition_and_permitted_impact_patterns(events, impacts) -> None:
    assert set(impacts.event_types) == set(EVENT_TYPES)
    assert len(impacts.event_type_map) == len(events) == 6
    for index, event_type in enumerate(impacts.event_types):
        has_demand = any(value > 0 for row in impacts.demand_impacts[index] for value in row)
        has_route = any(value > 0 for row in impacts.route_loss_impacts[index] for value in row)
        if event_type == "FLASH_DEMAND_SURGE":
            assert has_demand and not has_route
        elif event_type == "REGIONAL_EMERGENCY_DISRUPTION":
            assert has_demand and has_route
        else:
            assert not has_demand and has_route


def test_required_demand_and_service_audits_are_exact_matches() -> None:
    audit = (ROOT / "docs" / "compound_risk_model_audit.md").read_text(encoding="utf-8")
    assert "Demand-coverage audit (required item A)" in audit
    assert "Service-aggregation audit (required item B)" in audit
    assert "implemented inequality" in audit
    assert "one `e[j]`" in audit
    assert audit.count("EXACT_MATCH") >= 20


def test_paper2_frozen_checkout_is_untouched() -> None:
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PAPER2, text=True).strip() == "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
    assert subprocess.check_output(["git", "status", "--porcelain"], cwd=PAPER2, text=True).strip() == ""


def test_stage1_artifacts_are_unchanged() -> None:
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", STAGE1_COMMIT, "--", "artifacts/compound_risk_stage1"],
        cwd=ROOT, text=True,
    ).splitlines()
    assert changed == []


def test_no_solver_genai_or_m5_dependency_in_stage2_modules() -> None:
    module_text = "\n".join(
        (ROOT / "src" / "ai_risk_trigger_inventory" / "compound_risk" / name).read_text(encoding="utf-8").lower()
        for name in ("uncertainty_set.py", "model_spec.py")
    )
    assert "gurobipy" not in module_text
    assert "openai" not in module_text
    assert "import m5" not in module_text
    assert "solve_prb" not in module_text
    assert "column_and_constraint" not in module_text
