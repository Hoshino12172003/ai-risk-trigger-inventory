from __future__ import annotations

import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
import pytest

from ai_risk_trigger_inventory.calibration.causal_baselines import (
    BASELINE_MAE_TIE_TOLERANCE,
    EPSILON,
    SPLIT_BOUNDS,
    add_causal_baselines,
    add_uplift,
    assign_chronological_split,
    audit_causal_baselines,
    select_baseline,
)
from ai_risk_trigger_inventory.calibration.ml_gate import (
    ML_NECESSITY_THRESHOLDS,
    ML_VALUE_THRESHOLDS,
    conditional_ml_is_authorized,
)
from ai_risk_trigger_inventory.calibration.statistics import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    bootstrap_quantile_ci,
    empirical_coverage,
    empirical_quantiles,
    sample_status,
)


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "risk_impact_calibration_stage3"
STAGE2_COMMIT = "43755bf1b09550eb2a98096b1b7bd00a79a6a710"
PAPER2_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
PAPER2 = Path(
    r"C:\Users\Hu Jiaxin\Documents\Codex\2026-09-20\files-pasted-by-the-user-github\work\budget-inventory-benders-frozen-51aebd0"
)


def _weekly_series(periods: int = 60) -> pd.DataFrame:
    starts = pd.date_range("2015-01-05", periods=periods, freq="7D")
    return pd.DataFrame({
        "store_nbr": 1,
        "family": "FAMILY",
        "week_start": starts,
        "week_end": starts + pd.Timedelta(days=6),
        "actual_demand": np.arange(1, periods + 1, dtype=float),
        "holiday_any": False,
        "promotion_active": False,
    })


def test_time_split_is_chronological_nonoverlapping_and_fixed() -> None:
    frame = assign_chronological_split(_weekly_series())
    assert SPLIT_BOUNDS["TRAIN"][1] < SPLIT_BOUNDS["VALIDATION"][0]
    assert SPLIT_BOUNDS["VALIDATION"][1] < SPLIT_BOUNDS["TEST"][0]
    assigned = frame[frame["split"] != "EXCLUDED_BOUNDARY"]
    for split, (first, last) in SPLIT_BOUNDS.items():
        part = assigned[assigned["split"] == split]
        assert ((part["week_start"] >= first) & (part["week_end"] <= last)).all()


def test_rolling_baselines_are_shifted_not_centered_or_future_looking() -> None:
    frame = add_causal_baselines(_weekly_series())
    assert frame.loc[4, "B1_MEDIAN_4"] == pytest.approx(2.5)
    assert frame.loc[4, "B1_MEDIAN_4"] != np.median(frame.loc[1:4, "actual_demand"])
    assert frame.loc[52, "B4_LAG_52"] == frame.loc[0, "actual_demand"]
    audit = audit_causal_baselines(frame)
    assert audit.passed
    assert audit.centered_rolling_used is False
    assert audit.future_source_rows == 0


def test_uplift_formula_and_near_zero_baseline_handling() -> None:
    frame = pd.DataFrame({"actual_demand": [140.0, 1.0, 2.0], "B": [100.0, 0.0, EPSILON / 2]})
    result = add_uplift(frame, "B")
    assert result.loc[0, "uplift"] == pytest.approx(0.40)
    assert result.loc[0, "uplift_valid"]
    assert result.loc[1:, "uplift"].isna().all()
    assert not result.loc[1:, "uplift_valid"].any()


def test_baseline_selector_uses_validation_only_and_frozen_tie_break() -> None:
    validation = pd.DataFrame({
        "split": ["VALIDATION"] * 120,
        "holiday_any": False,
        "promotion_active": False,
        "actual_demand": 10.0,
        "B1_MEDIAN_4": 9.0,
        "B2_MEDIAN_8": 9.0 + BASELINE_MAE_TIE_TOLERANCE / 2,
        "B3_MEDIAN_12": 8.0,
        "B4_LAG_52": 7.0,
    })
    test = validation.iloc[:10].copy()
    test["split"] = "TEST"
    test["actual_demand"] = 1_000_000.0
    selected, comparison = select_baseline(pd.concat([validation, test], ignore_index=True))
    assert selected == "B1_MEDIAN_4"
    assert comparison["test_rows_used_for_selection"].eq(0).all()


def test_empirical_quantiles_are_deterministic_ordered_and_sample_gated() -> None:
    values = np.arange(1.0, 101.0)
    first = empirical_quantiles(values)
    second = empirical_quantiles(values)
    assert first == second
    assert list(first.values()) == sorted(first.values())
    assert all(np.isnan(value) for value in empirical_quantiles(values[:29]).values())
    assert sample_status(29) == "INSUFFICIENT_SAMPLE"
    assert sample_status(30) == "EXPLORATORY_GROUP_QUANTILE"
    assert sample_status(100) == "STABLE_GROUP_SUMMARY"


def test_bootstrap_and_coverage_are_deterministic() -> None:
    values = np.linspace(0.0, 1.0, 100)
    first = bootstrap_quantile_ci(values, 0.90, seed=BOOTSTRAP_SEED, resamples=50)
    second = bootstrap_quantile_ci(values, 0.90, seed=BOOTSTRAP_SEED, resamples=50)
    assert first == second
    assert BOOTSTRAP_RESAMPLES == 1000
    assert empirical_coverage([0.1, 0.2, 0.3, 0.4], 0.25) == 0.5


def test_ml_gates_and_execution_authorization_are_frozen() -> None:
    assert ML_NECESSITY_THRESHOLDS == {
        "median_group_absolute_q90_coverage_error": 0.05,
        "fraction_groups_absolute_error_over_0_10": 0.25,
        "large_group_error": 0.10,
        "group_q90_iqr": 0.20,
        "minimum_test_group_size": 30,
    }
    assert ML_VALUE_THRESHOLDS == {
        "overall_q90_absolute_error_improvement": 0.02,
        "median_group_q90_absolute_error_improvement": 0.02,
        "maximum_q95_absolute_error_worsening": 0.02,
    }
    assert conditional_ml_is_authorized("ML_NEEDED_FOR_CONDITIONAL_CALIBRATION")
    assert not conditional_ml_is_authorized("SIMPLE_QUANTILES_ADEQUATE")


def test_test_set_is_not_used_for_selection_or_tuning() -> None:
    baseline = pd.read_csv(ARTIFACTS / "baseline_model_comparison.csv")
    ml = pd.read_csv(ARTIFACTS / "conditional_ml_results.csv")
    assert baseline["test_rows_used_for_selection"].eq(0).all()
    assert ml["selected_model_family_on_validation"].any()
    assert ml["feature_timing"].str.contains("past_only", regex=False).all()
    protocol = (ROOT / "docs" / "risk_impact_calibration_stage3_protocol.md").read_text(encoding="utf-8")
    assert "TEST is unavailable to" in protocol
    assert "TEST is used once for final evaluation" in protocol


def test_run_audit_confirms_causality_and_zero_optimizer_dispatches() -> None:
    audit = json.loads((ARTIFACTS / "execution_audit.json").read_text(encoding="utf-8"))
    assert audit["status"] == "STAGE_3A_STATISTICAL_CALIBRATION_PASS"
    assert audit["leakage_audit"]["passed"] is True
    assert audit["leakage_audit"]["future_source_rows"] == 0
    assert audit["optimizer_dispatches"] == 0
    assert audit["final_demand_calibration_classification"] == "STAGE_3_SIMPLE_CALIBRATION_PREFERRED"


def test_ml_was_gated_and_crossing_was_reported_without_postprocessing() -> None:
    gate = json.loads((ARTIFACTS / "ml_necessity_gate.json").read_text(encoding="utf-8"))
    results = pd.read_csv(ARTIFACTS / "conditional_ml_results.csv")
    assert gate["ml_needed"] is True
    assert gate["stage3b_executed"] is True
    assert gate["ml_value_gate"]["supported"] is False
    assert results["quantile_crossing_count"].ge(0).all()
    assert not results["postprocessing_applied"].any()


def test_candidate_statuses_and_route_provenance_are_valid() -> None:
    candidates = pd.read_csv(ARTIFACTS / "calibration_candidates.csv")
    allowed = {
        "CANDIDATE_DATA_SUPPORTED", "CANDIDATE_ML_SUPPORTED", "STRESS_TEST_ONLY",
        "INSUFFICIENT_SAMPLE", "PENDING_EXTERNAL_EVIDENCE",
    }
    assert set(candidates["status"]) <= allowed
    assert "FINAL_CALIBRATION" not in set(candidates["status"])
    route = candidates[candidates["component"] == "ROUTE_SERVICE_LOSS"]
    favorita_route = route[route["data_source"].str.contains("Favorita", na=False)]
    assert favorita_route.empty
    assert set(route[route["value"].notna()]["status"]) == {"STRESS_TEST_ONLY"}
    assert set(route[route["value"].isna()]["status"]) == {"PENDING_EXTERNAL_EVIDENCE"}


def test_provenance_matrix_has_required_boundaries() -> None:
    provenance = pd.read_csv(ARTIFACTS / "provenance_matrix.csv")
    assert {"OBSERVED", "DERIVED", "CALIBRATED_NOT_OBSERVED", "PENDING_EXTERNAL_EVIDENCE"} <= set(provenance["category"])
    route = provenance[provenance["item"].str.contains("route", case=False)]
    assert not route["category"].eq("OBSERVED").any()


def test_no_deep_learning_dependency_or_optimizer_call_was_introduced() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    code = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for directory in (
            ROOT / "src" / "ai_risk_trigger_inventory" / "calibration",
            ROOT / "experiments" / "risk_impact_calibration_stage3",
        )
        for path in directory.glob("*.py")
    )
    for dependency in ("torch", "tensorflow", "keras", "transformers", "xgboost"):
        assert dependency not in pyproject
    for forbidden in ("gurobipy", "solve_prb", "column_and_constraint", "openai"):
        assert forbidden not in code


def test_stage1_and_stage2_artifacts_are_unchanged() -> None:
    changed = subprocess.check_output(
        [
            "git", "diff", "--name-only", STAGE2_COMMIT, "--",
            "artifacts/compound_risk_stage1", "artifacts/compound_risk_stage2",
        ],
        cwd=ROOT,
        text=True,
    ).splitlines()
    assert changed == []
    audit = json.loads((ARTIFACTS / "execution_audit.json").read_text(encoding="utf-8"))
    assert audit["prior_artifact_hashes_before"] == audit["prior_artifact_hashes_after"]


def test_paper2_frozen_checkout_is_untouched() -> None:
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=PAPER2, text=True).strip() == PAPER2_SHA
    assert subprocess.check_output(["git", "status", "--porcelain"], cwd=PAPER2, text=True).strip() == ""
