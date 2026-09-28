"""Causal helpers for independent-domain M5 demand calibration validation."""

from __future__ import annotations

import numpy as np
import pandas as pd


BASELINE_ORDER = ("B1_LAG_1", "B2_MEAN_12", "B3_MEDIAN_12", "B4_LAG_52")
MINIMUM_POSITIVE_BASELINE = 1.0
QUANTILES = (0.75, 0.90, 0.95)
COVERAGE_TOLERANCE = 0.05
MIN_QUANTILE_SAMPLE = 100
BOOTSTRAP_SEED = 20260928
BOOTSTRAP_RESAMPLES = 10_000


def add_causal_baselines(panel: pd.DataFrame) -> pd.DataFrame:
    frame = panel.sort_values(["store_id", "dept_id", "week_start"]).copy()
    group = frame.groupby(["store_id", "dept_id"], sort=False, observed=True)["actual_demand"]
    frame["B1_LAG_1"] = group.shift(1)
    frame["B2_MEAN_12"] = group.transform(
        lambda values: values.shift(1).rolling(12, min_periods=12).mean()
    )
    frame["B3_MEDIAN_12"] = group.transform(
        lambda values: values.shift(1).rolling(12, min_periods=12).median()
    )
    lag = frame[["store_id", "dept_id", "wm_yr_wk", "actual_demand"]].copy()
    lag["wm_yr_wk"] = lag["wm_yr_wk"] + 100
    lag = lag.rename(columns={"actual_demand": "B4_LAG_52"})
    frame = frame.merge(lag, on=["store_id", "dept_id", "wm_yr_wk"], how="left", validate="one_to_one")
    return frame.sort_values(["store_id", "dept_id", "week_start"]).reset_index(drop=True)


def select_baseline(panel: pd.DataFrame) -> tuple[str, pd.DataFrame]:
    validation = panel[panel["split"] == "VALIDATION"].copy()
    common = np.ones(len(validation), dtype=bool)
    for name in BASELINE_ORDER:
        common &= validation[name].notna() & (validation[name] >= MINIMUM_POSITIVE_BASELINE)
    subset = validation.loc[common]
    if len(subset) < 100:
        raise ValueError("insufficient common validation rows for baseline selection")
    rows = []
    for rank, name in enumerate(BASELINE_ORDER):
        error = subset["actual_demand"] - subset[name]
        rows.append({
            "baseline_id": name,
            "description": {
                "B1_LAG_1": "previous complete week",
                "B2_MEAN_12": "mean of 12 complete prior weeks after shift(1)",
                "B3_MEDIAN_12": "median of 12 complete prior weeks after shift(1)",
                "B4_LAG_52": "same M5 retail week in prior year",
            }[name],
            "selection_rank": rank,
            "validation_n": len(subset),
            "MAE": float(error.abs().mean()),
            "RMSE": float(np.sqrt(np.mean(np.square(error)))),
            "MedianAE": float(error.abs().median()),
            "test_rows_used_for_selection": 0,
            "leakage_check": "PAST_ONLY",
        })
    comparison = pd.DataFrame(rows)
    minimum = float(comparison["MAE"].min())
    tied = comparison[np.isclose(comparison["MAE"], minimum, rtol=0, atol=1e-12)]
    selected = str(tied.sort_values("selection_rank").iloc[0]["baseline_id"])
    comparison["selected"] = comparison["baseline_id"] == selected
    return selected, comparison.sort_values("selection_rank").reset_index(drop=True)


def add_uplift(panel: pd.DataFrame, baseline_id: str) -> pd.DataFrame:
    frame = panel.copy()
    frame["baseline_demand"] = frame[baseline_id]
    eligible = frame["baseline_demand"].notna() & (
        frame["baseline_demand"] >= MINIMUM_POSITIVE_BASELINE
    )
    frame["uplift"] = np.nan
    frame.loc[eligible, "uplift"] = (
        frame.loc[eligible, "actual_demand"] - frame.loc[eligible, "baseline_demand"]
    ) / frame.loc[eligible, "baseline_demand"]
    frame["uplift_eligible"] = eligible
    return frame


def audit_baselines(panel: pd.DataFrame) -> dict[str, object]:
    mismatches = 0
    for _, group in panel.groupby(["store_id", "dept_id"], sort=False, observed=True):
        group = group.sort_values("week_start")
        demand = group["actual_demand"].to_numpy(dtype=float)
        for index in range(12, len(group)):
            mismatches += int(not np.isclose(
                group.iloc[index]["B2_MEAN_12"], np.mean(demand[index - 12:index]),
                rtol=0, atol=1e-10, equal_nan=True,
            ))
            mismatches += int(not np.isclose(
                group.iloc[index]["B3_MEDIAN_12"], np.median(demand[index - 12:index]),
                rtol=0, atol=1e-10, equal_nan=True,
            ))
    return {
        "rolling_shifted_before_window": True,
        "centered_rolling_used": False,
        "test_used_for_selection": False,
        "rolling_value_mismatches": mismatches,
        "passed": mismatches == 0,
    }


def bootstrap_quantile(values: np.ndarray, quantile: float, seed: int) -> tuple[float, float, float]:
    data = np.asarray(values, dtype=float)
    data = data[np.isfinite(data)]
    if len(data) < MIN_QUANTILE_SAMPLE:
        return np.nan, np.nan, np.nan
    estimate = float(np.quantile(data, quantile, method="linear"))
    rng = np.random.default_rng(seed)
    samples = np.empty(BOOTSTRAP_RESAMPLES)
    for start in range(0, BOOTSTRAP_RESAMPLES, 25):
        size = min(25, BOOTSTRAP_RESAMPLES - start)
        draws = rng.choice(data, size=(size, len(data)), replace=True)
        samples[start:start + size] = np.quantile(draws, quantile, axis=1, method="linear")
    lower, upper = np.quantile(samples, [0.025, 0.975], method="linear")
    return estimate, float(lower), float(upper)


def coverage_direction(observed: float, target: float) -> str:
    if np.isclose(observed, target, rtol=0, atol=1e-12):
        return "EXACT"
    return "UNDER_COVERAGE" if observed < target else "OVER_COVERAGE"
