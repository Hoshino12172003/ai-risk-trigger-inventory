from itertools import combinations
import inspect
import json
import math
from pathlib import Path

from ai_risk_trigger_inventory.oracle.budget_inventory_adapter import (
    BudgetInventoryAdapter,
)
from ai_risk_trigger_inventory.oracle.cost_only_recourse import (
    compose_robust_recourse_cost,
)


ROOT = Path(__file__).resolve().parents[1]


def _audited_block_optima() -> dict[tuple[int, tuple[int, ...]], float]:
    audit = json.loads(
        (
            ROOT
            / "artifacts/decision_sensitivity_pilot/keep_stage1_lp_audit.json"
        ).read_text(encoding="utf-8")
    )
    parsed = {}
    for label, value in audit["block_optimum_costs"].items():
        product_text, regions_text = label.split(";")
        product = int(product_text.split("=")[1])
        region_values = regions_text.split("=")[1]
        regions = tuple(int(item) for item in region_values.split(",") if item)
        parsed[(product, regions)] = float(value)
    return parsed


def test_cost_only_composition_matches_frozen_stage1_diagnostic() -> None:
    components = [(region, product) for region in range(5) for product in range(3)]
    scenarios = [
        scenario
        for count in range(3)
        for scenario in combinations(components, count)
    ]

    robust_cost, _ = compose_robust_recourse_cost(
        scenarios, _audited_block_optima(), 3
    )

    assert math.isclose(robust_cost, 404641976678.6842, abs_tol=1e-4)


def test_keep_has_no_reporting_qp_dependency() -> None:
    source = inspect.getsource(BudgetInventoryAdapter.evaluate)

    assert "evaluate_robust_service_detailed" not in source
    assert "evaluate_cost_only_robust_recourse" in source


def test_reoptimize_post_audit_has_no_reporting_qp_dependency() -> None:
    source = inspect.getsource(BudgetInventoryAdapter.evaluate)

    assert "service_module" not in source
    assert "REOPTIMIZE_RECOURSE_IDENTITY_FAIL" in source
