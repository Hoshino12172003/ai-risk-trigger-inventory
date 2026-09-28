from __future__ import annotations

import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from ai_risk_trigger_inventory.calibration.m5_proxy_refinement import (
    MIN_VALID_PRICED_ITEMS,
    PRIMARY_DEPARTMENT_SHARE,
    PRIMARY_ITEM_DISCOUNT,
    add_causal_price_reference,
    aggregate_price_promotion,
    overlap_tables,
)


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "m5_event_proxy_refinement_stage4a"
STAGE4_ARTIFACTS = ROOT / "artifacts" / "m5_demand_calibration_stage4"
STAGE4_COMMIT = "83311de2e412e8f22ae5c142ad163498fade5bdf"
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


def test_v2_price_reference_is_strictly_causal() -> None:
    prices = pd.DataFrame({
        "store_id": ["CA_1"] * 13,
        "item_id": ["ITEM_1"] * 13,
        "dept_id": ["FOODS_1"] * 13,
        "wm_yr_wk": range(1, 14),
        "sell_price": np.arange(1.0, 14.0),
    })
    result = add_causal_price_reference(prices)
    assert result.loc[11, "price_reference_12"] != result.loc[11, "price_reference_12"]
    assert result.loc[12, "price_reference_12"] == 6.5
    assert result.loc[12, "sell_price"] == 13.0


def test_primary_thresholds_and_department_aggregation_are_frozen() -> None:
    assert PRIMARY_ITEM_DISCOUNT == 0.10
    assert PRIMARY_DEPARTMENT_SHARE == 0.10
    assert MIN_VALID_PRICED_ITEMS == 10
    rows = pd.DataFrame({
        "store_id": ["CA_1"] * 10,
        "dept_id": ["FOODS_1"] * 10,
        "wm_yr_wk": [1] * 10,
        "item_id": [f"I{i}" for i in range(10)],
        "valid_price_reference": [True] * 10,
        "discount": [0.10] + [0.0] * 9,
    })
    result = aggregate_price_promotion(
        rows, item_discount=0.10, department_share=0.10
    )
    assert result.loc[0, "promotion_share"] == 0.10
    assert bool(result.loc[0, "price_promotion_event"])


def test_overlap_utility_reports_intersection_union_jaccard_and_conditionals() -> None:
    frame = pd.DataFrame({"a": [1, 1, 0], "b": [1, 0, 1]})
    matrix, conditional, exclusive = overlap_tables(frame, {"A": "a", "B": "b"})
    row = matrix[(matrix["proxy_a"] == "A") & (matrix["proxy_b"] == "B")].iloc[0]
    assert row["intersection_count"] == 1
    assert row["union_count"] == 3
    assert np.isclose(row["jaccard_similarity"], 1 / 3)
    assert len(conditional) == 4
    assert exclusive["atomic_exclusive_count"].tolist() == [1, 1]


def test_all_required_stage4a_artifacts_exist() -> None:
    required = {
        "old_proxy_overlap_matrix.csv", "old_proxy_conditional_overlap.csv",
        "old_proxy_exclusive_counts.csv", "old_promotion_rule_saturation.csv",
        "v2_proxy_definition.csv", "v2_proxy_overlap_matrix.csv",
        "v2_proxy_conditional_overlap.csv", "v2_proxy_exclusive_counts.csv",
        "proxy_definition_sensitivity.csv", "v2_empirical_quantiles.csv",
        "v2_heldout_coverage.csv", "gate_results.json", "execution_audit.json",
    }
    assert {path.name for path in ARTIFACTS.iterdir() if path.is_file()} == required


def test_legacy_proxy_saturation_is_measured_not_reinterpreted() -> None:
    gate = load_json("gate_results.json")
    counts = pd.read_csv(ARTIFACTS / "old_proxy_exclusive_counts.csv").set_index("proxy_name")
    saturation = pd.read_csv(ARTIFACTS / "old_promotion_rule_saturation.csv").iloc[0]
    assert gate["promotion_proxy_saturation"] is True
    assert gate["old_promotion_proxy_status"] == "OVERBROAD_LEGACY_PROXY"
    assert counts.loc["M5_PROMOTION_LIKE", "sample_count"] == 8961
    assert counts.loc["M5_GENERIC_EVENT_PROXY", "sample_count"] == 8961
    assert counts.iloc[0]["calendar_promotion_snap_intersection_count"] == 2210
    assert 0.39 < saturation["labeled_item_week_percentage"] < 0.40
    assert saturation["promotion_field_status"] == "DERIVED_PROXY_NOT_AN_OFFICIAL_M5_PROMOTION_FIELD"


def test_v2_is_distinguishable_and_small_subtypes_are_not_fabricated() -> None:
    gate = load_json("gate_results.json")
    counts = pd.read_csv(ARTIFACTS / "v2_proxy_exclusive_counts.csv").set_index("proxy_name")
    quantiles = pd.read_csv(ARTIFACTS / "v2_empirical_quantiles.csv")
    assert gate["status"] == "STAGE_4A_PROXY_REFINEMENT_PASS"
    assert gate["gate_proxy_distinguishability"] is True
    assert gate["new_price_promotion_proxy_status"] == "DISTINGUISHABLE_DERIVED_PROXY"
    assert counts.loc["M5_PRICE_PROMOTION_EVENT_V2", "sample_count"] == 50
    assert counts.loc["M5_GENERIC_DEMAND_STIMULATION_V2", "sample_count"] == 5918
    price = quantiles[quantiles["proxy_name"] == "M5_PRICE_PROMOTION_EVENT_V2"]
    assert price["quantile_value"].isna().all()
    assert price["status"].eq("INSUFFICIENT_SAMPLE_FOR_QUANTILE").all()


def test_test_coverage_is_diagnostic_only_and_dispatches_are_zero() -> None:
    coverage = pd.read_csv(ARTIFACTS / "v2_heldout_coverage.csv")
    execution = load_json("execution_audit.json")
    assert coverage["interpretation_status"].eq(
        "POST_STAGE4_DIAGNOSTIC_HELDOUT_CHECK"
    ).all()
    assert not coverage["confirmatory_gate"].all()
    assert execution["test_used_for_proxy_selection"] is False
    assert execution["optimizer_dispatches"] == 0
    assert execution["genai_dispatches"] == 0
    assert execution["ml_training_dispatches"] == 0


def test_stage4_and_all_prior_artifacts_and_paper2_are_unchanged() -> None:
    changed = subprocess.check_output(
        [
            "git", "diff", "--name-only", STAGE4_COMMIT, "--",
            "artifacts/compound_risk_stage1", "artifacts/compound_risk_stage2",
            "artifacts/risk_impact_calibration_stage3",
            "artifacts/risk_impact_mapping_stage3c",
            "artifacts/route_risk_calibration_stage3d",
            "artifacts/m5_demand_calibration_stage4",
        ], cwd=ROOT, text=True,
    ).splitlines()
    assert changed == []
    execution = load_json("execution_audit.json")
    assert all(execution["protected_artifacts_unchanged"].values())
    assert execution["protected_hashes_before"] == execution["protected_hashes_after"]
    assert paper2_git("rev-parse", "HEAD") == PAPER2_SHA
    assert paper2_git("status", "--porcelain") == ""
