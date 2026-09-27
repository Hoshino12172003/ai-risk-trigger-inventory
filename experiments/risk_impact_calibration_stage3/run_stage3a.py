"""Run causal statistical risk-impact calibration without optimizer dispatches."""

from __future__ import annotations

import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from typing import Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_risk_trigger_inventory.calibration.causal_baselines import (
    BASELINE_ORDER,
    add_causal_baselines,
    add_event_proxies,
    add_uplift,
    assign_chronological_split,
    audit_causal_baselines,
    build_complete_weekly_panel,
    select_baseline,
)
from ai_risk_trigger_inventory.calibration.ml_gate import evaluate_ml_necessity
from ai_risk_trigger_inventory.calibration.statistics import (
    BOOTSTRAP_QUANTILES,
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    MIN_GROUP_SAMPLE,
    QUANTILES,
    bootstrap_quantile_ci,
    empirical_coverage,
    empirical_quantiles,
    sample_status,
)


STAGE2_COMMIT = "43755bf1b09550eb2a98096b1b7bd00a79a6a710"
PAPER2_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
ALLOWED_CANDIDATE_STATUSES = {
    "CANDIDATE_DATA_SUPPORTED", "CANDIDATE_ML_SUPPORTED", "STRESS_TEST_ONLY",
    "INSUFFICIENT_SAMPLE", "PENDING_EXTERNAL_EVIDENCE",
}
PRIMARY_PROXIES = {"P1_PROMOTION", "P2_HOLIDAY_EVENT", "P3_PROMOTION_HOLIDAY_OVERLAP"}


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _directory_hashes(path: Path) -> dict[str, str]:
    return {
        str(file.relative_to(ROOT)).replace("\\", "/"): _digest(file)
        for file in sorted(path.rglob("*")) if file.is_file()
    }


def _write_csv(path: Path, rows: pd.DataFrame | list[dict]) -> None:
    frame = rows if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")


def _group_views(frame: pd.DataFrame):
    yield "overall", "ALL_EVENT_PROXIES", frame
    dimensions = {
        "product_family": "family",
        "store": "store_nbr",
        "state_region": "state",
        "event_proxy": "event_proxy",
        "promotion_intensity": "promotion_stratum",
        "holiday_status": "holiday_status",
    }
    for dimension, column in dimensions.items():
        for value, group in frame.groupby(column, sort=True, observed=True):
            yield dimension, str(value), group


def _subset_for_group(frame: pd.DataFrame, dimension: str, value: str) -> pd.DataFrame:
    if dimension == "overall":
        return frame
    columns = {
        "product_family": "family", "store": "store_nbr",
        "state_region": "state", "event_proxy": "event_proxy",
        "promotion_intensity": "promotion_stratum",
        "holiday_status": "holiday_status",
    }
    return frame[frame[columns[dimension]].astype(str) == value]


def _quantile_rows(calibration: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for dimension, value, group in _group_views(calibration):
        estimates = empirical_quantiles(group["uplift"])
        rows.append({
            "population": "POSITIVE_UPLIFT_EVENT_PROXY_P1_P2_P3",
            "group_dimension": dimension,
            "group_value": value,
            "sample_size": len(group),
            "sample_status": sample_status(len(group)),
            **{f"q{int(q * 100)}": estimates[q] for q in QUANTILES},
            "data_source": "Favorita observed retail demand context",
            "sample_definition": "positive uplift; event proxy P1/P2/P3; TRAIN+VALIDATION",
            "provenance": "DERIVED_FROM_OBSERVED",
        })
    normal = calibration.attrs["normal_comparator"]
    estimates = empirical_quantiles(normal["uplift"])
    rows.append({
        "population": "POSITIVE_UPLIFT_NORMAL_P4_COMPARATOR",
        "group_dimension": "overall",
        "group_value": "P4_NORMAL",
        "sample_size": len(normal),
        "sample_status": sample_status(len(normal)),
        **{f"q{int(q * 100)}": estimates[q] for q in QUANTILES},
        "data_source": "Favorita observed retail demand context",
        "sample_definition": "positive uplift; normal proxy P4; TRAIN+VALIDATION",
        "provenance": "DERIVED_FROM_OBSERVED",
    })
    return pd.DataFrame(rows).sort_values(
        ["population", "group_dimension", "group_value"]
    ).reset_index(drop=True)


def _bootstrap_rows(calibration: pd.DataFrame) -> pd.DataFrame:
    groups = [("overall", "ALL_EVENT_PROXIES", calibration)]
    groups.extend(
        ("event_proxy", str(value), group)
        for value, group in calibration.groupby("event_proxy", sort=True, observed=True)
    )
    rows = []
    for group_index, (dimension, value, group) in enumerate(groups):
        for quantile_index, quantile in enumerate(BOOTSTRAP_QUANTILES):
            estimate, lower, upper = bootstrap_quantile_ci(
                group["uplift"], quantile,
                seed=BOOTSTRAP_SEED + group_index * 10 + quantile_index,
            )
            rows.append({
                "group_dimension": dimension, "group_value": value,
                "quantile_level": quantile, "estimate": estimate,
                "ci_lower": lower, "ci_upper": upper,
                "confidence_level": 0.95, "sample_size": len(group),
                "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
                "random_seed": BOOTSTRAP_SEED + group_index * 10 + quantile_index,
                "status": sample_status(len(group)),
            })
    return pd.DataFrame(rows)


def _coverage_rows(quantiles: pd.DataFrame, test: pd.DataFrame) -> pd.DataFrame:
    rows = []
    primary = quantiles[quantiles["population"] == "POSITIVE_UPLIFT_EVENT_PROXY_P1_P2_P3"]
    for row in primary.itertuples(index=False):
        group = _subset_for_group(test, row.group_dimension, row.group_value)
        for quantile in BOOTSTRAP_QUANTILES:
            estimate = getattr(row, f"q{int(quantile * 100)}")
            coverage = empirical_coverage(group["uplift"], estimate)
            rows.append({
                "group_dimension": row.group_dimension,
                "group_value": row.group_value,
                "quantile_level": quantile,
                "calibration_estimate": estimate,
                "calibration_sample_size": row.sample_size,
                "test_sample_size": len(group),
                "observed_coverage": coverage,
                "coverage_error": coverage - quantile if np.isfinite(coverage) else np.nan,
                "absolute_coverage_error": abs(coverage - quantile) if np.isfinite(coverage) else np.nan,
                "test_group_status": sample_status(len(group)),
                "threshold_source": "TRAIN+VALIDATION_ONLY",
            })
    return pd.DataFrame(rows).sort_values(
        ["group_dimension", "group_value", "quantile_level"]
    ).reset_index(drop=True)


def _heterogeneity_rows(
    quantiles: pd.DataFrame, test: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, object]]:
    primary = quantiles[quantiles["population"] == "POSITIVE_UPLIFT_EVENT_PROXY_P1_P2_P3"]
    global_row = primary[primary["group_dimension"] == "overall"].iloc[0]
    rows = []
    for row in primary[primary["group_dimension"] != "overall"].itertuples(index=False):
        group = _subset_for_group(test, row.group_dimension, row.group_value)
        coverage = empirical_coverage(group["uplift"], float(global_row.q90))
        rows.append({
            "group_dimension": row.group_dimension,
            "group_value": row.group_value,
            "calibration_sample_size": row.sample_size,
            "test_sample_size": len(group),
            "group_q90": row.q90,
            "group_q95": row.q95,
            "global_q90": global_row.q90,
            "global_q95": global_row.q95,
            "q90_difference_from_global": row.q90 - global_row.q90,
            "q95_difference_from_global": row.q95 - global_row.q95,
            "test_coverage_using_global_q90": coverage,
            "global_q90_coverage_error": coverage - 0.90 if np.isfinite(coverage) else np.nan,
            "absolute_global_q90_coverage_error": abs(coverage - 0.90) if np.isfinite(coverage) else np.nan,
            "eligible_for_ml_gate": row.sample_size >= 30 and len(group) >= 30,
        })
    result = pd.DataFrame(rows).sort_values(["group_dimension", "group_value"]).reset_index(drop=True)
    eligible = result[result["eligible_for_ml_gate"]]
    q90_dispersion_groups = eligible[
        eligible["group_dimension"].isin(["product_family", "state_region"])
    ]["group_q90"]
    gate = evaluate_ml_necessity(
        eligible["absolute_global_q90_coverage_error"], q90_dispersion_groups
    )
    return result, gate


def _candidate_rows(
    quantiles: pd.DataFrame, bootstrap: pd.DataFrame, coverage: pd.DataFrame
) -> pd.DataFrame:
    rows: list[dict] = []

    def add_empirical(risk_type: str, target: str, dimension: str, value: str, quantile: float):
        qrow = quantiles[
            (quantiles["population"] == "POSITIVE_UPLIFT_EVENT_PROXY_P1_P2_P3")
            & (quantiles["group_dimension"] == dimension)
            & (quantiles["group_value"] == value)
        ]
        if qrow.empty:
            return
        qrow = qrow.iloc[0]
        estimate = qrow[f"q{int(quantile * 100)}"]
        ci = bootstrap[
            (bootstrap["group_dimension"] == dimension)
            & (bootstrap["group_value"] == value)
            & (bootstrap["quantile_level"] == quantile)
        ]
        cov = coverage[
            (coverage["group_dimension"] == dimension)
            & (coverage["group_value"] == value)
            & (coverage["quantile_level"] == quantile)
        ]
        status = "CANDIDATE_DATA_SUPPORTED" if qrow["sample_size"] >= 30 else "INSUFFICIENT_SAMPLE"
        rows.append({
            "risk_type": risk_type, "component": "DEMAND_UPLIFT",
            "target_level": target, "candidate_parameter": "delta_D",
            "value": estimate, "units": "relative uplift on baseline",
            "quantile_level": quantile,
            "data_source": "Favorita observed retail demand context",
            "sample_definition": qrow["sample_definition"] + f"; {dimension}={value}",
            "sample_size": qrow["sample_size"],
            "bootstrap_ci_lower": ci.iloc[0]["ci_lower"] if not ci.empty else np.nan,
            "bootstrap_ci_upper": ci.iloc[0]["ci_upper"] if not ci.empty else np.nan,
            "heldout_coverage": cov.iloc[0]["observed_coverage"] if not cov.empty else np.nan,
            "status": status, "provenance": "DERIVED_FROM_OBSERVED_RETAIL_PROXY",
        })

    for quantile in BOOTSTRAP_QUANTILES:
        add_empirical("FLASH_DEMAND_SURGE", "GLOBAL_EVENT_PROXY", "overall", "ALL_EVENT_PROXIES", quantile)
    for proxy in ("P2_HOLIDAY_EVENT", "P3_PROMOTION_HOLIDAY_OVERLAP"):
        add_empirical("REGIONAL_EMERGENCY_DISRUPTION", proxy, "event_proxy", proxy, 0.90)

    for value, target in ((0.20, "E1_LOW_SENSITIVITY"), (0.40, "E1_MAIN"), (0.60, "E2_MAIN")):
        rows.append({
            "risk_type": "FLASH_DEMAND_SURGE", "component": "DEMAND_UPLIFT",
            "target_level": target, "candidate_parameter": "delta_D", "value": value,
            "units": "relative uplift on baseline", "quantile_level": np.nan,
            "data_source": "Stage-1 preregistered stress test", "sample_definition": "not data calibrated",
            "sample_size": np.nan, "bootstrap_ci_lower": np.nan, "bootstrap_ci_upper": np.nan,
            "heldout_coverage": np.nan, "status": "STRESS_TEST_ONLY",
            "provenance": "PREREGISTERED_STAGE1_STRESS_MAGNITUDE",
        })
    for value in (0.25, 0.50, 0.75, 1.00):
        for risk_type in ("TRANSPORT_NETWORK_DISRUPTION", "REGIONAL_EMERGENCY_DISRUPTION"):
            rows.append({
                "risk_type": risk_type, "component": "ROUTE_SERVICE_LOSS",
                "target_level": "EFFECTIVE_ROUTE", "candidate_parameter": "delta_A", "value": value,
                "units": "effective route-service loss fraction", "quantile_level": np.nan,
                "data_source": "Stage-1 preregistered stress test", "sample_definition": "Favorita has no route-loss observations",
                "sample_size": np.nan, "bootstrap_ci_lower": np.nan, "bootstrap_ci_upper": np.nan,
                "heldout_coverage": np.nan, "status": "STRESS_TEST_ONLY",
                "provenance": "CALIBRATED_NOT_OBSERVED",
            })
    for risk_type in ("REGIONAL_EMERGENCY_DISRUPTION", "TRANSPORT_NETWORK_DISRUPTION"):
        rows.append({
            "risk_type": risk_type, "component": "ROUTE_SERVICE_LOSS",
            "target_level": "EXTERNAL_EVIDENCE_PLACEHOLDER", "candidate_parameter": "delta_A", "value": np.nan,
            "units": "effective route-service loss fraction", "quantile_level": np.nan,
            "data_source": "none verified", "sample_definition": "future logistics evidence required",
            "sample_size": np.nan, "bootstrap_ci_lower": np.nan, "bootstrap_ci_upper": np.nan,
            "heldout_coverage": np.nan, "status": "PENDING_EXTERNAL_EVIDENCE",
            "provenance": "PENDING_EXTERNAL_EVIDENCE",
        })
    result = pd.DataFrame(rows)
    if not set(result["status"]) <= ALLOWED_CANDIDATE_STATUSES:
        raise RuntimeError("invalid calibration candidate status")
    return result.sort_values(
        ["risk_type", "component", "target_level", "quantile_level"], na_position="last"
    ).reset_index(drop=True)


def _provenance_rows() -> list[dict]:
    return [
        {"item": "sales", "category": "OBSERVED", "source": "Favorita", "calibration_use": "actual demand"},
        {"item": "promotions", "category": "OBSERVED", "source": "Favorita", "calibration_use": "retail event proxy"},
        {"item": "dates/store/state/family", "category": "OBSERVED", "source": "Favorita", "calibration_use": "panel keys and context"},
        {"item": "holiday/event records", "category": "OBSERVED", "source": "Favorita", "calibration_use": "source retail context"},
        {"item": "weekly holiday/event flags", "category": "DERIVED", "source": "Favorita context transformation", "calibration_use": "retail event proxy"},
        {"item": "weekly demand/baselines/uplift/quantiles", "category": "DERIVED", "source": "Stage-3 causal pipeline", "calibration_use": "candidate delta_D"},
        {"item": "warehouses/capacities/routes", "category": "CALIBRATED_NOT_OBSERVED", "source": "not in Favorita", "calibration_use": "none"},
        {"item": "route disruption magnitude", "category": "CALIBRATED_NOT_OBSERVED", "source": "not in Favorita", "calibration_use": "STRESS_TEST_ONLY"},
        {"item": "external route evidence", "category": "PENDING_EXTERNAL_EVIDENCE", "source": "none verified", "calibration_use": "future delta_A calibration"},
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--paper2-checkout", required=True, type=Path)
    args = parser.parse_args()
    daily_path = args.data_dir / "favorita_store_family_day.csv.gz"
    if not daily_path.exists():
        raise SystemExit("STOP: cleaned Favorita daily panel is missing")
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=args.paper2_checkout, text=True).strip() != PAPER2_SHA:
        raise SystemExit("STOP: Paper-2 frozen SHA mismatch")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=args.paper2_checkout, text=True).strip():
        raise SystemExit("STOP: Paper-2 frozen checkout is dirty")

    prior_before = {
        "stage1": _directory_hashes(ROOT / "artifacts" / "compound_risk_stage1"),
        "stage2": _directory_hashes(ROOT / "artifacts" / "compound_risk_stage2"),
    }
    usecols = [
        "date", "store_nbr", "family", "sales", "onpromotion", "state",
        "holiday_any", "holiday_event_count",
    ]
    daily = pd.read_csv(daily_path, usecols=usecols, parse_dates=["date"])
    source_rows = len(daily)
    if daily.duplicated(["date", "store_nbr", "family"]).any():
        raise SystemExit("STOP: daily panel key is not unique")
    if daily.groupby("store_nbr", observed=True)["state"].nunique().max() != 1:
        raise SystemExit("STOP: store-to-state mapping is not stable")
    daily_missingness = {column: int(daily[column].isna().sum()) for column in usecols}
    source_first, source_last = daily["date"].min(), daily["date"].max()

    weekly = build_complete_weekly_panel(daily)
    del daily
    weekly = add_causal_baselines(weekly)
    weekly = assign_chronological_split(weekly)
    weekly, promo_thresholds = add_event_proxies(weekly)
    weekly["holiday_status"] = np.where(weekly["holiday_any"], "HOLIDAY", "NO_HOLIDAY")
    leakage = audit_causal_baselines(weekly)
    if not leakage.passed:
        raise SystemExit(f"STOP: future leakage audit failed: {leakage}")
    selected, baseline_comparison = select_baseline(weekly)
    weekly = add_uplift(weekly, selected)

    usable = weekly[weekly["split"].isin(["TRAIN", "VALIDATION", "TEST"]) & weekly["uplift_valid"]].copy()
    calibration_all = usable[usable["split"].isin(["TRAIN", "VALIDATION"]) & (usable["uplift"] > 0)].copy()
    test_all = usable[(usable["split"] == "TEST") & (usable["uplift"] > 0)].copy()
    calibration = calibration_all[calibration_all["event_proxy"].isin(PRIMARY_PROXIES)].copy()
    test = test_all[test_all["event_proxy"].isin(PRIMARY_PROXIES)].copy()
    calibration.attrs["normal_comparator"] = calibration_all[calibration_all["event_proxy"] == "P4_NORMAL"].copy()
    if len(calibration) < 100 or len(test) < 30:
        raise SystemExit("STOP: main demand-surge quantile samples are insufficient")

    quantiles = _quantile_rows(calibration)
    bootstrap = _bootstrap_rows(calibration)
    coverage = _coverage_rows(quantiles, test)
    heterogeneity, ml_gate = _heterogeneity_rows(quantiles, test)
    candidates = _candidate_rows(quantiles, bootstrap, coverage)

    summary_rows = []
    for split in ("TRAIN", "VALIDATION", "TEST"):
        split_frame = usable[usable["split"] == split]
        for proxy, group in split_frame.groupby("event_proxy", sort=True, observed=True):
            summary_rows.append({
                "split": split, "event_proxy": proxy,
                "uplift_valid_rows": len(group),
                "positive_uplift_rows": int((group["uplift"] > 0).sum()),
                "nonpositive_uplift_rows": int((group["uplift"] <= 0).sum()),
                "median_uplift": float(group["uplift"].median()),
                "store_family_count": int(group[["store_nbr", "family"]].drop_duplicates().shape[0]),
            })

    output = ROOT / "artifacts" / "risk_impact_calibration_stage3"
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "baseline_model_comparison.csv", baseline_comparison)
    _write_csv(output / "uplift_observations_summary.csv", summary_rows)
    _write_csv(output / "empirical_quantiles.csv", quantiles)
    _write_csv(output / "quantile_bootstrap_ci.csv", bootstrap)
    _write_csv(output / "heldout_coverage.csv", coverage)
    _write_csv(output / "heterogeneity_diagnostic.csv", heterogeneity)
    _write_csv(output / "calibration_candidates.csv", candidates)
    _write_csv(output / "provenance_matrix.csv", _provenance_rows())
    ml_gate.update({
        "protocol_frozen_before_results": True,
        "stage3b_executed": False,
        "conditional_ml_results_file": None,
    })
    (output / "ml_necessity_gate.json").write_text(json.dumps(ml_gate, indent=2) + "\n", encoding="utf-8")

    split_rows = {}
    for split in ("TRAIN", "VALIDATION", "TEST"):
        part = weekly[weekly["split"] == split]
        split_rows[split] = {
            "rows": len(part),
            "first_week_start": str(part["week_start"].min().date()),
            "last_week_end": str(part["week_end"].max().date()),
            "store_family_count": int(part[["store_nbr", "family"]].drop_duplicates().shape[0]),
            "selected_baseline_missing": int(part[selected].isna().sum()),
        }
    prior_after = {
        "stage1": _directory_hashes(ROOT / "artifacts" / "compound_risk_stage1"),
        "stage2": _directory_hashes(ROOT / "artifacts" / "compound_risk_stage2"),
    }
    audit = {
        "status": "STAGE_3A_STATISTICAL_CALIBRATION_PASS",
        "stage2_commit": STAGE2_COMMIT,
        "paper2_frozen_sha_before": PAPER2_SHA,
        "paper2_frozen_sha_after": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=args.paper2_checkout, text=True).strip(),
        "paper2_clean_before": True,
        "paper2_clean_after": not bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=args.paper2_checkout, text=True).strip()),
        "optimizer_dispatches": 0,
        "data_file": daily_path.name,
        "data_sha256": _digest(daily_path),
        "source_date_first": str(source_first.date()),
        "source_date_last": str(source_last.date()),
        "source_rows": source_rows,
        "source_missingness": daily_missingness,
        "unit_of_analysis": "store_nbr x product_family x complete Monday-start week",
        "complete_weekly_rows": len(weekly),
        "splits": split_rows,
        "selected_baseline": selected,
        "promotion_intensity_train_tertiles": {"lower": promo_thresholds[0], "upper": promo_thresholds[1]},
        "leakage_audit": leakage.__dict__ | {"passed": leakage.passed},
        "primary_calibration_rows": len(calibration),
        "primary_test_rows": len(test),
        "ml_necessity_classification": ml_gate["classification"],
        "stage1_artifacts_unchanged": prior_before["stage1"] == prior_after["stage1"],
        "stage2_artifacts_unchanged": prior_before["stage2"] == prior_after["stage2"],
        "prior_artifact_hashes_before": prior_before,
        "prior_artifact_hashes_after": prior_after,
        "route_calibration_status": "STRESS_TEST_ONLY",
        "external_route_evidence": "PENDING_EXTERNAL_EVIDENCE",
    }
    if not all((audit["paper2_clean_after"], audit["stage1_artifacts_unchanged"], audit["stage2_artifacts_unchanged"])):
        raise SystemExit("STOP: frozen repository or prior artifacts changed")
    (output / "execution_audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": audit["status"], "selected_baseline": selected,
        "primary_calibration_rows": len(calibration), "primary_test_rows": len(test),
        "ml_gate": ml_gate["classification"],
    }, indent=2))


if __name__ == "__main__":
    main()
