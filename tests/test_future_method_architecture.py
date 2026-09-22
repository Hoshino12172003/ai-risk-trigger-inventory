from pathlib import Path
import tomllib
from typing import get_type_hints

import pytest

from ai_risk_trigger_inventory.optimization.paper2_adapter import (
    Paper2DemandParameters,
)
from ai_risk_trigger_inventory.uncertainty import (
    ContextualUncertaintyModel,
    DecisionAwareUncertaintyModel,
    StaticUncertaintyModel,
    UncertaintyEstimate,
    UncertaintyModel,
)


ROOT = Path(__file__).resolve().parents[1]
MODEL_TYPES = (
    StaticUncertaintyModel,
    ContextualUncertaintyModel,
    DecisionAwareUncertaintyModel,
)


def test_all_three_models_implement_common_interface() -> None:
    for model_type in MODEL_TYPES:
        assert issubclass(model_type, UncertaintyModel)
        assert get_type_hints(model_type.predict)["return"] is UncertaintyEstimate
        with pytest.raises(NotImplementedError):
            model_type().fit([], [])


def test_uncertainty_estimate_schema_is_consistent_at_oracle_boundary() -> None:
    estimate = UncertaintyEstimate(
        demand_center=((10.0, 20.0),),
        demand_deviation=((1.0, 2.0),),
        metadata={"method": "test"},
    )

    parameters = Paper2DemandParameters.from_estimate(estimate)

    assert parameters.demand_bar == estimate.demand_center
    assert parameters.demand_hat == estimate.demand_deviation
    assert parameters.metadata == estimate.metadata


def test_no_external_solver_or_deep_learning_dependency_introduced() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = " ".join(project["project"]["dependencies"]).lower()
    architecture_source = "\n".join(
        path.read_text(encoding="utf-8")
        for folder in ("context", "uncertainty", "optimization")
        for path in (
            ROOT / "src/ai_risk_trigger_inventory" / folder
        ).glob("*.py")
    ).lower()

    for forbidden in ("torch", "pytorch", "cvxro", "mosek", "gurobipy"):
        assert forbidden not in dependencies
        assert forbidden not in architecture_source
