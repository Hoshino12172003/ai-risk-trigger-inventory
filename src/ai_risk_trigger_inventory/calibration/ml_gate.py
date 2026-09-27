"""Preregistered conditional-ML necessity and value gates."""

from __future__ import annotations

import numpy as np


ML_NECESSITY_THRESHOLDS = {
    "median_group_absolute_q90_coverage_error": 0.05,
    "fraction_groups_absolute_error_over_0_10": 0.25,
    "large_group_error": 0.10,
    "group_q90_iqr": 0.20,
    "minimum_test_group_size": 30,
}

ML_VALUE_THRESHOLDS = {
    "overall_q90_absolute_error_improvement": 0.02,
    "median_group_q90_absolute_error_improvement": 0.02,
    "maximum_q95_absolute_error_worsening": 0.02,
}


def conditional_ml_is_authorized(classification: str) -> bool:
    """Return whether the frozen Stage-3A gate authorizes Stage 3B."""
    return classification == "ML_NEEDED_FOR_CONDITIONAL_CALIBRATION"


def evaluate_ml_necessity(group_absolute_errors, group_q90_values) -> dict[str, object]:
    errors = np.asarray(group_absolute_errors, dtype=float)
    errors = errors[np.isfinite(errors)]
    q90 = np.asarray(group_q90_values, dtype=float)
    q90 = q90[np.isfinite(q90)]
    if len(errors) == 0 or len(q90) == 0:
        raise ValueError("ML gate requires eligible coverage and heterogeneity groups")
    median_error = float(np.median(errors))
    fraction_large = float(np.mean(errors > ML_NECESSITY_THRESHOLDS["large_group_error"]))
    q90_iqr = float(np.quantile(q90, 0.75) - np.quantile(q90, 0.25))
    conditions = {
        "A_median_group_error": median_error > ML_NECESSITY_THRESHOLDS["median_group_absolute_q90_coverage_error"],
        "B_fraction_large_error": fraction_large >= ML_NECESSITY_THRESHOLDS["fraction_groups_absolute_error_over_0_10"],
        "C_q90_dispersion": q90_iqr >= ML_NECESSITY_THRESHOLDS["group_q90_iqr"],
    }
    needed = any(conditions.values())
    return {
        "classification": "ML_NEEDED_FOR_CONDITIONAL_CALIBRATION" if needed else "SIMPLE_QUANTILES_ADEQUATE",
        "ml_needed": needed,
        "conditions": conditions,
        "median_group_absolute_q90_coverage_error": median_error,
        "fraction_groups_absolute_error_over_0_10": fraction_large,
        "eligible_coverage_groups": int(len(errors)),
        "product_state_group_q90_iqr": q90_iqr,
        "eligible_q90_groups": int(len(q90)),
        "thresholds": ML_NECESSITY_THRESHOLDS,
    }
