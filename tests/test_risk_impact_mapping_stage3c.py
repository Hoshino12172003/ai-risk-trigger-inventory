from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pandas as pd
import pytest

from ai_risk_trigger_inventory.calibration.event_mapping import (
    AffectedArc,
    DemandCalibrationLookup,
    EventMetadata,
    SEVERITY_TO_QUANTILE,
    demand_stratum_for_event,
    map_event_to_demand_impact,
    map_event_to_route_impacts,
    route_severity_rows,
)


ROOT = Path(__file__).resolve().parents[1]
STAGE3_COMMIT = "1e4ba587e0aff54d7c38e97f69a6cd34c1d3a77b"
PAPER2_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
PAPER2 = Path(
    r"C:\Users\Hu Jiaxin\Documents\Codex\2026-09-20\files-pasted-by-the-user-github\work\budget-inventory-benders-frozen-51aebd0"
)
STAGE3A = ROOT / "artifacts" / "risk_impact_calibration_stage3"
ARTIFACTS = ROOT / "artifacts" / "risk_impact_mapping_stage3c"


@pytest.fixture(scope="module")
def lookup() -> DemandCalibrationLookup:
    return DemandCalibrationLookup.from_stage3a_artifacts(STAGE3A)


def _event(
    subtype: str,
    severity: str = "HIGH",
    risk_type: str = "FLASH_DEMAND_SURGE",
    arcs: tuple[AffectedArc, ...] = (),
) -> EventMetadata:
    return EventMetadata(
        event_id="TEST_EVENT",
        risk_type=risk_type,
        event_subtype=subtype,
        region_scope=("R1",),
        severity=severity,
        start_time="2030-01-01T00:00:00Z",
        expected_duration="ONE_PERIOD",
        evidence_source="TEST",
        affected_arcs=arcs,
    )


def test_severity_quantile_mapping_is_frozen_and_q99_is_absent() -> None:
    assert dict(SEVERITY_TO_QUANTILE) == {
        "MODERATE": 0.75,
        "HIGH": 0.90,
        "EXTREME": 0.95,
    }
    assert 0.99 not in SEVERITY_TO_QUANTILE.values()


def test_flash_subtype_precedence_maps_d1_d2_d3_d4() -> None:
    assert demand_stratum_for_event(_event("flash promotion")) == "D1_PROMOTION"
    assert demand_stratum_for_event(_event("scheduled public event")) == "D2_HOLIDAY_EVENT"
    assert demand_stratum_for_event(_event("holiday promotion campaign")) == "D3_PROMOTION_HOLIDAY_OVERLAP"
    assert demand_stratum_for_event(_event("unclassified trigger")) == "D4_GENERIC_EVENT_PROXY"


def test_regional_emergency_always_uses_indirect_generic_proxy(lookup) -> None:
    metadata = _event(
        "holiday promotion regional emergency",
        risk_type="REGIONAL_EMERGENCY_DISRUPTION",
    )
    result = map_event_to_demand_impact(metadata, lookup)
    assert result["calibration_stratum"] == "D4_GENERIC_EVENT_PROXY"
    assert result["provenance"] == "INDIRECT_RETAIL_PROXY"
    assert result["status"] == "INDIRECT_PROXY"


def test_unknown_severity_cannot_produce_demand_delta(lookup) -> None:
    result = map_event_to_demand_impact(_event("promotion", "UNKNOWN"), lookup)
    assert result["delta_D"] is None
    assert result["quantile_level"] is None
    assert result["status"] == "UNRESOLVED"
    assert result["resolution_code"] == "CALIBRATION_UNRESOLVED"


def test_mapping_values_exactly_equal_stage3a_artifacts() -> None:
    mapping = pd.read_csv(ARTIFACTS / "demand_severity_mapping.csv")
    quantiles = pd.read_csv(STAGE3A / "empirical_quantiles.csv")
    keys = {
        "D1_PROMOTION": ("event_proxy", "P1_PROMOTION"),
        "D2_HOLIDAY_EVENT": ("event_proxy", "P2_HOLIDAY_EVENT"),
        "D3_PROMOTION_HOLIDAY_OVERLAP": ("event_proxy", "P3_PROMOTION_HOLIDAY_OVERLAP"),
        "D4_GENERIC_EVENT_PROXY": ("overall", "ALL_EVENT_PROXIES"),
    }
    for row in mapping.itertuples(index=False):
        dimension, value = keys[row.calibration_stratum]
        source = quantiles[
            (quantiles["population"] == "POSITIVE_UPLIFT_EVENT_PROXY_P1_P2_P3")
            & (quantiles["group_dimension"] == dimension)
            & (quantiles["group_value"] == value)
        ].iloc[0]
        assert row.delta_D == pytest.approx(source[f"q{int(row.quantile_level * 100)}"], rel=0, abs=0)


def test_mapping_has_no_product_specific_rows() -> None:
    mapping = pd.read_csv(ARTIFACTS / "demand_severity_mapping.csv")
    text = mapping.to_csv(index=False).lower()
    assert "product_family" not in text
    assert not mapping["calibration_stratum"].str.contains("PRODUCT", case=False).any()


def test_formal_route_endpoints_and_unresolved_middle_states() -> None:
    formal = {
        row["route_state"]: row
        for row in route_severity_rows()
        if row["mapping_role"] == "FORMAL_STATE"
    }
    assert formal["NORMAL"]["delta_A"] == 0.0
    assert formal["CLOSURE"]["delta_A"] == 1.0
    assert formal["MODERATE_DISRUPTION"]["delta_A"] is None
    assert formal["SEVERE_DISRUPTION"]["delta_A"] is None
    assert formal["MODERATE_DISRUPTION"]["status"] == "PENDING_EXTERNAL_EVIDENCE"
    assert formal["SEVERE_DISRUPTION"]["status"] == "PENDING_EXTERNAL_EVIDENCE"


def test_stage1_route_stress_values_are_reference_only() -> None:
    rows = route_severity_rows()
    references = [row for row in rows if row["status"] == "STRESS_TEST_REFERENCE"]
    assert [row["delta_A"] for row in references] == [0.25, 0.50, 0.75]
    assert all(row["mapping_role"] == "REFERENCE_ONLY" for row in references)
    formal_values = {
        row["delta_A"] for row in rows
        if row["mapping_role"] == "FORMAL_STATE" and row["delta_A"] is not None
    }
    assert formal_values == {0.0, 1.0}


def test_route_mapping_targets_warehouse_region_arcs_and_preserves_duration() -> None:
    arcs = (AffectedArc("W1", "R1"), AffectedArc("W2", "R2"))
    result = map_event_to_route_impacts(
        _event("regional road disruption", "HIGH", "TRANSPORT_NETWORK_DISRUPTION", arcs)
    )
    assert [(row["warehouse_id"], row["region_id"]) for row in result] == [("W1", "R1"), ("W2", "R2")]
    assert all(row["impact_scope"] == "WAREHOUSE_REGION_ARC" for row in result)
    assert all(row["route_state"] == "SEVERE_DISRUPTION" for row in result)
    assert all(row["delta_A"] is None for row in result)
    assert all(row["expected_duration"] == "ONE_PERIOD" for row in result)


def test_confirmed_full_closure_is_definition_supported() -> None:
    result = map_event_to_route_impacts(
        _event(
            "confirmed full road closure", "EXTREME", "TRANSPORT_NETWORK_DISRUPTION",
            (AffectedArc("W1", "R1"),),
        )
    )[0]
    assert result["route_state"] == "CLOSURE"
    assert result["delta_A"] == 1.0
    assert result["status"] == "DEFINITION_SUPPORTED"


def test_regional_artifact_records_mixed_provenance() -> None:
    regional = pd.read_csv(ARTIFACTS / "regional_emergency_mapping.csv")
    assert len(regional) == 3
    assert set(regional["demand_provenance"]) == {"INDIRECT_RETAIL_PROXY"}
    assert set(regional["demand_status"]) == {"INDIRECT_PROXY"}
    assert regional["delta_A"].isna().all()
    assert set(regional["route_status"]) == {"PENDING_EXTERNAL_EVIDENCE"}


def test_examples_cover_required_four_cases() -> None:
    examples = json.loads((ARTIFACTS / "mapping_examples.json").read_text(encoding="utf-8"))
    assert examples["A"]["demand_mapping"]["calibration_stratum"] == "D1_PROMOTION"
    assert examples["A"]["demand_mapping"]["quantile_level"] == 0.90
    assert examples["B"]["demand_mapping"]["calibration_stratum"] == "D2_HOLIDAY_EVENT"
    assert examples["B"]["demand_mapping"]["quantile_level"] == 0.95
    assert examples["C"]["demand_mapping"]["status"] == "INDIRECT_PROXY"
    assert examples["C"]["route_mapping"][0]["delta_A"] is None
    assert examples["D"]["route_mapping"][0]["delta_A"] == 1.0


def test_no_optimizer_or_ml_training_dispatches() -> None:
    audit = json.loads((ARTIFACTS / "execution_audit.json").read_text(encoding="utf-8"))
    assert audit["optimizer_dispatches"] == 0
    assert audit["ml_training_dispatches"] == 0
    new_code = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for directory in (
            ROOT / "src" / "ai_risk_trigger_inventory" / "calibration",
            ROOT / "experiments" / "risk_impact_mapping_stage3c",
        )
        for path in directory.glob("*.py")
    )
    for forbidden in (
        "gurobipy", "solve_prb", "column_and_constraint", ".fit(",
        "quantileregressor", "gradientboostingregressor", "xgboost", "tensorflow", "torch",
    ):
        assert forbidden not in new_code


def test_prior_artifacts_are_unchanged() -> None:
    changed = subprocess.check_output(
        [
            "git", "diff", "--name-only", STAGE3_COMMIT, "--",
            "artifacts/compound_risk_stage1", "artifacts/compound_risk_stage2",
            "artifacts/risk_impact_calibration_stage3",
        ],
        cwd=ROOT,
        text=True,
    ).splitlines()
    assert changed == []
    audit = json.loads((ARTIFACTS / "execution_audit.json").read_text(encoding="utf-8"))
    assert all(audit["prior_artifacts_unchanged"].values())
    assert audit["prior_artifact_hashes_before"] == audit["prior_artifact_hashes_after"]


def test_frozen_paper2_checkout_is_clean() -> None:
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PAPER2, text=True).strip() == PAPER2_SHA
    assert subprocess.check_output(["git", "status", "--porcelain"], cwd=PAPER2, text=True).strip() == ""
