from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from ai_risk_trigger_inventory.oracle.budget_inventory_adapter import (
    BudgetInventoryAdapter,
    OraclePayload,
    assert_comparable_payloads,
    make_comparable_payloads,
    total_cost_once,
)


EXPERIMENT = Path(__file__).resolve().parents[1] / "experiments" / "decision_sensitivity_pilot"
sys.path.insert(0, str(EXPERIMENT))
from execute_five_state_pilot import _cost_audit, preflight_pair


def sample_payload() -> OraclePayload:
    return OraclePayload(
        decision_mode="KEEP",
        instance={"name": "dry-run"},
        x0=[[10.0, 20.0]],
        y0=[1],
        budget=100.0,
        gamma=1,
        lambda_r=0.05,
        active_network_id="network-1",
        evaluation_horizon="2020-01-06/2020-01-12",
    )


def feasible_payload() -> OraclePayload:
    payload = sample_payload()
    return OraclePayload(
        **{
            **payload.__dict__,
            "instance": {
                "depot_ids": ["d1"],
                "region_ids": ["r1"],
                "product_ids": ["p1", "p2"],
                "base_demand": [[5.0, 6.0]],
                "demand_deviation": [[1.0, 1.0]],
                "transport_cost": [[[1.0, 1.0]]],
                "shortage_penalty": [[5.0, 5.0]],
                "service_level": [0.9, 0.9],
                "service_penalty": [10.0, 10.0],
                "capacity": [40.0],
                "inventory_upper_bound": [[20.0, 30.0]],
                "fixed_depot_cost": [5.0],
                "inventory_cost": [[1.0, 1.0]],
                "product_volume": [1.0, 1.0],
            },
        }
    )


def test_keep_and_reoptimize_payloads_differ_only_by_mode() -> None:
    keep, reoptimize = make_comparable_payloads(sample_payload())

    assert_comparable_payloads(keep, reoptimize)
    assert keep.decision_mode == "KEEP"
    assert reoptimize.decision_mode == "REOPTIMIZE"


def test_total_cost_does_not_duplicate_reconfiguration_friction() -> None:
    assert total_cost_once(10.0, 20.0, 3.0, 40.0) == 73.0


def test_preflight_accepts_feasible_comparable_payloads() -> None:
    keep, reoptimize = make_comparable_payloads(feasible_payload())

    result = preflight_pair(keep, reoptimize)

    assert result["x0_inventory_upper_bounds_pass"] is True
    assert result["x0_capacity_pass"] is True
    assert result["keep_budget_pass"] is True


def test_preflight_rejects_payload_difference_beyond_mode() -> None:
    keep, reoptimize = make_comparable_payloads(feasible_payload())
    reoptimize = OraclePayload(**{**reoptimize.__dict__, "gamma": 0})

    with pytest.raises(ValueError, match="differ beyond decision_mode"):
        preflight_pair(keep, reoptimize)


def test_cost_audit_detects_dominance_violation() -> None:
    keep = {
        "total_cost": 100.0,
        "first_stage_expenditure": 40.0,
        "robust_recourse_cost": 60.0,
    }
    reoptimize = {
        "total_cost": 101.0,
        "first_stage_expenditure": 41.0,
        "robust_recourse_cost": 60.0,
        "recourse_decomposition_error": 0.0,
    }

    assert _cost_audit("state", keep, reoptimize)["cost_dominance_pass"] is False


def test_adapter_verifies_frozen_checkout_using_read_only_git_commands() -> None:
    responses = [
        "51aebd06edf8f5d6d124d0f3eebdbb901e63274f\n",
        "",
    ]
    with patch("subprocess.check_output", side_effect=responses) as check_output:
        adapter = BudgetInventoryAdapter(Path("frozen-checkout"))

    assert adapter.solver_calls == 0
    commands = [call.args[0][1:] for call in check_output.call_args_list]
    assert commands == [["rev-parse", "HEAD"], ["status", "--porcelain"]]
