"""Solver-independent schemas for future decision evaluation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DecisionMetrics:
    decision_value: float
    relative_decision_value: float
    inventory_change_l1: float
    changed_inventory_pairs: int


def decision_value(total_cost_keep: float, total_cost_reoptimize: float) -> float:
    """Return KEEP minus REOPTIMIZE without modifying either objective."""

    return total_cost_keep - total_cost_reoptimize
