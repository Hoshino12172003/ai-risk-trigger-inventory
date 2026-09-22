import pytest
import pandas as pd

from ai_risk_trigger_inventory.data.decision_sensitivity import (
    assert_no_post_solve_leakage,
    calibrate_inventory,
    demand_shift,
    rolling_baseline,
    select_families,
    select_store_contexts,
    select_stores,
)
from experiments.decision_sensitivity_pilot.run_pilot import validate_dry_run_rows
from experiments.decision_sensitivity_pilot.build_instances import (
    _select_diverse_states,
)


def test_baseline_and_shift_use_only_prior_weeks() -> None:
    history = [80.0, 100.0, 120.0, 100.0]
    baseline = rolling_baseline(history)
    shift = demand_shift(120.0, baseline)

    assert baseline == pytest.approx(100.0)
    assert shift["relative_demand_shift"] == pytest.approx(0.20)
    assert shift["positive_demand_shift"] == pytest.approx(0.20)
    assert shift["negative_demand_shift"] == 0.0


def test_small_baseline_is_filtered() -> None:
    with pytest.raises(ValueError, match="below the fixed minimum"):
        rolling_baseline([0.0, 0.5, 0.0, 0.5])


def test_store_and_family_selection_are_deterministic() -> None:
    stores = {str(index): float(index) for index in range(1, 11)}

    assert select_stores(stores) == ("1", "3", "5", "8", "10")
    assert select_families({"DAIRY", "PRODUCE", "GROCERY I"}) == (
        "GROCERY I",
        "PRODUCE",
        "DAIRY",
    )


def test_multiple_network_contexts_are_deterministic_and_small() -> None:
    stores = {str(index): float(index) for index in range(1, 11)}
    contexts = select_store_contexts(stores, context_count=3)

    assert len(contexts) == 3
    assert len(set(contexts)) == 3
    assert all(len(context) == 5 for context in contexts)


def test_incumbent_inventory_is_calibrated_not_observed() -> None:
    calibration = calibrate_inventory([80.0, 100.0, 120.0, 100.0])

    assert calibration.incumbent_inventory == pytest.approx(110.0)
    assert calibration.capacity == pytest.approx(144.0)
    assert calibration.source == "CALIBRATED"


def test_post_solve_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="post-solve leakage"):
        assert_no_post_solve_leakage({"state", "decision_value"})


def test_five_state_dry_run_validates_without_executing_oracle() -> None:
    rows = []
    for index in range(5):
        baseline = 100.0
        current = 105.0 + index
        relative = (current - baseline) / baseline
        rows.append(
            {
                "state_id": f"state-{index}",
                "state": "Pichincha" if index % 2 == 0 else "Guayas",
                "selected_stores": "1|2|3|4|5",
                "selected_families": "GROCERY I|BEVERAGES|PRODUCE",
                "baseline_demand": baseline,
                "current_demand": current,
                "absolute_demand_shift": current - baseline,
                "relative_demand_shift": relative,
                "positive_demand_shift": relative,
                "negative_demand_shift": 0.0,
                "inventory_to_demand_ratio": 1.05,
                "capacity_slack_ratio": 0.20,
                "demand_concentration": 0.10,
                "incumbent_inventory_concentration": 0.10,
                "transport_cost_cv": 0.20,
                "transport_substitutability_proxy": 0.50,
                "incumbent_inventory_source": "CALIBRATED",
                "oracle_repository": "Hoshino12172003/budget-inventory-benders",
                "oracle_commit": "51aebd06edf8f5d6d124d0f3eebdbb901e63274f",
            }
        )
    result = validate_dry_run_rows(rows, list(rows[0]))

    assert result["status"] == "PASS"
    assert result["constructed_states"] == 5
    assert result["executed_states"] == 0
    assert result["solver_calls"] == 0


def test_twenty_state_selection_is_balanced_diverse_and_deterministic() -> None:
    rows = []
    shifts = [-0.20, -0.08, -0.03, 0.0, 0.03, 0.08, 0.15, 0.25, -0.12, 0.01, 0.12, 0.30]
    for state in ("Pichincha", "Guayas"):
        for index, shift in enumerate(shifts):
            rows.append(
                {
                    "state_id": f"{state}-{index}",
                    "state": state,
                    "week_start": pd.Timestamp(2013 + index % 5, 1 + index % 12, 1),
                    "network_id": f"context-{index % 2}",
                    "selected_families": "A|B|C" if index % 2 else "D|E|F",
                    "relative_demand_shift": shift,
                    "positive_demand_shift": max(shift, 0.0),
                    "promotion_intensity": index / 100.0,
                    "holiday_flag": index in (4, 9),
                }
            )
    candidates = pd.DataFrame(rows)

    first = _select_diverse_states(candidates, 20)
    second = _select_diverse_states(candidates, 20)

    assert first["state_id"].tolist() == second["state_id"].tolist()
    assert first["state"].value_counts().to_dict() == {
        "Pichincha": 10,
        "Guayas": 10,
    }
    assert (first["relative_demand_shift"] < -0.05).any()
    assert (first["relative_demand_shift"].abs() <= 0.05).any()
    assert (first["relative_demand_shift"] > 0.05).any()
    assert first["selected_families"].nunique() == 2
