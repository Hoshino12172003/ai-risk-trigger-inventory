import pytest

from ai_risk_trigger_inventory.evaluation.decision_sensitivity import (
    PARTIAL_STRUCTURAL_SIGNAL,
    STRONG_STRUCTURAL_SIGNAL,
    WEAK_OR_NO_STRUCTURAL_SIGNAL,
    StructuralEvidence,
    classify_structural_signal,
)
from ai_risk_trigger_inventory.oracle.decision_sensitivity import (
    BlockedOracleAdapter,
    OracleIntegrationBlocked,
)


def test_unverified_oracle_is_blocked_without_a_solver_call() -> None:
    adapter = BlockedOracleAdapter()

    with pytest.raises(OracleIntegrationBlocked, match="BLOCKED"):
        adapter.evaluate({"state_id": "dry-run"})

    assert adapter.solver_calls == 0
    assert adapter.provenance.commit_sha == "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (StructuralEvidence(20, 0.60, 0.16, 3, 2, 2), STRONG_STRUCTURAL_SIGNAL),
        (StructuralEvidence(20, 0.75, 0.06, 1, 2, 1), PARTIAL_STRUCTURAL_SIGNAL),
        (StructuralEvidence(20, 0.85, 0.01, 0, 2, 2), WEAK_OR_NO_STRUCTURAL_SIGNAL),
    ],
)
def test_preregistered_classification(evidence: StructuralEvidence, expected: str) -> None:
    assert classify_structural_signal(evidence) == expected
