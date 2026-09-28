from __future__ import annotations

import csv
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "m5_demand_calibration_stage4"
STAGE3D_COMMIT = "0104de30da0686ee4babf7700738a3bbbec70d30"
PAPER2_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
PAPER2 = Path(
    r"C:\Users\Hu Jiaxin\Documents\Codex\2026-09-20\files-pasted-by-the-user-github\work\budget-inventory-benders-frozen-51aebd0"
)


def _audit() -> dict:
    return json.loads((ARTIFACTS / "data_availability_audit.json").read_text(encoding="utf-8"))


def _paper2_git(*arguments: str) -> str:
    safe_path = str(PAPER2.resolve()).replace("\\", "/")
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={safe_path}", *arguments],
        cwd=PAPER2,
        text=True,
    ).strip()


def test_stage4_is_blocked_before_calibration_for_missing_critical_files() -> None:
    audit = _audit()
    assert audit["status"] == "STAGE_4_BLOCKED_MISSING_M5_DATA"
    assert audit["missing_required_files"] == [
        "sales_train_validation.csv", "calendar.csv", "sell_prices.csv",
    ]
    assert audit["missing_optional_files"] == ["sales_train_evaluation.csv"]
    assert audit["calibration_permitted"] is False
    assert audit["calibration_started"] is False
    assert audit["test_coverage_read"] is False


def test_schema_audit_records_all_expected_files_without_invented_values() -> None:
    with (ARTIFACTS / "m5_schema_audit.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["filename"] for row in rows] == [
        "sales_train_validation.csv", "sales_train_evaluation.csv", "calendar.csv", "sell_prices.csv",
    ]
    assert all(row["present"] == "False" for row in rows)
    assert all(row["status"] == "MISSING" for row in rows)
    assert all(row["row_count"] == "" for row in rows)
    assert all(row["columns_json"] == "[]" for row in rows)
    assert all(row["duplicate_key_check"] == "NOT_AUDITED_MISSING_FILE" for row in rows)


def test_blocked_run_created_no_calibration_outputs_or_dispatches() -> None:
    assert sorted(path.name for path in ARTIFACTS.iterdir()) == [
        "data_availability_audit.json", "m5_schema_audit.csv",
    ]
    audit = _audit()
    assert audit["optimizer_dispatches"] == 0
    assert audit["genai_dispatches"] == 0
    assert audit["ml_training_dispatches"] == 0
    assert audit["fabricated_or_downloaded_data"] is False


def test_prior_artifacts_are_unchanged() -> None:
    changed = subprocess.check_output(
        [
            "git", "diff", "--name-only", STAGE3D_COMMIT, "--",
            "artifacts/compound_risk_stage1", "artifacts/compound_risk_stage2",
            "artifacts/risk_impact_calibration_stage3", "artifacts/risk_impact_mapping_stage3c",
            "artifacts/route_risk_calibration_stage3d",
        ],
        cwd=ROOT,
        text=True,
    ).splitlines()
    assert changed == []
    audit = _audit()
    assert all(audit["prior_artifacts_unchanged"].values())
    assert audit["prior_artifact_hashes_before"] == audit["prior_artifact_hashes_after"]


def test_frozen_paper2_checkout_is_unchanged_and_clean() -> None:
    assert _paper2_git("rev-parse", "HEAD") == PAPER2_SHA
    assert _paper2_git("status", "--porcelain") == ""
