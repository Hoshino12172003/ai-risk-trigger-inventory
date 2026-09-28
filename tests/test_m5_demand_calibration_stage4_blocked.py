from __future__ import annotations

import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from ai_risk_trigger_inventory.calibration.m5_validation import (
    COVERAGE_TOLERANCE,
    MINIMUM_POSITIVE_BASELINE,
    add_causal_baselines,
    add_uplift,
    coverage_direction,
)


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "m5_demand_calibration_stage4"
STAGE3D_COMMIT = "0104de30da0686ee4babf7700738a3bbbec70d30"
PAPER2_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
PAPER2 = Path(
    r"C:\Users\Hu Jiaxin\Documents\Codex\2026-09-20\files-pasted-by-the-user-github\work\budget-inventory-benders-frozen-51aebd0"
)


def load_json(name: str) -> dict:
    return json.loads((ARTIFACTS / name).read_text(encoding="utf-8"))


def paper2_git(*arguments: str) -> str:
    safe_path = str(PAPER2.resolve()).replace("\\", "/")
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={safe_path}", *arguments],
        cwd=PAPER2, text=True,
    ).strip()


def test_initial_blocked_record_is_preserved_and_restoration_passed() -> None:
    blocked = load_json("data_availability_audit_blocked_initial.json")
    restored = load_json("data_restoration_audit.json")
    assert blocked["status"] == "STAGE_4_BLOCKED_MISSING_M5_DATA"
    assert restored["status"] == "M5_DATA_RESTORATION_PASS"
    assert restored["raw_data_committed"] is False
    assert len(restored["files"]) == 5
    assert all(row["copy_status"] == "COPIED_AND_VERIFIED" for row in restored["files"])
    assert all(row["size_bytes"] > 0 and len(row["sha256"]) == 64 for row in restored["files"])


def test_real_m5_availability_and_schema_audit_passed() -> None:
    audit = load_json("data_availability_audit.json")
    assert audit["status"] == "M5_DATA_AVAILABILITY_PASS"
    assert audit["calibration_permitted"] is True
    assert audit["missing_required_files"] == []
    files = {row["filename"]: row for row in audit["files"]}
    assert files["sales_train_validation.csv"]["row_count"] == 30490
    assert files["sales_train_evaluation.csv"]["row_count"] == 30490
    assert files["sell_prices.csv"]["row_count"] == 6841121
    assert files["calendar.csv"]["row_count"] == 1969
    assert files["sell_prices.csv"]["duplicate_key_check"]["exact_duplicate_rows"] == 0
    assert files["sales_train_evaluation.csv"]["missingness_summary"]["missing_cells"] == 0


def test_calibration_unit_and_split_were_frozen_before_test() -> None:
    unit = load_json("calibration_unit_decision.json")
    split = load_json("temporal_split.json")
    assert unit["decision_frozen_before_test_demand_read"] is True
    assert unit["selected_unit"] == "store x department x week"
    candidates = {row["candidate"]: row for row in unit["candidates"]}
    assert candidates["store x item x day"]["zero_demand_rate"] > 0.68
    assert candidates["store x department x week"]["zero_demand_rate"] == 0
    assert split["rule_frozen_before_test_demand_read"] is True
    assert split["splits"]["TRAIN"]["week_count"] == 204
    assert split["splits"]["VALIDATION"]["week_count"] == 51
    assert split["splits"]["TEST"]["week_count"] == 20


def test_causal_baseline_helpers_shift_before_rolling_and_use_no_epsilon() -> None:
    weeks = pd.DataFrame({
        "store_id": ["CA_1"] * 14,
        "dept_id": ["FOODS_1"] * 14,
        "wm_yr_wk": list(range(11101, 11115)),
        "week_start": pd.date_range("2011-01-01", periods=14, freq="7D"),
        "actual_demand": np.arange(1.0, 15.0),
    })
    result = add_causal_baselines(weeks)
    assert result.loc[12, "B2_MEAN_12"] == np.mean(np.arange(1.0, 13.0))
    assert result.loc[12, "B3_MEDIAN_12"] == np.median(np.arange(1.0, 13.0))
    uplift = add_uplift(result, "B1_LAG_1")
    assert MINIMUM_POSITIVE_BASELINE == 1.0
    assert np.isclose(uplift.loc[1, "uplift"], 1.0)


def test_quantiles_bootstrap_and_heldout_coverage_are_exactly_audited() -> None:
    quantiles = pd.read_csv(ARTIFACTS / "empirical_quantiles.csv")
    primary = quantiles[quantiles["proxy_stratum"] == "M5_GENERIC_EVENT_PROXY"]
    assert primary["sample_count"].nunique() == 1
    assert primary["sample_count"].iloc[0] == 8961
    assert np.all(np.diff(primary.sort_values("quantile_level")["quantile_value"]) >= 0)
    bootstrap = pd.read_csv(ARTIFACTS / "quantile_bootstrap_ci.csv")
    assert len(bootstrap) == 3
    assert bootstrap["bootstrap_repetitions"].eq(10000).all()
    coverage = pd.read_csv(ARTIFACTS / "heldout_coverage.csv")
    primary_coverage = coverage[coverage["proxy_stratum"] == "M5_GENERIC_EVENT_PROXY"]
    assert primary_coverage["test_n"].eq(742).all()
    assert primary_coverage["absolute_coverage_error"].le(COVERAGE_TOLERANCE).all()
    assert coverage_direction(0.89, 0.90) == "UNDER_COVERAGE"
    assert coverage_direction(0.91, 0.90) == "OVER_COVERAGE"


def test_all_six_gates_pass_without_ml_optimizer_or_genai() -> None:
    gates = load_json("gate_results.json")
    execution = load_json("execution_audit.json")
    ml_gate = load_json("ml_necessity_gate.json")
    assert gates["status"] == "STAGE_4_DEMAND_CALIBRATION_VALIDATION_PASS"
    assert len(gates["gates"]) == 6
    assert all(row["pass"] for row in gates["gates"])
    assert execution["cross_domain_calibration_status"] == "SUPPORTED"
    assert execution["calibration_model_status"] == "SIMPLE_EMPIRICAL_QUANTILE_RETAINED"
    assert execution["optimizer_dispatches"] == 0
    assert execution["genai_dispatches"] == 0
    assert execution["ml_training_dispatches"] == 0
    assert execution["route_risk_recalibrated"] is False
    assert execution["universal_parameter_claim"] is False
    assert ml_gate["classification"] == "SIMPLE_CALIBRATION_RETAINED"
    assert ml_gate["ml_needed"] is False
    assert not (ARTIFACTS / "conditional_ml_results.csv").exists()


def test_cross_domain_comparison_does_not_require_parameter_equality() -> None:
    comparison = pd.read_csv(ARTIFACTS / "favorita_vs_m5_quantile_comparison.csv")
    assert comparison["quantile_level"].tolist() == [0.75, 0.90, 0.95]
    assert comparison["same_method"].all()
    assert (comparison["favorita_value"] != comparison["m5_value"]).all()
    assert comparison["interpretation"].str.contains("equality not required").all()


def test_prior_artifacts_and_frozen_paper2_are_unchanged() -> None:
    changed = subprocess.check_output(
        [
            "git", "diff", "--name-only", STAGE3D_COMMIT, "--",
            "artifacts/compound_risk_stage1", "artifacts/compound_risk_stage2",
            "artifacts/risk_impact_calibration_stage3", "artifacts/risk_impact_mapping_stage3c",
            "artifacts/route_risk_calibration_stage3d",
        ], cwd=ROOT, text=True,
    ).splitlines()
    assert changed == []
    execution = load_json("execution_audit.json")
    assert all(execution["prior_artifacts_unchanged"].values())
    assert execution["prior_artifact_hashes_before"] == execution["prior_artifact_hashes_after"]
    assert paper2_git("rev-parse", "HEAD") == PAPER2_SHA
    assert paper2_git("status", "--porcelain") == ""
