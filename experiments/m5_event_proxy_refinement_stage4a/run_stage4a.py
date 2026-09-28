"""Audit legacy M5 proxy saturation and build the preregistered Stage-4A V2."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from experiments.m5_demand_calibration_stage4.run_stage4 import (
    PAPER2_SHA,
    add_context,
    aggregate_demand,
    directory_hashes,
    paper2_git,
    price_context,
    week_table,
    write_csv,
)
from ai_risk_trigger_inventory.calibration.m5_validation import (
    BASELINE_ORDER,
    QUANTILES,
    add_causal_baselines,
    add_uplift,
    coverage_direction,
)
from ai_risk_trigger_inventory.calibration.m5_proxy_refinement import (
    MIN_SATURATION_REDUCTION,
    MIN_VALID_PRICED_ITEMS,
    NEAR_EQUAL_COUNT_TOLERANCE,
    PAIRWISE_DISTINGUISHABILITY_JACCARD,
    PRIMARY_DEPARTMENT_SHARE,
    PRIMARY_ITEM_DISCOUNT,
    SATURATION_CONDITIONAL_THRESHOLD,
    SENSITIVITY_DEPARTMENT_SHARES,
    SENSITIVITY_ITEM_DISCOUNTS,
    add_causal_price_reference,
    aggregate_price_promotion,
    conditional_value,
    overlap_tables,
)


STAGE4_COMMIT = "83311de2e412e8f22ae5c142ad163498fade5bdf"
STAGE4_ARTIFACTS = ROOT / "artifacts" / "m5_demand_calibration_stage4"
OUTPUT = ROOT / "artifacts" / "m5_event_proxy_refinement_stage4a"
OLD_PROXY_COLUMNS = {
    "M5_CALENDAR_EVENT": "calendar_event",
    "M5_SNAP_CONTEXT": "snap_active",
    "M5_PROMOTION_LIKE": "promotion_like",
    "M5_EVENT_PROMOTION_OVERLAP": "event_promotion_overlap",
    "M5_GENERIC_EVENT_PROXY": "generic_event_proxy",
}
V2_PROXY_COLUMNS = {
    "M5_CALENDAR_EVENT_V2": "calendar_event_v2",
    "M5_SNAP_CONTEXT_V2": "snap_context_v2",
    "M5_PRICE_PROMOTION_EVENT_V2": "price_promotion_event_v2",
    "M5_CALENDAR_PRICE_OVERLAP_V2": "calendar_price_overlap_v2",
    "M5_GENERIC_DEMAND_STIMULATION_V2": "generic_demand_stimulation_v2",
}


def add_v2_context(
    panel: pd.DataFrame, weeks: pd.DataFrame, price_proxy: pd.DataFrame
) -> pd.DataFrame:
    frame = panel.merge(
        weeks[["wm_yr_wk", "calendar_event", "snap_CA_days", "snap_TX_days", "snap_WI_days"]],
        on="wm_yr_wk", how="left", validate="many_to_one",
    ).merge(
        price_proxy,
        on=["store_id", "dept_id", "wm_yr_wk"], how="left", validate="one_to_one",
    )
    frame["price_promotion_event"] = frame["price_promotion_event"].fillna(False).astype(bool)
    snap_column = "snap_" + frame["state_id"].astype(str) + "_days"
    frame["snap_context_v2"] = np.fromiter(
        (frame.iloc[index][column] >= 4 for index, column in enumerate(snap_column)),
        dtype=bool, count=len(frame),
    )
    frame["calendar_event_v2"] = frame["calendar_event"].astype(bool)
    frame["price_promotion_event_v2"] = frame["price_promotion_event"]
    frame["calendar_price_overlap_v2"] = (
        frame["calendar_event_v2"] & frame["price_promotion_event_v2"]
    )
    frame["generic_demand_stimulation_v2"] = (
        frame["calendar_event_v2"]
        | frame["price_promotion_event_v2"]
        | frame["snap_context_v2"]
    )
    return frame


def positive_proxy_sample(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[frame["uplift_eligible"] & (frame["uplift"] > 0)].copy()


def old_promotion_saturation(prices: pd.DataFrame, train_weeks: set[int]) -> pd.DataFrame:
    reference = prices[prices["wm_yr_wk"].isin(train_weeks)].groupby(
        ["store_id", "item_id"], sort=True, observed=True
    )["sell_price"].max().rename("train_maximum_price")
    frame = prices.join(reference, on=["store_id", "item_id"])
    frame["old_promotion_like"] = frame["sell_price"] < frame["train_maximum_price"]
    frame["non_max_week"] = frame["sell_price"] < frame.groupby(
        ["store_id", "item_id"], sort=False, observed=True
    )["sell_price"].transform("max")
    grouped = frame.groupby(["store_id", "item_id"], sort=True, observed=True).agg(
        priced_weeks=("wm_yr_wk", "size"),
        labeled_weeks=("old_promotion_like", "sum"),
        non_max_weeks=("non_max_week", "sum"),
    )
    grouped["labeled_fraction"] = grouped["labeled_weeks"] / grouped["priced_weeks"]
    grouped["labeled_non_max_fraction"] = np.where(
        grouped["non_max_weeks"] > 0,
        grouped["labeled_weeks"] / grouped["non_max_weeks"],
        np.nan,
    )
    fractions = grouped["labeled_fraction"]
    return pd.DataFrame([{
        "rule": "sell_price < store-item TRAIN maximum price",
        "audit_scope": "TRAIN+VALIDATION priced item-weeks",
        "priced_item_week_count": len(frame),
        "labeled_item_week_count": int(frame["old_promotion_like"].sum()),
        "labeled_item_week_percentage": float(frame["old_promotion_like"].mean()),
        "store_item_count": len(grouped),
        "labeled_fraction_p25": float(fractions.quantile(0.25)),
        "labeled_fraction_p50": float(fractions.quantile(0.50)),
        "labeled_fraction_p75": float(fractions.quantile(0.75)),
        "labeled_fraction_p90": float(fractions.quantile(0.90)),
        "store_items_over_80pct_labeled": int((fractions > 0.80).sum()),
        "store_items_all_nonmax_weeks_labeled": int(
            np.isclose(grouped["labeled_non_max_fraction"], 1.0, rtol=0, atol=1e-12).sum()
        ),
        "promotion_field_status": "DERIVED_PROXY_NOT_AN_OFFICIAL_M5_PROMOTION_FIELD",
    }])


def quantile_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for proxy_name, column in V2_PROXY_COLUMNS.items():
        sample = frame[frame[column] & frame["uplift_eligible"] & (frame["uplift"] > 0)]
        if len(sample) < 100:
            for quantile in QUANTILES:
                rows.append({
                    "domain": "M5",
                    "proxy_name": proxy_name,
                    "quantile_level": quantile,
                    "quantile_value": np.nan,
                    "sample_count": len(sample),
                    "result_label": "STAGE4A_PROXY_REFINED_CALIBRATION",
                    "status": "INSUFFICIENT_SAMPLE_FOR_QUANTILE",
                    "stage4_original_overwritten": False,
                })
            continue
        for quantile in QUANTILES:
            rows.append({
                "domain": "M5",
                "proxy_name": proxy_name,
                "quantile_level": quantile,
                "quantile_value": float(np.quantile(sample["uplift"], quantile, method="linear")),
                "sample_count": len(sample),
                "result_label": "STAGE4A_PROXY_REFINED_CALIBRATION",
                "status": "ESTIMATED",
                "stage4_original_overwritten": False,
            })
    return pd.DataFrame(rows).sort_values(["proxy_name", "quantile_level"]).reset_index(drop=True)


def coverage_table(quantiles: pd.DataFrame, test: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for row in quantiles.itertuples(index=False):
        if not np.isfinite(row.quantile_value):
            continue
        column = V2_PROXY_COLUMNS[row.proxy_name]
        sample = test[test[column] & test["uplift_eligible"] & (test["uplift"] > 0)]
        observed = float((sample["uplift"] <= row.quantile_value).mean()) if len(sample) else np.nan
        rows.append({
            "proxy_name": row.proxy_name,
            "target_quantile": row.quantile_level,
            "calibrated_value": row.quantile_value,
            "test_n": len(sample),
            "observed_coverage": observed,
            "coverage_direction": coverage_direction(observed, row.quantile_level),
            "absolute_coverage_error": abs(observed - row.quantile_level),
            "interpretation_status": "POST_STAGE4_DIAGNOSTIC_HELDOUT_CHECK",
            "confirmatory_gate": False,
        })
    return pd.DataFrame(rows).sort_values(["proxy_name", "target_quantile"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--paper2-checkout", required=True, type=Path)
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)

    if paper2_git(args.paper2_checkout, "rev-parse", "HEAD") != PAPER2_SHA:
        raise SystemExit("STOP: Paper-2 frozen SHA changed")
    if paper2_git(args.paper2_checkout, "status", "--porcelain"):
        raise SystemExit("STOP: Paper-2 frozen checkout is not clean")

    protected_dirs = {
        "stage1": ROOT / "artifacts" / "compound_risk_stage1",
        "stage2": ROOT / "artifacts" / "compound_risk_stage2",
        "stage3a": ROOT / "artifacts" / "risk_impact_calibration_stage3",
        "stage3c": ROOT / "artifacts" / "risk_impact_mapping_stage3c",
        "stage3d": ROOT / "artifacts" / "route_risk_calibration_stage3d",
        "stage4": STAGE4_ARTIFACTS,
    }
    hashes_before = {name: directory_hashes(path) for name, path in protected_dirs.items()}

    calendar = pd.read_csv(args.data_dir / "calendar.csv", parse_dates=["date"])
    sales_path = args.data_dir / "sales_train_evaluation.csv"
    demand_columns = [
        column for column in pd.read_csv(sales_path, nrows=0).columns
        if column.startswith("d_")
    ]
    weeks = week_table(calendar, demand_columns)
    metadata = pd.read_csv(
        sales_path, usecols=["item_id", "dept_id", "cat_id", "store_id", "state_id"]
    )
    pre_weeks = weeks[weeks["split"].isin(["TRAIN", "VALIDATION"])].copy()
    pre_panel, _ = aggregate_demand(sales_path, metadata, pre_weeks, collect_item_stats=False)

    legacy_prices = price_context(args.data_dir / "sell_prices.csv", metadata, weeks)
    legacy_pre = add_context(pre_panel, weeks, legacy_prices)
    legacy_pre = add_uplift(add_causal_baselines(legacy_pre), "B1_LAG_1")
    positive_legacy = positive_proxy_sample(legacy_pre)
    old_matrix, old_conditional, old_exclusive = overlap_tables(
        positive_legacy, OLD_PROXY_COLUMNS
    )
    old_exclusive["calendar_promotion_intersection_count"] = int((
        positive_legacy["calendar_event"] & positive_legacy["promotion_like"]
    ).sum())
    old_exclusive["calendar_snap_intersection_count"] = int((
        positive_legacy["calendar_event"] & positive_legacy["snap_active"]
    ).sum())
    old_exclusive["promotion_snap_intersection_count"] = int((
        positive_legacy["promotion_like"] & positive_legacy["snap_active"]
    ).sum())
    old_exclusive["calendar_promotion_snap_intersection_count"] = int((
        positive_legacy["calendar_event"]
        & positive_legacy["promotion_like"]
        & positive_legacy["snap_active"]
    ).sum())
    write_csv(OUTPUT / "old_proxy_overlap_matrix.csv", old_matrix)
    write_csv(OUTPUT / "old_proxy_conditional_overlap.csv", old_conditional)
    write_csv(OUTPUT / "old_proxy_exclusive_counts.csv", old_exclusive)

    item_map = metadata[["item_id", "dept_id"]].drop_duplicates("item_id")
    valid_weeks = set(weeks["wm_yr_wk"].astype(int))
    raw_prices = pd.read_csv(
        args.data_dir / "sell_prices.csv",
        dtype={"store_id": "category", "item_id": "category", "wm_yr_wk": "int32", "sell_price": "float32"},
    )
    raw_prices = raw_prices[raw_prices["wm_yr_wk"].isin(valid_weeks)].merge(
        item_map, on="item_id", how="left", validate="many_to_one"
    )
    train_validation_weeks = set(pre_weeks["wm_yr_wk"].astype(int))
    old_saturation = old_promotion_saturation(
        raw_prices[raw_prices["wm_yr_wk"].isin(train_validation_weeks)].copy(),
        set(weeks.loc[weeks["split"] == "TRAIN", "wm_yr_wk"].astype(int)),
    )
    write_csv(OUTPUT / "old_promotion_rule_saturation.csv", old_saturation)

    priced = add_causal_price_reference(raw_prices)
    primary_price = aggregate_price_promotion(
        priced,
        item_discount=PRIMARY_ITEM_DISCOUNT,
        department_share=PRIMARY_DEPARTMENT_SHARE,
    )
    sensitivity_rows = []
    for item_threshold in SENSITIVITY_ITEM_DISCOUNTS:
        for share_threshold in SENSITIVITY_DEPARTMENT_SHARES:
            candidate = aggregate_price_promotion(
                priced,
                item_discount=item_threshold,
                department_share=share_threshold,
            )
            candidate = candidate[candidate["wm_yr_wk"].isin(train_validation_weeks)]
            merged = pre_panel[["store_id", "dept_id", "wm_yr_wk"]].merge(
                candidate, on=["store_id", "dept_id", "wm_yr_wk"], how="left", validate="one_to_one"
            )
            active = merged["price_promotion_event"].fillna(False).astype(bool)
            sensitivity_rows.append({
                "item_discount_threshold": item_threshold,
                "department_share_threshold": share_threshold,
                "minimum_valid_priced_items": MIN_VALID_PRICED_ITEMS,
                "train_validation_department_week_count": len(merged),
                "eligible_price_history_department_week_count": int(merged["valid_priced_item_count"].notna().sum()),
                "promotion_department_week_count": int(active.sum()),
                "promotion_department_week_rate": float(active.mean()),
                "is_preregistered_primary_rule": item_threshold == PRIMARY_ITEM_DISCOUNT
                and share_threshold == PRIMARY_DEPARTMENT_SHARE,
                "test_used_for_rule_selection": False,
            })
    write_csv(OUTPUT / "proxy_definition_sensitivity.csv", sensitivity_rows)

    v2_pre = add_v2_context(pre_panel, weeks, primary_price)
    v2_pre = add_uplift(add_causal_baselines(v2_pre), "B1_LAG_1")
    positive_v2 = positive_proxy_sample(v2_pre)
    v2_matrix, v2_conditional, v2_exclusive = overlap_tables(
        positive_v2, V2_PROXY_COLUMNS
    )
    v2_exclusive["calendar_price_intersection_count"] = int((
        positive_v2["calendar_event_v2"] & positive_v2["price_promotion_event_v2"]
    ).sum())
    v2_exclusive["calendar_snap_intersection_count"] = int((
        positive_v2["calendar_event_v2"] & positive_v2["snap_context_v2"]
    ).sum())
    v2_exclusive["price_snap_intersection_count"] = int((
        positive_v2["price_promotion_event_v2"] & positive_v2["snap_context_v2"]
    ).sum())
    v2_exclusive["calendar_price_snap_intersection_count"] = int((
        positive_v2["calendar_event_v2"]
        & positive_v2["price_promotion_event_v2"]
        & positive_v2["snap_context_v2"]
    ).sum())
    write_csv(OUTPUT / "v2_proxy_overlap_matrix.csv", v2_matrix)
    write_csv(OUTPUT / "v2_proxy_conditional_overlap.csv", v2_conditional)
    write_csv(OUTPUT / "v2_proxy_exclusive_counts.csv", v2_exclusive)

    definitions = [
        {
            "proxy_name": "M5_CALENDAR_EVENT_V2",
            "source_fields": "event_name_1,event_type_1,event_name_2,event_type_2",
            "construction_rule": "same frozen weekly calendar-event definition as Stage 4",
            "direct_or_derived": "DIRECT_CONTEXT",
            "economic_interpretation": "calendar-based external demand context",
            "provenance": "CALENDAR_OBSERVED_CONTEXT",
            "sample_count": int(positive_v2["calendar_event_v2"].sum()),
        },
        {
            "proxy_name": "M5_PRICE_PROMOTION_EVENT_V2",
            "source_fields": "sell_price,store_id,item_id,dept_id,wm_yr_wk",
            "construction_rule": "shift(1) 12-available-week median; item discount >=10%; department promoted share >=10%; valid items >=10",
            "direct_or_derived": "DERIVED",
            "economic_interpretation": "price-based promotion-like demand context",
            "provenance": "PRICE_DERIVED_PROMOTION_PROXY",
            "sample_count": int(positive_v2["price_promotion_event_v2"].sum()),
        },
        {
            "proxy_name": "M5_SNAP_CONTEXT_V2",
            "source_fields": "snap_CA,snap_TX,snap_WI,state_id",
            "construction_rule": "same frozen state-specific majority-week SNAP definition as Stage 4",
            "direct_or_derived": "DIRECT_CONTEXT",
            "economic_interpretation": "state-level SNAP context; not promotion or holiday",
            "provenance": "SNAP_OBSERVED_CONTEXT",
            "sample_count": int(positive_v2["snap_context_v2"].sum()),
        },
        {
            "proxy_name": "M5_CALENDAR_PRICE_OVERLAP_V2",
            "source_fields": "calendar fields,sell_price",
            "construction_rule": "calendar event AND V2 price-promotion event",
            "direct_or_derived": "DERIVED",
            "economic_interpretation": "calendar and price-promotion context overlap",
            "provenance": "DERIVED_CONTEXT_OVERLAP",
            "sample_count": int(positive_v2["calendar_price_overlap_v2"].sum()),
        },
        {
            "proxy_name": "M5_GENERIC_DEMAND_STIMULATION_V2",
            "source_fields": "calendar fields,sell_price,SNAP fields",
            "construction_rule": "calendar OR V2 price promotion OR SNAP",
            "direct_or_derived": "DERIVED",
            "economic_interpretation": "broader external demand-stimulation context",
            "provenance": "GENERIC_DERIVED_DEMAND_STIMULATION",
            "sample_count": int(positive_v2["generic_demand_stimulation_v2"].sum()),
        },
    ]
    write_csv(OUTPUT / "v2_proxy_definition.csv", definitions)

    quantiles = quantile_table(v2_pre)
    write_csv(OUTPUT / "v2_empirical_quantiles.csv", quantiles)

    # Rules and all TRAIN+VALIDATION audits are frozen before TEST demand is aggregated.
    test_weeks = weeks[weeks["split"] == "TEST"].copy()
    test_panel, _ = aggregate_demand(sales_path, metadata, test_weeks, collect_item_stats=False)
    combined = pd.concat([
        v2_pre.drop(columns=list(BASELINE_ORDER) + ["baseline_demand", "uplift", "uplift_eligible"]),
        add_v2_context(test_panel, weeks, primary_price),
    ])
    combined = add_uplift(add_causal_baselines(combined), "B1_LAG_1")
    coverage = coverage_table(quantiles, combined[combined["split"] == "TEST"])
    write_csv(OUTPUT / "v2_heldout_coverage.csv", coverage)

    old_price_given_calendar = conditional_value(
        old_conditional, "M5_PROMOTION_LIKE", "M5_CALENDAR_EVENT"
    )
    old_price_given_snap = conditional_value(
        old_conditional, "M5_PROMOTION_LIKE", "M5_SNAP_CONTEXT"
    )
    old_price_given_generic = conditional_value(
        old_conditional, "M5_PROMOTION_LIKE", "M5_GENERIC_EVENT_PROXY"
    )
    old_generic_count = int(positive_legacy["generic_event_proxy"].sum())
    old_price_count = int(positive_legacy["promotion_like"].sum())
    old_near_equal = abs(old_generic_count - old_price_count) / max(old_generic_count, 1) <= NEAR_EQUAL_COUNT_TOLERANCE
    old_saturation_flag = any([
        old_price_given_calendar >= SATURATION_CONDITIONAL_THRESHOLD,
        old_price_given_snap >= SATURATION_CONDITIONAL_THRESHOLD,
        old_price_given_generic >= SATURATION_CONDITIONAL_THRESHOLD,
        old_near_equal,
    ])

    v2_price_given_calendar = conditional_value(
        v2_conditional, "M5_PRICE_PROMOTION_EVENT_V2", "M5_CALENDAR_EVENT_V2"
    )
    v2_price_given_snap = conditional_value(
        v2_conditional, "M5_PRICE_PROMOTION_EVENT_V2", "M5_SNAP_CONTEXT_V2"
    )
    v2_price_given_generic = conditional_value(
        v2_conditional, "M5_PRICE_PROMOTION_EVENT_V2", "M5_GENERIC_DEMAND_STIMULATION_V2"
    )
    v2_generic_count = int(positive_v2["generic_demand_stimulation_v2"].sum())
    v2_price_count = int(positive_v2["price_promotion_event_v2"].sum())
    v2_not_near_equal = abs(v2_generic_count - v2_price_count) / max(v2_generic_count, 1) > NEAR_EQUAL_COUNT_TOLERANCE
    atomic_names = [
        "M5_CALENDAR_EVENT_V2", "M5_PRICE_PROMOTION_EVENT_V2", "M5_SNAP_CONTEXT_V2"
    ]
    atomic_jaccards = v2_matrix[
        v2_matrix["proxy_a"].isin(atomic_names)
        & v2_matrix["proxy_b"].isin(atomic_names)
        & (v2_matrix["proxy_a"] != v2_matrix["proxy_b"])
    ]["jaccard_similarity"]
    old_max = max(old_price_given_calendar, old_price_given_snap, old_price_given_generic)
    v2_max = max(v2_price_given_calendar, v2_price_given_snap, v2_price_given_generic)
    distinguishability_pass = bool(
        (atomic_jaccards < PAIRWISE_DISTINGUISHABILITY_JACCARD).all()
        and v2_not_near_equal
        and old_max - v2_max >= MIN_SATURATION_REDUCTION
    )
    if old_saturation_flag and distinguishability_pass:
        classification = "STAGE_4A_PROXY_REFINEMENT_PASS"
        old_status = "OVERBROAD_LEGACY_PROXY"
        new_status = "DISTINGUISHABLE_DERIVED_PROXY"
    elif not old_saturation_flag:
        classification = "STAGE_4A_NO_MAJOR_PROXY_SATURATION"
        old_status = "NO_MAJOR_PROXY_SATURATION"
        new_status = "DERIVED_PROXY_REFINED"
    else:
        classification = "STAGE_4A_PROXY_REFINEMENT_INCONCLUSIVE"
        old_status = "OVERBROAD_LEGACY_PROXY"
        new_status = "PRICE_PROXY_REQUIRES_FURTHER_REFINEMENT"

    gate_results = {
        "status": classification,
        "stage4_status_preserved": "STAGE_4_DEMAND_CALIBRATION_VALIDATION_PASS",
        "cross_domain_calibration_status_preserved": "SUPPORTED",
        "old_promotion_proxy_status": old_status,
        "new_price_promotion_proxy_status": new_status,
        "promotion_proxy_saturation": old_saturation_flag,
        "gate_proxy_distinguishability": distinguishability_pass,
        "frozen_gate_thresholds": {
            "saturation_conditional_threshold": SATURATION_CONDITIONAL_THRESHOLD,
            "near_equal_count_tolerance": NEAR_EQUAL_COUNT_TOLERANCE,
            "pairwise_distinguishability_jaccard": PAIRWISE_DISTINGUISHABILITY_JACCARD,
            "minimum_saturation_reduction": MIN_SATURATION_REDUCTION,
        },
        "old_conditionals": {
            "P(PROMOTION_LIKE|CALENDAR_EVENT)": old_price_given_calendar,
            "P(PROMOTION_LIKE|SNAP_CONTEXT)": old_price_given_snap,
            "P(PROMOTION_LIKE|GENERIC_EVENT_PROXY)": old_price_given_generic,
        },
        "v2_conditionals": {
            "P(PRICE_PROMOTION_V2|CALENDAR)": v2_price_given_calendar,
            "P(PRICE_PROMOTION_V2|SNAP)": v2_price_given_snap,
            "P(PRICE_PROMOTION_V2|GENERIC_DEMAND_STIMULATION_V2)": v2_price_given_generic,
        },
        "old_max_saturation_conditional": old_max,
        "v2_max_saturation_conditional": v2_max,
        "saturation_reduction": old_max - v2_max,
    }
    (OUTPUT / "gate_results.json").write_text(
        json.dumps(gate_results, indent=2) + "\n", encoding="utf-8"
    )

    hashes_after = {name: directory_hashes(path) for name, path in protected_dirs.items()}
    integrity = {name: hashes_before[name] == hashes_after[name] for name in protected_dirs}
    if not all(integrity.values()):
        raise SystemExit(f"STOP: protected artifacts changed: {integrity}")
    execution = {
        "status": classification,
        "base_commit": STAGE4_COMMIT,
        "stage4_scientific_conclusion_preserved": True,
        "stage4_original_artifacts_unchanged": integrity["stage4"],
        "protected_artifacts_unchanged": integrity,
        "protected_hashes_before": hashes_before,
        "protected_hashes_after": hashes_after,
        "selected_calibration_unit": "store x department x week",
        "selected_baseline": "B1_LAG_1",
        "primary_item_discount_threshold": PRIMARY_ITEM_DISCOUNT,
        "primary_department_share_threshold": PRIMARY_DEPARTMENT_SHARE,
        "minimum_valid_priced_items": MIN_VALID_PRICED_ITEMS,
        "test_used_for_proxy_selection": False,
        "test_result_status": "POST_STAGE4_DIAGNOSTIC_HELDOUT_CHECK",
        "optimizer_dispatches": 0,
        "genai_dispatches": 0,
        "ml_training_dispatches": 0,
        "paper2_frozen_sha_before": PAPER2_SHA,
        "paper2_frozen_sha_after": paper2_git(args.paper2_checkout, "rev-parse", "HEAD"),
        "paper2_clean_before": True,
        "paper2_clean_after": not bool(paper2_git(args.paper2_checkout, "status", "--porcelain")),
    }
    (OUTPUT / "execution_audit.json").write_text(
        json.dumps(execution, indent=2) + "\n", encoding="utf-8"
    )
    print(classification)


if __name__ == "__main__":
    main()
