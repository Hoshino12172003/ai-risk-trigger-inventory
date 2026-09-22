"""Solver-independent schemas for future uncertainty calibration reporting."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CalibrationMetrics:
    empirical_coverage: float
    target_coverage: float
    mean_interval_width: float

    @property
    def coverage_gap(self) -> float:
        return self.empirical_coverage - self.target_coverage
