from pathlib import Path
from unittest.mock import patch

from ai_risk_trigger_inventory.oracle.budget_inventory_adapter import (
    BudgetInventoryAdapter,
    OraclePayload,
    assert_comparable_payloads,
    make_comparable_payloads,
    total_cost_once,
)


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


def test_keep_and_reoptimize_payloads_differ_only_by_mode() -> None:
    keep, reoptimize = make_comparable_payloads(sample_payload())

    assert_comparable_payloads(keep, reoptimize)
    assert keep.decision_mode == "KEEP"
    assert reoptimize.decision_mode == "REOPTIMIZE"


def test_total_cost_does_not_duplicate_reconfiguration_friction() -> None:
    assert total_cost_once(10.0, 20.0, 3.0, 40.0) == 73.0


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
