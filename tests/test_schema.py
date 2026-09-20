from dataclasses import fields

from ai_risk_trigger_inventory.data.schemas import DecisionState


def test_decision_state_excludes_post_solve_fields() -> None:
    field_names = {field.name for field in fields(DecisionState)}

    assert field_names.isdisjoint({"objective", "RI", "runtime", "iterations"})


def test_decision_state_contains_required_pre_solve_fields() -> None:
    field_names = {field.name for field in fields(DecisionState)}

    assert {
        "domain_id",
        "network_id",
        "time_id",
        "beta",
        "Gamma",
        "lambda_R",
        "demand_features",
        "inventory_features",
        "network_features",
        "cost_features",
    } <= field_names
