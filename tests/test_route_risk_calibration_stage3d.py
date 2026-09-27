from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pandas as pd
import pytest

from ai_risk_trigger_inventory.calibration.event_mapping import AffectedArc, EventMetadata
from ai_risk_trigger_inventory.calibration.route_calibration import (
    ALLOWED_EVIDENCE_LEVELS,
    ALLOWED_TRANSFER_ROLES,
    ROUTE_CALIBRATION,
    ROUTE_SENSITIVITY_GRID,
    evidence_rows,
    map_event_to_route_calibration,
    route_state_for_metadata,
)


ROOT = Path(__file__).resolve().parents[1]
STAGE3C_COMMIT = "1620f4787347ef59e8d421e2dca89beef296aa4d"
PAPER2_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
PAPER2 = Path(
    r"C:\Users\Hu Jiaxin\Documents\Codex\2026-09-20\files-pasted-by-the-user-github\work\budget-inventory-benders-frozen-51aebd0"
)
ARTIFACTS = ROOT / "artifacts" / "route_risk_calibration_stage3d"


def _event(
    severity: str,
    subtype: str = "road disruption",
    risk_type: str = "TRANSPORT_NETWORK_DISRUPTION",
) -> EventMetadata:
    return EventMetadata(
        event_id="TEST",
        risk_type=risk_type,
        event_subtype=subtype,
        region_scope=("R1",),
        severity=severity,
        start_time="2030-01-01T00:00:00Z",
        expected_duration="ONE_PERIOD",
        evidence_source="TEST",
        affected_arcs=(AffectedArc("W1", "R1"),),
    )


def test_route_intervals_and_main_values_are_exact() -> None:
    assert (
        ROUTE_CALIBRATION["NORMAL"]["delta_A_lower"],
        ROUTE_CALIBRATION["NORMAL"]["delta_A_main"],
        ROUTE_CALIBRATION["NORMAL"]["delta_A_upper"],
    ) == (0.0, 0.0, 0.0)
    assert (
        ROUTE_CALIBRATION["MODERATE_DISRUPTION"]["delta_A_lower"],
        ROUTE_CALIBRATION["MODERATE_DISRUPTION"]["delta_A_main"],
        ROUTE_CALIBRATION["MODERATE_DISRUPTION"]["delta_A_upper"],
    ) == (0.10, 0.25, 0.40)
    assert (
        ROUTE_CALIBRATION["SEVERE_DISRUPTION"]["delta_A_lower"],
        ROUTE_CALIBRATION["SEVERE_DISRUPTION"]["delta_A_main"],
        ROUTE_CALIBRATION["SEVERE_DISRUPTION"]["delta_A_upper"],
    ) == (0.40, 0.60, 0.80)
    assert (
        ROUTE_CALIBRATION["CLOSURE"]["delta_A_lower"],
        ROUTE_CALIBRATION["CLOSURE"]["delta_A_main"],
        ROUTE_CALIBRATION["CLOSURE"]["delta_A_upper"],
    ) == (1.0, 1.0, 1.0)


def test_sensitivity_grid_is_exact() -> None:
    assert ROUTE_SENSITIVITY_GRID == (
        {"scenario_label": "LOW", "moderate_delta_A": 0.10, "severe_delta_A": 0.40, "closure_delta_A": 1.00},
        {"scenario_label": "MAIN", "moderate_delta_A": 0.25, "severe_delta_A": 0.60, "closure_delta_A": 1.00},
        {"scenario_label": "HIGH", "moderate_delta_A": 0.40, "severe_delta_A": 0.80, "closure_delta_A": 1.00},
    )


def test_extreme_does_not_automatically_map_to_closure() -> None:
    assert route_state_for_metadata(_event("EXTREME")) == "SEVERE_DISRUPTION"
    result = map_event_to_route_calibration(_event("EXTREME"))[0]
    assert result["delta_A_main"] == 0.60
    assert result["delta_A_upper"] == 0.80


@pytest.mark.parametrize(
    "subtype",
    ["full closure", "confirmed closure", "road completely closed", "route unavailable", "confirmed full road closure"],
)
def test_explicit_closure_phrases_map_to_one(subtype: str) -> None:
    result = map_event_to_route_calibration(_event("MODERATE", subtype))[0]
    assert result["route_state"] == "CLOSURE"
    assert result["delta_A_main"] == 1.0


def test_transport_disruption_has_zero_demand_impact() -> None:
    mapping = pd.read_csv(ARTIFACTS / "transport_disruption_mapping.csv")
    assert mapping["delta_D"].eq(0.0).all()
    assert mapping["demand_rule"].eq("NO_DEMAND_IMPACT").all()
    assert mapping.set_index("severity")["delta_A_main"].to_dict() == {
        "MODERATE": 0.25, "HIGH": 0.60, "EXTREME": 0.60,
    }


def test_regional_emergency_has_demand_and_route_parameters() -> None:
    mapping = pd.read_csv(ARTIFACTS / "regional_emergency_complete_mapping.csv")
    assert len(mapping) == 3
    assert mapping["delta_D"].notna().all()
    assert mapping["delta_A_main"].notna().all()
    assert mapping.set_index("severity")["delta_A_main"].to_dict() == {
        "MODERATE": 0.25, "HIGH": 0.60, "EXTREME": 0.60,
    }
    assert set(mapping["demand_provenance"]) == {"INDIRECT_RETAIL_PROXY"}
    assert set(mapping["route_provenance"]) == {"LITERATURE_ANCHORED_CROSS_REGION"}


def test_route_mapping_is_not_product_specific() -> None:
    mapping = pd.read_csv(ARTIFACTS / "route_calibration_mapping.csv")
    assert "product" not in mapping.to_csv(index=False).lower()


def test_evidence_rows_use_allowed_hierarchy_and_roles() -> None:
    evidence = evidence_rows()
    assert len(evidence) == 7
    assert {row["evidence_level"] for row in evidence} <= ALLOWED_EVIDENCE_LEVELS
    assert {row["transfer_role"] for row in evidence} <= ALLOWED_TRANSFER_ROLES
    assert sum(row["evidence_level"] == "LEVEL_1" for row in evidence) == 3
    assert sum(row["evidence_level"] == "LEVEL_2" for row in evidence) == 1
    assert sum(row["evidence_level"] == "LEVEL_3" for row in evidence) == 3


def test_no_evidence_claims_direct_ecuador_parameter_estimate() -> None:
    evidence = pd.read_csv(ARTIFACTS / "evidence_table.csv")
    assert "DIRECT_ECUADOR_PARAMETER_ESTIMATE" not in set(evidence["transfer_role"])
    ecuador = evidence[evidence["evidence_level"] == "LEVEL_1"]
    assert set(ecuador["transfer_role"]) == {"LOCAL_CONTEXT_SUPPORT"}
    assert ecuador["notes"].str.contains("does not|not a", case=False, regex=True).all()


def test_no_genai_optimizer_or_ml_training() -> None:
    audit = json.loads((ARTIFACTS / "execution_audit.json").read_text(encoding="utf-8"))
    assert audit["genai_dispatches"] == 0
    assert audit["optimizer_dispatches"] == 0
    assert audit["ml_training_dispatches"] == 0
    code = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for directory in (
            ROOT / "src" / "ai_risk_trigger_inventory" / "calibration",
            ROOT / "experiments" / "route_risk_calibration_stage3d",
        )
        for path in directory.glob("*.py")
    )
    for forbidden in (
        "openai", "gurobipy", "solve_prb", "column_and_constraint", ".fit(",
        "quantileregressor", "gradientboostingregressor", "xgboost", "tensorflow", "torch",
    ):
        assert forbidden not in code


def test_prior_stage_artifacts_are_unchanged() -> None:
    changed = subprocess.check_output(
        [
            "git", "diff", "--name-only", STAGE3C_COMMIT, "--",
            "artifacts/compound_risk_stage1", "artifacts/compound_risk_stage2",
            "artifacts/risk_impact_calibration_stage3", "artifacts/risk_impact_mapping_stage3c",
        ],
        cwd=ROOT,
        text=True,
    ).splitlines()
    assert changed == []
    audit = json.loads((ARTIFACTS / "execution_audit.json").read_text(encoding="utf-8"))
    assert all(audit["prior_artifacts_unchanged"].values())
    assert audit["prior_artifact_hashes_before"] == audit["prior_artifact_hashes_after"]


def test_frozen_paper2_checkout_is_unchanged_and_clean() -> None:
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PAPER2, text=True).strip() == PAPER2_SHA
    assert subprocess.check_output(["git", "status", "--porcelain"], cwd=PAPER2, text=True).strip() == ""


def test_final_classifications_are_audited_without_real_world_claim() -> None:
    audit = json.loads((ARTIFACTS / "execution_audit.json").read_text(encoding="utf-8"))
    assert audit["status"] == "STAGE_3D_ROUTE_CALIBRATION_PASS"
    assert audit["full_event_parameter_mapping_status"] == "COMPLETE_FOR_CURRENT_MODEL"
    assert audit["real_world_calibration_claim"] is False
