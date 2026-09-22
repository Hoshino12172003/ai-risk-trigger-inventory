"""Preregistered evidence rules for the exploratory feasibility pilot."""

from __future__ import annotations

from dataclasses import dataclass


STRONG_STRUCTURAL_SIGNAL = "STRONG_STRUCTURAL_SIGNAL"
PARTIAL_STRUCTURAL_SIGNAL = "PARTIAL_STRUCTURAL_SIGNAL"
WEAK_OR_NO_STRUCTURAL_SIGNAL = "WEAK_OR_NO_STRUCTURAL_SIGNAL"

DEMAND_ONLY_LIMITED_R2_MAX = 0.70
DEMAND_ONLY_DOMINANT_R2_MIN = 0.80
STRONG_ADJUSTED_R2_GAIN_MIN = 0.15
PARTIAL_ADJUSTED_R2_GAIN_MIN = 0.05
STRONG_MATCHED_PAIRS_MIN = 3
PARTIAL_MATCHED_PAIRS_MIN = 1
STRONG_EXECUTED_STATES_MIN = 20
MATCH_SHIFT_TOLERANCE = 0.05
MATCH_DECISION_VALUE_RATIO_MIN = 2.0
MATCH_ABSOLUTE_GAP_MEDIAN_MULTIPLIER = 0.50


@dataclass(frozen=True)
class StructuralEvidence:
    executed_states: int
    demand_only_r2: float
    adjusted_r2_gain: float
    matched_pairs: int
    represented_states: int
    represented_family_sets: int


def classify_structural_signal(evidence: StructuralEvidence) -> str:
    """Apply fixed thresholds without looking at feature signs or narratives."""

    if (
        evidence.executed_states >= STRONG_EXECUTED_STATES_MIN
        and evidence.demand_only_r2 <= DEMAND_ONLY_LIMITED_R2_MAX
        and evidence.adjusted_r2_gain >= STRONG_ADJUSTED_R2_GAIN_MIN
        and evidence.matched_pairs >= STRONG_MATCHED_PAIRS_MIN
        and evidence.represented_states >= 2
        and evidence.represented_family_sets >= 2
    ):
        return STRONG_STRUCTURAL_SIGNAL

    if (
        evidence.demand_only_r2 >= DEMAND_ONLY_DOMINANT_R2_MIN
        and evidence.adjusted_r2_gain < PARTIAL_ADJUSTED_R2_GAIN_MIN
        and evidence.matched_pairs < PARTIAL_MATCHED_PAIRS_MIN
    ):
        return WEAK_OR_NO_STRUCTURAL_SIGNAL

    if (
        evidence.adjusted_r2_gain >= PARTIAL_ADJUSTED_R2_GAIN_MIN
        or evidence.matched_pairs >= PARTIAL_MATCHED_PAIRS_MIN
    ):
        return PARTIAL_STRUCTURAL_SIGNAL

    return WEAK_OR_NO_STRUCTURAL_SIGNAL
