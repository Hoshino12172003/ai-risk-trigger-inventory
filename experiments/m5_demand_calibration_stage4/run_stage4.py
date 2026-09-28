"""Run independent-domain M5 demand-calibration validation without optimization."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_risk_trigger_inventory.calibration.m5_validation import (
    BASELINE_ORDER,
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    COVERAGE_TOLERANCE,
    MINIMUM_POSITIVE_BASELINE,
    MIN_QUANTILE_SAMPLE,
    QUANTILES,
    add_causal_baselines,
    add_uplift,
    audit_baselines,
    bootstrap_quantile,
    coverage_direction,
    select_baseline,
)


PAPER2_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
STAGE3D_COMMIT = "0104de30da0686ee4babf7700738a3bbbec70d30"
PRIMARY_STRATUM = "M5_GENERIC_EVENT_PROXY"
HETEROGENEITY_THRESHOLDS = {
    "median_group_q90_absolute_error": 0.05,
    "fraction_groups_q90_error_over_0_10": 0.25,
    "group_q90_iqr": 0.20,
}
SIMPLE_MISCALIBRATION_RULE = "at least two of q75/q90/q95 absolute coverage errors exceed 0.05"


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def directory_hashes(path: Path) -> dict[str, str]:
    return {
        str(file.relative_to(ROOT)).replace("\\", "/"): digest(file)
        for file in sorted(path.rglob("*")) if file.is_file()
    }


def paper2_git(checkout: Path, *arguments: str) -> str:
    safe_path = str(checkout.resolve()).replace("\\", "/")
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={safe_path}", *arguments],
        cwd=checkout, text=True,
    ).strip()


def write_csv(path: Path, value: pd.DataFrame | list[dict]) -> None:
    frame = value if isinstance(value, pd.DataFrame) else pd.DataFrame(value)
    frame.to_csv(path, index=False, lineterminator="\n")


def week_table(calendar: pd.DataFrame, demand_columns: list[str]) -> pd.DataFrame:
    selected = calendar[calendar["d"].isin(demand_columns)].copy()
    selected["d_number"] = selected["d"].str.removeprefix("d_").astype(int)
    selected = selected.sort_values("d_number")
    rows = []
    for week, group in selected.groupby("wm_yr_wk", sort=True):
        if len(group) != 7:
            continue
        first, last = group["date"].min(), group["date"].max()
        split = "EXCLUDED_BOUNDARY"
        if last <= pd.Timestamp("2014-12-31"):
            split = "TRAIN"
        elif first >= pd.Timestamp("2015-01-01") and last <= pd.Timestamp("2015-12-31"):
            split = "VALIDATION"
        elif first >= pd.Timestamp("2016-01-01"):
            split = "TEST"
        rows.append({
            "wm_yr_wk": int(week), "week_start": first, "week_end": last,
            "split": split, "day_columns": tuple(group["d"]),
            "calendar_event": bool(group[["event_name_1", "event_name_2"]].notna().any(axis=None)),
            "event_names": "|".join(sorted(set(
                group[["event_name_1", "event_name_2"]].stack().dropna().astype(str)
            ))),
            "snap_CA_days": int(group["snap_CA"].sum()),
            "snap_TX_days": int(group["snap_TX"].sum()),
            "snap_WI_days": int(group["snap_WI"].sum()),
        })
    return pd.DataFrame(rows).sort_values("week_start").reset_index(drop=True)


def aggregate_demand(
    sales_path: Path,
    metadata: pd.DataFrame,
    weeks: pd.DataFrame,
    *,
    collect_item_stats: bool,
) -> tuple[pd.DataFrame, dict[str, int]]:
    groups = metadata[["store_id", "dept_id", "cat_id", "state_id"]].drop_duplicates()
    groups = groups.sort_values(["store_id", "dept_id"]).reset_index(drop=True)
    group_index = {(row.store_id, row.dept_id): index for index, row in groups.iterrows()}
    matrix = np.zeros((len(groups), len(weeks)), dtype=np.float64)
    day_columns = [day for days in weeks["day_columns"] for day in days]
    stats = {"observations": 0, "zero": 0, "positive": 0, "entities": len(metadata)}
    offset = 0
    usecols = ["store_id", "dept_id", *day_columns]
    for chunk in pd.read_csv(sales_path, usecols=usecols, chunksize=500):
        values = chunk[day_columns].to_numpy(dtype=np.float64)
        if collect_item_stats:
            stats["observations"] += values.size
            stats["zero"] += int((values == 0).sum())
            stats["positive"] += int((values > 0).sum())
        weekly = values.reshape(len(chunk), len(weeks), 7).sum(axis=2)
        indices = np.fromiter(
            (group_index[(store, dept)] for store, dept in zip(chunk["store_id"], chunk["dept_id"])),
            dtype=np.int64, count=len(chunk),
        )
        np.add.at(matrix, indices, weekly)
        offset += len(chunk)
    if offset != len(metadata):
        raise RuntimeError("sales row order/count differs from metadata")
    group_values = np.repeat(groups.to_numpy(), len(weeks), axis=0)
    week_values = np.tile(weeks[["wm_yr_wk", "week_start", "week_end", "split"]].to_numpy(), (len(groups), 1))
    panel = pd.DataFrame(
        np.column_stack([group_values, week_values, matrix.reshape(-1)]),
        columns=["store_id", "dept_id", "cat_id", "state_id", "wm_yr_wk", "week_start", "week_end", "split", "actual_demand"],
    )
    panel["wm_yr_wk"] = panel["wm_yr_wk"].astype(int)
    panel["actual_demand"] = panel["actual_demand"].astype(float)
    panel["week_start"] = pd.to_datetime(panel["week_start"])
    panel["week_end"] = pd.to_datetime(panel["week_end"])
    return panel, stats


def price_context(prices_path: Path, metadata: pd.DataFrame, weeks: pd.DataFrame) -> pd.DataFrame:
    item_map = metadata[["item_id", "dept_id"]].drop_duplicates("item_id")
    train_weeks = set(weeks.loc[weeks["split"] == "TRAIN", "wm_yr_wk"].astype(int))
    reference_parts = []
    for chunk in pd.read_csv(prices_path, chunksize=250000):
        train = chunk[chunk["wm_yr_wk"].isin(train_weeks)]
        if not train.empty:
            reference_parts.append(train.groupby(["store_id", "item_id"], sort=False)["sell_price"].max())
    reference = pd.concat(reference_parts).groupby(level=[0, 1]).max().rename("train_reference_price").reset_index()
    parts = []
    valid_weeks = set(weeks["wm_yr_wk"].astype(int))
    for chunk in pd.read_csv(prices_path, chunksize=250000):
        chunk = chunk[chunk["wm_yr_wk"].isin(valid_weeks)].merge(
            reference, on=["store_id", "item_id"], how="left", validate="many_to_one",
        ).merge(item_map, on="item_id", how="left", validate="many_to_one")
        chunk["price_below_train_reference"] = chunk["sell_price"] < chunk["train_reference_price"]
        parts.append(chunk.groupby(["store_id", "dept_id", "wm_yr_wk"], sort=False).agg(
            priced_item_count=("item_id", "size"),
            price_drop_item_count=("price_below_train_reference", "sum"),
        ))
    return pd.concat(parts).groupby(level=[0, 1, 2]).sum().reset_index()


def add_context(panel: pd.DataFrame, weeks: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    context = panel.merge(
        weeks.drop(columns=["day_columns", "split", "week_start", "week_end"]),
        on="wm_yr_wk", how="left", validate="many_to_one",
    ).merge(prices, on=["store_id", "dept_id", "wm_yr_wk"], how="left", validate="one_to_one")
    context[["priced_item_count", "price_drop_item_count"]] = context[
        ["priced_item_count", "price_drop_item_count"]
    ].fillna(0)
    context["promotion_like"] = context["price_drop_item_count"] > 0
    snap_column = "snap_" + context["state_id"].astype(str) + "_days"
    context["snap_active"] = np.fromiter(
        (context.iloc[index][column] >= 4 for index, column in enumerate(snap_column)),
        dtype=bool, count=len(context),
    )
    context["generic_event_proxy"] = (
        context["calendar_event"] | context["snap_active"] | context["promotion_like"]
    )
    context["event_promotion_overlap"] = context["calendar_event"] & context["promotion_like"]
    return context


def calibration_unit_rows(item_stats: dict[str, int], dept_panel: pd.DataFrame) -> list[dict]:
    category = dept_panel.groupby(["store_id", "cat_id", "wm_yr_wk"], observed=True)["actual_demand"].sum()
    dept = dept_panel["actual_demand"]
    return [
        {
            "candidate": "store x item x day", "observations": item_stats["observations"],
            "zero_demand_rate": item_stats["zero"] / item_stats["observations"],
            "positive_demand_rate": item_stats["positive"] / item_stats["observations"],
            "entity_count": item_stats["entities"],
            "baseline_usability": "causal baselines feasible but panel is highly sparse and computationally large",
            "selected": False,
        },
        {
            "candidate": "store x department x week", "observations": len(dept),
            "zero_demand_rate": float((dept == 0).mean()),
            "positive_demand_rate": float((dept > 0).mean()),
            "entity_count": int(dept_panel[["store_id", "dept_id"]].drop_duplicates().shape[0]),
            "baseline_usability": "stable interpretable weekly panel with causal lag history",
            "selected": True,
        },
        {
            "candidate": "store x category x week", "observations": len(category),
            "zero_demand_rate": float((category == 0).mean()),
            "positive_demand_rate": float((category > 0).mean()),
            "entity_count": int(dept_panel[["store_id", "cat_id"]].drop_duplicates().shape[0]),
            "baseline_usability": "stable but coarser than necessary",
            "selected": False,
        },
    ]


def mask_for_stratum(frame: pd.DataFrame, stratum: str) -> pd.Series:
    return {
        "M5_GENERIC_EVENT_PROXY": frame["generic_event_proxy"],
        "M5_CALENDAR_EVENT": frame["calendar_event"],
        "M5_PROMOTION_LIKE": frame["promotion_like"],
        "M5_EVENT_PROMOTION_OVERLAP": frame["event_promotion_overlap"],
        "M5_SNAP_CONTEXT": frame["snap_active"],
    }[stratum].astype(bool)


def quantile_rows(calibration: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for stratum in (
        PRIMARY_STRATUM, "M5_CALENDAR_EVENT", "M5_PROMOTION_LIKE",
        "M5_EVENT_PROMOTION_OVERLAP", "M5_SNAP_CONTEXT",
    ):
        sample = calibration[mask_for_stratum(calibration, stratum) & (calibration["uplift"] > 0)]
        if len(sample) < MIN_QUANTILE_SAMPLE:
            continue
        for quantile in QUANTILES:
            rows.append({
                "domain": "M5", "proxy_stratum": stratum, "quantile_level": quantile,
                "quantile_value": float(np.quantile(sample["uplift"], quantile, method="linear")),
                "sample_count": len(sample),
                "calibration_sample_definition": "positive uplift; TRAIN+VALIDATION; baseline >= 1; frozen proxy rule",
                "provenance": "M5_ONLY_DERIVED_FROM_OBSERVED",
                "status": "STABLE_GROUP_SUMMARY",
            })
    return pd.DataFrame(rows).sort_values(["proxy_stratum", "quantile_level"]).reset_index(drop=True)


def coverage_rows(quantiles: pd.DataFrame, test: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for row in quantiles.itertuples(index=False):
        sample = test[mask_for_stratum(test, row.proxy_stratum) & (test["uplift"] > 0)]
        observed = float((sample["uplift"] <= row.quantile_value).mean()) if len(sample) else np.nan
        rows.append({
            "proxy_stratum": row.proxy_stratum, "target_quantile": row.quantile_level,
            "calibrated_value": row.quantile_value, "test_n": len(sample),
            "observed_coverage": observed,
            "coverage_direction": coverage_direction(observed, row.quantile_level),
            "absolute_coverage_error": abs(observed - row.quantile_level),
            "tolerance_frozen_before_test": COVERAGE_TOLERANCE,
        })
    return pd.DataFrame(rows).sort_values(["proxy_stratum", "target_quantile"]).reset_index(drop=True)


def heterogeneity_rows(calibration: pd.DataFrame, test: pd.DataFrame, global_q90: float) -> pd.DataFrame:
    rows = []
    for dimension in ("store_id", "state_id", "cat_id", "dept_id"):
        for value, cal_group in calibration.groupby(dimension, sort=True, observed=True):
            cal_group = cal_group[cal_group["generic_event_proxy"] & (cal_group["uplift"] > 0)]
            test_group = test[(test[dimension] == value) & test["generic_event_proxy"] & (test["uplift"] > 0)]
            if len(cal_group) < 30 or len(test_group) < 30:
                continue
            group_q90 = float(np.quantile(cal_group["uplift"], 0.90, method="linear"))
            coverage = float((test_group["uplift"] <= global_q90).mean())
            rows.append({
                "group_dimension": dimension, "group_value": value,
                "calibration_n": len(cal_group), "test_n": len(test_group),
                "group_q90": group_q90, "global_q90": global_q90,
                "q90_difference_from_global": group_q90 - global_q90,
                "test_coverage_using_global_q90": coverage,
                "absolute_global_q90_coverage_error": abs(coverage - 0.90),
            })
    return pd.DataFrame(rows).sort_values(["group_dimension", "group_value"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--paper2-checkout", required=True, type=Path)
    args = parser.parse_args()
    output = ROOT / "artifacts" / "m5_demand_calibration_stage4"
    availability = json.loads((output / "data_availability_audit.json").read_text(encoding="utf-8"))
    if availability["status"] != "M5_DATA_AVAILABILITY_PASS":
        raise SystemExit("STOP: M5 data availability audit has not passed")
    paper2_head = paper2_git(args.paper2_checkout, "rev-parse", "HEAD")
    paper2_clean = not bool(paper2_git(args.paper2_checkout, "status", "--porcelain"))
    if paper2_head != PAPER2_SHA or not paper2_clean:
        raise SystemExit("STOP: Paper-2 frozen checkout changed")
    prior_dirs = {
        "stage1": ROOT / "artifacts" / "compound_risk_stage1",
        "stage2": ROOT / "artifacts" / "compound_risk_stage2",
        "stage3a": ROOT / "artifacts" / "risk_impact_calibration_stage3",
        "stage3c": ROOT / "artifacts" / "risk_impact_mapping_stage3c",
        "stage3d": ROOT / "artifacts" / "route_risk_calibration_stage3d",
    }
    prior_before = {name: directory_hashes(path) for name, path in prior_dirs.items()}

    calendar = pd.read_csv(args.data_dir / "calendar.csv", parse_dates=["date"])
    sales_path = args.data_dir / "sales_train_evaluation.csv"
    columns = pd.read_csv(sales_path, nrows=0).columns.tolist()
    demand_columns = [column for column in columns if column.startswith("d_")]
    weeks = week_table(calendar, demand_columns)
    metadata = pd.read_csv(
        sales_path, usecols=["item_id", "dept_id", "cat_id", "store_id", "state_id"],
    )
    pre_weeks = weeks[weeks["split"].isin(["TRAIN", "VALIDATION"])].copy()
    pre_panel, item_stats = aggregate_demand(sales_path, metadata, pre_weeks, collect_item_stats=True)
    unit_candidates = calibration_unit_rows(item_stats, pre_panel)
    unit_decision = {
        "decision_frozen_before_test_demand_read": True,
        "selected_unit": "store x department x week",
        "reason": "item-day is substantially sparse and very large; department-week is stable while preserving more detail than category-week",
        "candidates": unit_candidates,
    }
    (output / "calibration_unit_decision.json").write_text(
        json.dumps(unit_decision, indent=2) + "\n", encoding="utf-8",
    )
    split_record = {
        "rule_frozen_before_test_demand_read": True,
        "rule": "complete M5 retail weeks; TRAIN ends 2014-12-31; VALIDATION is within 2015; TEST starts 2016-01-01",
        "splits": {},
    }
    for split in ("TRAIN", "VALIDATION", "TEST"):
        part = weeks[weeks["split"] == split]
        split_record["splits"][split] = {
            "first_date": str(part["week_start"].min().date()),
            "last_date": str(part["week_end"].max().date()),
            "week_count": len(part), "entity_count": 70,
            "panel_row_count": len(part) * 70,
        }
    (output / "temporal_split.json").write_text(
        json.dumps(split_record, indent=2) + "\n", encoding="utf-8",
    )

    prices = price_context(args.data_dir / "sell_prices.csv", metadata, weeks)
    pre_panel = add_context(pre_panel, weeks, prices)
    pre_panel = add_causal_baselines(pre_panel)
    leakage = audit_baselines(pre_panel)
    if not leakage["passed"]:
        raise SystemExit(f"STOP: causal baseline leakage audit failed: {leakage}")
    selected_baseline, baseline_comparison = select_baseline(pre_panel)
    pre_panel = add_uplift(pre_panel, selected_baseline)
    quantiles = quantile_rows(pre_panel)
    primary = quantiles[quantiles["proxy_stratum"] == PRIMARY_STRATUM]
    if len(primary) != 3:
        raise SystemExit("STOP: primary M5 quantiles are not identifiable")
    bootstrap_rows = []
    primary_sample = pre_panel[
        pre_panel["generic_event_proxy"] & pre_panel["uplift_eligible"] & (pre_panel["uplift"] > 0)
    ]["uplift"].to_numpy()
    for index, quantile in enumerate(QUANTILES):
        estimate, lower, upper = bootstrap_quantile(
            primary_sample, quantile, BOOTSTRAP_SEED + index,
        )
        bootstrap_rows.append({
            "proxy_stratum": PRIMARY_STRATUM, "quantile_level": quantile,
            "point_estimate": estimate, "ci_lower": lower, "ci_upper": upper,
            "bootstrap_repetitions": BOOTSTRAP_RESAMPLES, "seed": BOOTSTRAP_SEED + index,
        })

    # TEST demand is first aggregated only after unit, split, proxy rules, baseline,
    # minimum baseline, quantiles, bootstrap settings, and coverage tolerance are frozen.
    test_weeks = weeks[weeks["split"] == "TEST"].copy()
    test_panel, _ = aggregate_demand(sales_path, metadata, test_weeks, collect_item_stats=False)
    combined = pd.concat([pre_panel.drop(columns=list(BASELINE_ORDER) + ["baseline_demand", "uplift", "uplift_eligible"]), add_context(test_panel, weeks, prices)])
    combined = add_causal_baselines(combined)
    combined = add_uplift(combined, selected_baseline)
    test = combined[combined["split"] == "TEST"].copy()
    coverage = coverage_rows(quantiles, test)
    global_q90 = float(primary.loc[np.isclose(primary["quantile_level"], 0.90), "quantile_value"].iloc[0])
    heterogeneity = heterogeneity_rows(pre_panel, test, global_q90)
    errors = heterogeneity["absolute_global_q90_coverage_error"].to_numpy()
    q90_values = heterogeneity["group_q90"].to_numpy()
    heterogeneity_conditions = {
        "median_group_q90_absolute_error": float(np.median(errors)) > HETEROGENEITY_THRESHOLDS["median_group_q90_absolute_error"],
        "fraction_groups_q90_error_over_0_10": float(np.mean(errors > 0.10)) >= HETEROGENEITY_THRESHOLDS["fraction_groups_q90_error_over_0_10"],
        "group_q90_iqr": float(np.quantile(q90_values, 0.75) - np.quantile(q90_values, 0.25)) >= HETEROGENEITY_THRESHOLDS["group_q90_iqr"],
    }
    heterogeneity_material = all(heterogeneity_conditions.values())
    primary_coverage = coverage[coverage["proxy_stratum"] == PRIMARY_STRATUM]
    systematic_miscalibration = int((primary_coverage["absolute_coverage_error"] > COVERAGE_TOLERANCE).sum()) >= 2
    ml_needed = heterogeneity_material and systematic_miscalibration
    ml_gate = {
        "classification": "ML_REEVALUATION_JUSTIFIED" if ml_needed else "SIMPLE_CALIBRATION_RETAINED",
        "ml_needed": ml_needed,
        "heterogeneity_material": heterogeneity_material,
        "heterogeneity_conditions": heterogeneity_conditions,
        "heterogeneity_thresholds": HETEROGENEITY_THRESHOLDS,
        "simple_systematic_miscalibration": systematic_miscalibration,
        "simple_miscalibration_rule": SIMPLE_MISCALIBRATION_RULE,
        "ml_training_dispatches": 0,
        "conditional_ml_results_file": None,
        "negative_improvement_note": "negative ML improvement means ML error is larger; it does not determine under- versus over-coverage",
    }

    favorita = pd.read_csv(ROOT / "artifacts" / "risk_impact_calibration_stage3" / "empirical_quantiles.csv")
    favorita = favorita[(favorita["group_dimension"] == "overall") & (favorita["group_value"] == "ALL_EVENT_PROXIES")].iloc[0]
    comparison_rows = []
    for quantile in QUANTILES:
        m5 = float(primary.loc[np.isclose(primary["quantile_level"], quantile), "quantile_value"].iloc[0])
        fav = float(favorita[f"q{int(quantile * 100)}"])
        comparison_rows.append({
            "proxy_concept": "positive uplift under generic observed retail event/context proxy",
            "quantile_level": quantile, "favorita_value": fav, "m5_value": m5,
            "absolute_difference": abs(m5 - fav),
            "relative_difference": abs(m5 - fav) / abs(fav) if fav else np.nan,
            "same_method": True,
            "interpretation": "same positive-uplift empirical-quantile method; domain-specific parameter values; equality not required",
        })

    summary_rows = []
    for split, frame in (("TRAIN_VALIDATION", pre_panel), ("TEST", test)):
        eligible = frame[frame["uplift_eligible"]]
        summary_rows.append({
            "split": split, "total_rows": len(frame), "eligible_rows": len(eligible),
            "excluded_baseline_below_threshold_rows": int((~frame["uplift_eligible"]).sum()),
            "positive_uplift_rows": int((eligible["uplift"] > 0).sum()),
            "zero_uplift_rows": int((eligible["uplift"] == 0).sum()),
            "negative_uplift_rows": int((eligible["uplift"] < 0).sum()),
            "minimum_positive_baseline_threshold": MINIMUM_POSITIVE_BASELINE,
            "all_uplift_median": float(eligible["uplift"].median()),
        })
    proxy_rows = []
    definitions = {
        "M5_GENERIC_EVENT_PROXY": ("calendar event OR majority-week SNAP context OR promotion-like price context", "DERIVED_UNION"),
        "M5_CALENDAR_EVENT": ("event_name_1 or event_name_2 present on any day in the week", "DIRECT_CALENDAR"),
        "M5_PROMOTION_LIKE": ("at least one listed item price below its store-item TRAIN maximum", "DERIVED_PRICE_CONTEXT"),
        "M5_EVENT_PROMOTION_OVERLAP": ("calendar event AND promotion-like price context", "DERIVED_OVERLAP"),
        "M5_SNAP_CONTEXT": ("state-specific SNAP flag active on at least four of seven days", "DIRECT_CALENDAR_STATE_CONTEXT"),
    }
    for proxy, (rule, provenance) in definitions.items():
        proxy_rows.append({
            "proxy_id": proxy, "proxy_name": proxy, "source_field": "calendar.csv and/or sell_prices.csv",
            "construction_rule": rule, "direct_or_derived": "DERIVED" if provenance.startswith("DERIVED") else "DIRECT_CONTEXT",
            "economic_interpretation": "retail demand context; SNAP, calendar events, and price context are not asserted to be the same mechanism",
            "provenance": provenance,
            "sample_count": int((mask_for_stratum(pre_panel, proxy) & pre_panel["uplift_eligible"] & (pre_panel["uplift"] > 0)).sum()),
        })

    gate_rows = [
        {"gate": "GATE_1_DATA_INTEGRITY", "pass": True, "evidence": "four critical files present, readable, schema/key/date audited"},
        {"gate": "GATE_2_NO_LEAKAGE", "pass": leakage["passed"], "evidence": json.dumps(leakage, sort_keys=True)},
        {"gate": "GATE_3_BASELINE_VALIDITY", "pass": bool(baseline_comparison.loc[baseline_comparison["selected"], "MAE"].iloc[0] <= baseline_comparison.loc[baseline_comparison["baseline_id"] == "B1_LAG_1", "MAE"].iloc[0]), "evidence": "validation-selected baseline MAE no worse than lag-1 naive"},
        {"gate": "GATE_4_QUANTILE_IDENTIFIABILITY", "pass": bool(primary["sample_count"].min() >= MIN_QUANTILE_SAMPLE and np.all(np.diff(primary.sort_values("quantile_level")["quantile_value"]) >= 0)), "evidence": "q75 <= q90 <= q95 and primary sample >= frozen minimum"},
        {"gate": "GATE_5_HELDOUT_CALIBRATION", "pass": bool((primary_coverage["absolute_coverage_error"] <= COVERAGE_TOLERANCE).all()), "evidence": "all primary absolute coverage errors <= 0.05"},
        {"gate": "GATE_6_METHOD_TRANSFERABILITY", "pass": True, "evidence": "independent M5 baseline-to-uplift-to-quantile-to-heldout chain completed"},
    ]
    overall_pass = all(row["pass"] for row in gate_rows)
    status = "STAGE_4_DEMAND_CALIBRATION_VALIDATION_PASS" if overall_pass else "STAGE_4_DEMAND_CALIBRATION_VALIDATION_FAIL"
    prior_after = {name: directory_hashes(path) for name, path in prior_dirs.items()}
    unchanged = {name: prior_before[name] == prior_after[name] for name in prior_dirs}
    if not all(unchanged.values()):
        raise SystemExit("STOP: prior-stage artifacts changed")

    write_csv(output / "baseline_model_comparison.csv", baseline_comparison)
    write_csv(output / "uplift_observations_summary.csv", summary_rows)
    write_csv(output / "event_proxy_definition.csv", proxy_rows)
    write_csv(output / "empirical_quantiles.csv", quantiles)
    write_csv(output / "quantile_bootstrap_ci.csv", bootstrap_rows)
    write_csv(output / "heldout_coverage.csv", coverage)
    write_csv(output / "heterogeneity_diagnostic.csv", heterogeneity)
    write_csv(output / "favorita_vs_m5_quantile_comparison.csv", comparison_rows)
    (output / "ml_necessity_gate.json").write_text(json.dumps(ml_gate, indent=2) + "\n", encoding="utf-8")
    (output / "gate_results.json").write_text(json.dumps({"status": status, "gates": gate_rows}, indent=2) + "\n", encoding="utf-8")
    audit = {
        "status": status,
        "cross_domain_calibration_status": "SUPPORTED" if overall_pass else "NOT_SUPPORTED_BY_FROZEN_GATES",
        "calibration_model_status": (
            "SIMPLE_EMPIRICAL_QUANTILE_RETAINED"
            if not ml_needed else "ML_REEVALUATION_JUSTIFIED"
        ),
        "stage3d_base_commit": STAGE3D_COMMIT,
        "data_availability_status": availability["status"],
        "unit_of_analysis": unit_decision["selected_unit"],
        "selected_baseline": selected_baseline,
        "minimum_positive_baseline_threshold": MINIMUM_POSITIVE_BASELINE,
        "coverage_tolerance": COVERAGE_TOLERANCE,
        "test_first_read_purpose": "held-out aggregation after all selection/calibration rules frozen",
        "optimizer_dispatches": 0, "genai_dispatches": 0,
        "ml_training_dispatches": ml_gate["ml_training_dispatches"],
        "paper2_frozen_sha_before": paper2_head,
        "paper2_frozen_sha_after": paper2_git(args.paper2_checkout, "rev-parse", "HEAD"),
        "paper2_clean_before": paper2_clean,
        "paper2_clean_after": not bool(paper2_git(args.paper2_checkout, "status", "--porcelain")),
        "prior_artifacts_unchanged": unchanged,
        "prior_artifact_hashes_before": prior_before,
        "prior_artifact_hashes_after": prior_after,
        "favorita_parameter_equality_required": False,
        "route_risk_recalibrated": False,
        "universal_parameter_claim": False,
    }
    (output / "execution_audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": status, "selected_baseline": selected_baseline,
        "ml_gate": ml_gate["classification"],
        "primary_coverage": primary_coverage[["target_quantile", "observed_coverage", "absolute_coverage_error"]].to_dict("records"),
    }, indent=2))


if __name__ == "__main__":
    main()
