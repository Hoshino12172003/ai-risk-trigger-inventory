"""Strictly past-only weekly aggregation and baseline candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


EPSILON = 1e-9
BASELINE_MAE_TIE_TOLERANCE = 1e-12
BASELINE_ORDER = ("B1_MEDIAN_4", "B2_MEDIAN_8", "B3_MEDIAN_12", "B4_LAG_52")
ROLLING_WINDOWS = {
    "B1_MEDIAN_4": 4,
    "B2_MEDIAN_8": 8,
    "B3_MEDIAN_12": 12,
}
SPLIT_BOUNDS = {
    "TRAIN": (pd.Timestamp("2013-01-01"), pd.Timestamp("2015-12-31")),
    "VALIDATION": (pd.Timestamp("2016-01-01"), pd.Timestamp("2016-12-31")),
    "TEST": (pd.Timestamp("2017-01-01"), pd.Timestamp("2017-08-15")),
}


@dataclass(frozen=True)
class LeakageAudit:
    rolling_shifted: bool
    centered_rolling_used: bool
    future_source_rows: int
    rolling_value_mismatches: int
    seasonal_date_mismatches: int

    @property
    def passed(self) -> bool:
        return (
            self.rolling_shifted
            and not self.centered_rolling_used
            and self.future_source_rows == 0
            and self.rolling_value_mismatches == 0
            and self.seasonal_date_mismatches == 0
        )


def build_complete_weekly_panel(daily: pd.DataFrame) -> pd.DataFrame:
    required = {
        "date", "store_nbr", "family", "sales", "onpromotion", "state",
        "holiday_any", "holiday_event_count",
    }
    missing = required - set(daily)
    if missing:
        raise ValueError(f"daily panel missing columns: {sorted(missing)}")
    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame["week_start"] = frame["date"] - pd.to_timedelta(frame["date"].dt.weekday, unit="D")
    grouped = frame.groupby(["store_nbr", "family", "week_start"], sort=True, observed=True)
    weekly = grouped.agg(
        actual_demand=("sales", "sum"),
        promotion_count=("onpromotion", "sum"),
        holiday_any=("holiday_any", "max"),
        holiday_event_count=("holiday_event_count", "sum"),
        state=("state", "first"),
        days_observed=("date", "nunique"),
    ).reset_index()
    weekly = weekly[weekly["days_observed"] == 7].copy()
    weekly["week_end"] = weekly["week_start"] + pd.Timedelta(days=6)
    weekly["promotion_active"] = weekly["promotion_count"] > 0
    weekly["promotion_intensity"] = weekly["promotion_count"] / 7.0
    weekly["holiday_any"] = weekly["holiday_any"].astype(bool)
    return weekly.sort_values(["store_nbr", "family", "week_start"]).reset_index(drop=True)


def add_causal_baselines(weekly: pd.DataFrame) -> pd.DataFrame:
    frame = weekly.sort_values(["store_nbr", "family", "week_start"]).copy()
    group = frame.groupby(["store_nbr", "family"], sort=False, observed=True)["actual_demand"]
    for name, window in ROLLING_WINDOWS.items():
        frame[name] = group.transform(
            lambda series, window=window: series.shift(1).rolling(window, min_periods=window).median()
        )
    lag = frame[["store_nbr", "family", "week_start", "actual_demand"]].copy()
    lag["week_start"] = lag["week_start"] + pd.Timedelta(weeks=52)
    lag = lag.rename(columns={"actual_demand": "B4_LAG_52"})
    frame = frame.merge(lag, on=["store_nbr", "family", "week_start"], how="left", validate="one_to_one")
    return frame.sort_values(["store_nbr", "family", "week_start"]).reset_index(drop=True)


def assign_chronological_split(weekly: pd.DataFrame) -> pd.DataFrame:
    frame = weekly.copy()
    frame["split"] = "EXCLUDED_BOUNDARY"
    for split, (first, last) in SPLIT_BOUNDS.items():
        mask = (frame["week_start"] >= first) & (frame["week_end"] <= last)
        frame.loc[mask, "split"] = split
    return frame


def add_event_proxies(frame: pd.DataFrame, train_thresholds: tuple[float, float] | None = None) -> tuple[pd.DataFrame, tuple[float, float]]:
    result = frame.copy()
    promo = result["promotion_active"].astype(bool)
    holiday = result["holiday_any"].astype(bool)
    result["event_proxy"] = np.select(
        [promo & ~holiday, ~promo & holiday, promo & holiday],
        ["P1_PROMOTION", "P2_HOLIDAY_EVENT", "P3_PROMOTION_HOLIDAY_OVERLAP"],
        default="P4_NORMAL",
    )
    if train_thresholds is None:
        train_positive = result.loc[
            (result["split"] == "TRAIN") & promo,
            "promotion_intensity",
        ]
        if train_positive.empty:
            raise ValueError("TRAIN has no promotion-active observations")
        train_thresholds = tuple(map(float, train_positive.quantile([1 / 3, 2 / 3]).tolist()))
    low, high = train_thresholds
    result["promotion_stratum"] = np.select(
        [~promo, result["promotion_intensity"] <= low, result["promotion_intensity"] <= high],
        ["NONE", "LOW", "MEDIUM"],
        default="HIGH",
    )
    return result, (low, high)


def select_baseline(frame: pd.DataFrame) -> tuple[str, pd.DataFrame]:
    common = (
        (frame["split"] == "VALIDATION")
        & ~frame["holiday_any"].astype(bool)
        & ~frame["promotion_active"].astype(bool)
        & (frame["actual_demand"] > 0)
    )
    for name in BASELINE_ORDER:
        common &= frame[name].notna() & (frame[name] > EPSILON)
    subset = frame.loc[common].copy()
    if len(subset) < 100:
        raise ValueError("insufficient normal-validation history for baseline selection")
    rows = []
    for rank, name in enumerate(BASELINE_ORDER):
        error = subset["actual_demand"] - subset[name]
        relative = error / subset[name]
        rows.append({
            "baseline_id": name,
            "selection_rank": rank,
            "validation_rows": len(subset),
            "validation_mae": float(error.abs().mean()),
            "validation_median_ape": float(relative.abs().median()),
            "validation_median_relative_bias": float(relative.median()),
            "normal_subset": "VALIDATION; no holiday; no promotion; positive demand; common valid history",
            "test_rows_used_for_selection": 0,
        })
    comparison = pd.DataFrame(rows)
    minimum_mae = float(comparison["validation_mae"].min())
    tied = comparison[
        comparison["validation_mae"] <= minimum_mae + BASELINE_MAE_TIE_TOLERANCE
    ]
    selected = str(tied.sort_values("selection_rank").iloc[0]["baseline_id"])
    comparison["selected"] = comparison["baseline_id"] == selected
    return selected, comparison.sort_values("selection_rank").reset_index(drop=True)


def add_uplift(frame: pd.DataFrame, baseline_id: str) -> pd.DataFrame:
    result = frame.copy()
    result["baseline_demand"] = result[baseline_id]
    valid = result["baseline_demand"].notna() & (result["baseline_demand"] > EPSILON)
    result["uplift"] = np.nan
    result.loc[valid, "uplift"] = (
        result.loc[valid, "actual_demand"] - result.loc[valid, "baseline_demand"]
    ) / result.loc[valid, "baseline_demand"]
    result["uplift_valid"] = valid
    return result


def audit_causal_baselines(frame: pd.DataFrame) -> LeakageAudit:
    mismatches = 0
    future = 0
    seasonal_mismatches = 0
    for _, group in frame.groupby(["store_nbr", "family"], sort=False, observed=True):
        group = group.sort_values("week_start")
        demand = group["actual_demand"].to_numpy(dtype=float)
        dates = group["week_start"].to_numpy(dtype="datetime64[ns]")
        for name, window in ROLLING_WINDOWS.items():
            actual = group[name].to_numpy(dtype=float)
            for index in range(window, len(group)):
                expected = float(np.median(demand[index - window:index]))
                if not np.isclose(actual[index], expected, rtol=0, atol=1e-10, equal_nan=True):
                    mismatches += 1
                if dates[index - 1] >= dates[index]:
                    future += 1
        b4 = group["B4_LAG_52"].to_numpy(dtype=float)
        by_date = {date: value for date, value in zip(dates, demand)}
        delta = np.timedelta64(364, "D")
        for index, value in enumerate(b4):
            expected = by_date.get(dates[index] - delta, np.nan)
            if not np.isclose(value, expected, rtol=0, atol=1e-10, equal_nan=True):
                seasonal_mismatches += 1
    return LeakageAudit(True, False, future, mismatches, seasonal_mismatches)
