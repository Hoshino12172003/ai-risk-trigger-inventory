"""Deterministic empirical quantile, bootstrap, and coverage calculations."""

from __future__ import annotations

import numpy as np


QUANTILES = (0.50, 0.75, 0.90, 0.95, 0.99)
BOOTSTRAP_QUANTILES = (0.75, 0.90, 0.95)
MIN_GROUP_SAMPLE = 30
STABLE_GROUP_SAMPLE = 100
BOOTSTRAP_SEED = 20260927
BOOTSTRAP_RESAMPLES = 1000


def sample_status(sample_size: int) -> str:
    if sample_size < MIN_GROUP_SAMPLE:
        return "INSUFFICIENT_SAMPLE"
    if sample_size < STABLE_GROUP_SAMPLE:
        return "EXPLORATORY_GROUP_QUANTILE"
    return "STABLE_GROUP_SUMMARY"


def empirical_quantiles(values, quantiles=QUANTILES) -> dict[float, float]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if len(array) < MIN_GROUP_SAMPLE:
        return {quantile: np.nan for quantile in quantiles}
    estimates = np.quantile(array, quantiles, method="linear")
    return {quantile: float(value) for quantile, value in zip(quantiles, estimates)}


def bootstrap_quantile_ci(
    values,
    quantile: float,
    *,
    seed: int,
    resamples: int = BOOTSTRAP_RESAMPLES,
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if len(array) < MIN_GROUP_SAMPLE:
        return np.nan, np.nan, np.nan
    estimate = float(np.quantile(array, quantile, method="linear"))
    rng = np.random.default_rng(seed)
    samples = np.empty(resamples, dtype=float)
    batch = 25
    written = 0
    while written < resamples:
        size = min(batch, resamples - written)
        draws = rng.choice(array, size=(size, len(array)), replace=True)
        samples[written:written + size] = np.quantile(draws, quantile, axis=1, method="linear")
        written += size
    tail = (1.0 - confidence) / 2.0
    lower, upper = np.quantile(samples, [tail, 1.0 - tail], method="linear")
    return estimate, float(lower), float(upper)


def empirical_coverage(values, threshold: float) -> float:
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if len(array) == 0 or not np.isfinite(threshold):
        return np.nan
    return float(np.mean(array <= threshold))
