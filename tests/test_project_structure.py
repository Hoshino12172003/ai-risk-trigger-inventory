from pathlib import Path

import pytest

from ai_risk_trigger_inventory.evaluation.metrics import decision_loss


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "relative_path",
    [
        "README.md",
        "pyproject.toml",
        ".gitignore",
        "docs/research_positioning.md",
        "docs/research_questions.md",
        "docs/first_paper_boundary.md",
        "docs/pilot_evidence.md",
        "docs/data_strategy.md",
        "docs/terminology.md",
        "experiments/configs",
        "experiments/pilot",
        "experiments/formal",
        "artifacts/pilot",
        "artifacts/formal",
        "external/README.md",
    ],
)
def test_required_project_path_exists(relative_path: str) -> None:
    assert (ROOT / relative_path).exists()


def test_decision_loss_is_policy_cost_minus_exact_cost() -> None:
    assert decision_loss(113.5, 100.0) == pytest.approx(13.5)
