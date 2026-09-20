from ai_risk_trigger_inventory.oracle.provenance import OptimizationOracleProvenance


def test_oracle_is_pinned_and_frozen() -> None:
    provenance = OptimizationOracleProvenance()

    assert provenance.commit_sha == "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
    assert provenance.frozen is True
