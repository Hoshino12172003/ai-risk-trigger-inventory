from __future__ import annotations

import csv
import json
from pathlib import Path
import subprocess

import pytest

from ai_risk_trigger_inventory.compound_risk.events import build_event_catalog
from ai_risk_trigger_inventory.compound_risk.metrics import (
    GATE_THRESHOLDS,
    tied_worst,
)
from ai_risk_trigger_inventory.compound_risk.scenarios import (
    REGIMES,
    enumerate_scenarios,
    scenarios_for_regime,
)


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "compound_risk_stage1"
BASE_COMMIT = "2f0ea2d0af2af5424cf947866a6b1e9ca121859c"
PAPER2 = Path(
    r"C:\Users\Hu Jiaxin\Documents\Codex\2026-09-20\files-pasted-by-the-user-github\work\budget-inventory-benders-frozen-51aebd0"
)


@pytest.fixture(scope="module")
def base() -> dict:
    return json.loads((ARTIFACTS / "base_state.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def events(base):
    return build_event_catalog(base["instance"], "MAIN")


@pytest.fixture(scope="module")
def scenarios(base, events):
    return enumerate_scenarios(base["instance"], events, 2)


def test_exactly_six_main_events(events) -> None:
    assert [event.event_id for event in events] == [f"E{i}" for i in range(1, 7)]


def test_full_compound_has_22_deterministic_scenarios(base, events, scenarios) -> None:
    assert len(scenarios) == 22
    assert [row.scenario_id for row in scenarios] == [
        row.scenario_id for row in enumerate_scenarios(base["instance"], events, 2)
    ]
    assert all(len(row.active_events) <= 2 for row in scenarios)


def test_additive_demand_and_capped_route_semantics(base, events) -> None:
    synthetic = [events[0], events[0].__class__(
        "E9", events[0].event_type, events[0].demand_shocks,
        ((0, 0, 0.75),), "test overlap",
    ), events[0].__class__(
        "E8", "TRANSPORT_NETWORK_DISRUPTION", (), ((0, 0, 0.75),), "test cap",
    )]
    catalog = enumerate_scenarios(base["instance"], synthetic, 2)
    overlap = next(row for row in catalog if row.active_events == ("E1", "E9"))
    r, j, shock = events[0].demand_shocks[0]
    assert overlap.demand_multiplier[r][j] == pytest.approx(1.0 + 2 * shock)
    capped = next(row for row in catalog if row.active_events == ("E8", "E9"))
    assert capped.route_availability[0][0] == 0.0


def test_scenarios_have_valid_demand_and_availability(base, scenarios) -> None:
    assert all(
        base["instance"]["base_demand"][r][j] * row.demand_multiplier[r][j] >= 0
        for row in scenarios for r in range(5) for j in range(3)
    )
    assert all(
        0 <= value <= 1
        for row in scenarios for availability in row.route_availability for value in availability
    )


def test_regime_counts(scenarios) -> None:
    assert {name: len(scenarios_for_regime(scenarios, name)) for name in REGIMES} == {
        "R0_NOMINAL": 1,
        "R1_DEMAND_ONLY": 4,
        "R2_REGIONAL_ONLY": 4,
        "R3_NETWORK_ONLY": 4,
        "R4_DEMAND_PLUS_REGIONAL": 11,
        "R5_DEMAND_PLUS_NETWORK": 11,
        "R6_REGIONAL_PLUS_NETWORK": 11,
        "R7_FULL_COMPOUND": 22,
    }


def test_full_closure_forces_affected_shipment_to_zero() -> None:
    with (ARTIFACTS / "scenario_results.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    e6 = next(
        row for row in rows
        if row["regime"] == "R7_FULL_COMPOUND"
        and row["mode"] == "KEEP" and row["active_events"] == "E6"
    )
    assert float(e6["served_demand_under_disrupted_arcs"]) == pytest.approx(0.0, abs=1e-7)


def test_nominal_reproduces_frozen_cost_only_recourse() -> None:
    gates = json.loads((ARTIFACTS / "gate_results.json").read_text(encoding="utf-8"))
    assert gates["nominal_identity"]["pass"] is True
    assert gates["nominal_identity"]["local"] == pytest.approx(
        gates["nominal_identity"]["frozen_cost_only"], rel=1e-5
    )


def test_keep_inventory_is_exact_incumbent(base) -> None:
    with (ARTIFACTS / "inventory_solutions.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    keep = [row for row in rows if row["mode"] == "KEEP"]
    assert keep
    assert all(float(row["inventory"]) == float(row["incumbent_inventory"]) for row in keep)


def test_reconfiguration_friction_counted_exactly_once() -> None:
    with (ARTIFACTS / "regime_summary.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        assert float(row["robust_total_objective"]) == pytest.approx(
            float(row["first_stage_cost"])
            + float(row["reconfiguration_friction"])
            + float(row["worst_case_recourse_cost"]),
            rel=1e-8,
        )


def test_worst_selection_and_ties_are_deterministic() -> None:
    from ai_risk_trigger_inventory.compound_risk.extensive_form import ScenarioRecourse

    def row(sid, value):
        return ScenarioRecourse(sid, (), (), value, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0)
    tied = tied_worst((row("S02", 100.0), row("S01", 100.0 + 5e-5), row("S00", 90.0)))
    assert [value.scenario_id for value in tied] == ["S01", "S02"]


def test_gate_thresholds_are_frozen() -> None:
    assert GATE_THRESHOLDS == {
        "inventory_normalized_l1": 0.05,
        "interaction_relative_increment": 0.05,
        "relative_decision_value": 0.01,
        "shortage_relative_increase": 0.10,
        "fill_rate_drop": 0.02,
    }


def test_frozen_paper2_checkout_is_untouched() -> None:
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PAPER2, text=True).strip() == "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
    assert subprocess.check_output(["git", "status", "--porcelain"], cwd=PAPER2, text=True).strip() == ""


def test_no_genai_ccg_or_m5_dependency() -> None:
    new_code = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for folder in (ROOT / "src" / "ai_risk_trigger_inventory" / "compound_risk", ROOT / "experiments" / "compound_risk_stage1")
        for path in folder.glob("*.py")
    )
    assert "openai" not in new_code
    assert "solve_prb" not in new_code
    assert "column_and_constraint" not in new_code
    assert "import m5" not in new_code
    assert "from m5" not in new_code


def test_previous_experiments_are_unchanged() -> None:
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", BASE_COMMIT], cwd=ROOT, text=True
    ).splitlines()
    assert not any(
        name.startswith("experiments/decision_sensitivity_pilot/")
        or name.startswith("artifacts/decision_sensitivity_pilot/")
        for name in changed
    )
