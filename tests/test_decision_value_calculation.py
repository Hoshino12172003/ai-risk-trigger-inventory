from ai_risk_trigger_inventory.oracle.decision_sensitivity import calculate_decision_value


def test_decision_value_is_keep_minus_reopt_without_extra_friction() -> None:
    total_cost_keep = 125.0
    total_cost_reopt_including_reconfiguration_friction = 100.0

    assert calculate_decision_value(
        total_cost_keep,
        total_cost_reopt_including_reconfiguration_friction,
    ) == 25.0
