import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts/decision_sensitivity_pilot"


def _rows(filename: str) -> list[dict[str, str]]:
    with (ARTIFACTS / filename).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_twenty_state_results_are_complete_and_certified() -> None:
    main = _rows("twenty_state_results_main.csv")
    sensitivity = _rows("twenty_state_results_sensitivity.csv")

    assert len(main) == len(sensitivity) == 20
    assert len({row["state_id"] for row in main}) == 20
    assert {row["state_id"] for row in main} == {
        row["state_id"] for row in sensitivity
    }
    for row in main + sensitivity:
        assert row["oracle_status"] == "OPTIMAL"
        assert row["exact_certification_pass"] == "True"
        assert row["global_risk_budget_coupling_pass"] == "True"
        assert abs(float(row["recourse_identity_error"])) <= 1e-4
        assert float(row["decision_value"]) >= -1e-4


def test_only_main_calibration_drives_structural_classification() -> None:
    summary = json.loads(
        (ARTIFACTS / "twenty_state_summary.json").read_text(encoding="utf-8")
    )

    assert summary["calibration_for_classification"] == "PRIMARY_PILOT_CALIBRATION"
    assert summary["main_scheme"] == "UNIT_COST_BASED"
    assert summary["sensitivity_scheme"] == "TRANSPORT_BASED"
    assert summary["thresholds_unchanged"] is True
    assert summary["classification"] in {
        "STRONG_STRUCTURAL_SIGNAL",
        "PARTIAL_STRUCTURAL_SIGNAL",
        "WEAK_OR_NO_STRUCTURAL_SIGNAL",
    }


def test_execution_audit_records_frozen_clean_certified_batch() -> None:
    audit = json.loads(
        (ARTIFACTS / "twenty_state_execution_audit.json").read_text(
            encoding="utf-8"
        )
    )

    assert audit["status"] == "PASS"
    assert audit["certified_reoptimizations"] == 40
    assert audit["solver_dispatch_count"] == 120
    assert audit["frozen_checkout_clean_after"] is True
    assert all(
        audit["payload_equivalence_after_removing_service_penalty"].values()
    )
