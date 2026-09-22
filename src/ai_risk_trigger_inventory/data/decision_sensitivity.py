"""Pre-solve construction rules for the decision-sensitivity pilot."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean


TARGET_STATES = ("Pichincha", "Guayas")
PRIORITY_FAMILIES = (
    "GROCERY I",
    "BEVERAGES",
    "PRODUCE",
    "CLEANING",
    "DAIRY",
    "BREAD/BAKERY",
)
BASELINE_WEEKS = 4
CAPACITY_LOOKBACK_WEEKS = 8
MIN_BASELINE_DEMAND = 1.0
INCUMBENT_COVERAGE = 1.10
CAPACITY_BUFFER = 1.20
FORBIDDEN_INPUT_FIELDS = frozenset(
    {
        "objective",
        "RI",
        "optimization_value",
        "decision_value",
        "iterations",
        "runtime",
        "solver_iterations",
        "total_cost_keep",
        "total_cost_reopt",
    }
)


@dataclass(frozen=True)
class InventoryCalibration:
    """Calibrated inventory inputs; none of these are observed inventory."""

    baseline_demand: float
    incumbent_inventory: float
    capacity: float
    source: str = "CALIBRATED"


def select_stores(store_demand: Mapping[str, float], target_count: int = 5) -> tuple[str, ...]:
    """Select 4--6 demand regions deterministically across the demand ranking."""

    if len(store_demand) < 4:
        raise ValueError("at least four stores are required for a network context")
    count = min(max(target_count, 4), 6, len(store_demand))
    ranked = sorted(store_demand, key=lambda store: (store_demand[store], str(store)))
    indices = [round(index * (len(ranked) - 1) / (count - 1)) for index in range(count)]
    return tuple(ranked[index] for index in indices)


def select_store_contexts(
    store_demand: Mapping[str, float],
    context_count: int,
    target_count: int = 5,
) -> tuple[tuple[str, ...], ...]:
    """Create deterministic low-to-high demand mixes for multiple contexts."""

    if context_count < 1:
        raise ValueError("at least one network context is required")
    ranked = sorted(store_demand, key=lambda store: (store_demand[store], str(store)))
    base = select_stores(store_demand, target_count)
    base_indices = [ranked.index(store) for store in base]
    offsets = [round(index * len(ranked) / context_count) for index in range(context_count)]
    return tuple(
        tuple(ranked[(base_index + offset) % len(ranked)] for base_index in base_indices)
        for offset in offsets
    )


def select_families(available: Iterable[str], count: int = 3) -> tuple[str, ...]:
    """Select two or three families using the preregistered priority order."""

    available_set = {str(family).upper() for family in available}
    selected = tuple(family for family in PRIORITY_FAMILIES if family in available_set)[:count]
    if len(selected) < 2:
        raise ValueError("at least two priority families are required")
    return selected


def rolling_baseline(history: Sequence[float], window: int = BASELINE_WEEKS) -> float:
    """Return the mean of the last complete historical weeks."""

    if len(history) < window:
        raise ValueError(f"at least {window} historical weeks are required")
    baseline = float(fmean(history[-window:]))
    if baseline < MIN_BASELINE_DEMAND:
        raise ValueError(
            f"baseline demand {baseline:.6g} is below the fixed minimum "
            f"{MIN_BASELINE_DEMAND:.6g}"
        )
    return baseline


def demand_shift(current_demand: float, baseline_demand: float) -> dict[str, float]:
    """Calculate shift measures after enforcing the denominator rule."""

    if baseline_demand < MIN_BASELINE_DEMAND:
        raise ValueError("baseline demand is too small for a stable relative shift")
    absolute = float(current_demand - baseline_demand)
    relative = absolute / baseline_demand
    return {
        "absolute_demand_shift": absolute,
        "relative_demand_shift": relative,
        "positive_demand_shift": max(relative, 0.0),
        "negative_demand_shift": min(relative, 0.0),
    }


def calibrate_inventory(
    history: Sequence[float],
    *,
    coverage: float = INCUMBENT_COVERAGE,
    capacity_buffer: float = CAPACITY_BUFFER,
) -> InventoryCalibration:
    """Calibrate nominal incumbent inventory and capacity from demand history."""

    baseline = rolling_baseline(history)
    lookback = history[-CAPACITY_LOOKBACK_WEEKS:]
    peak = max(float(value) for value in lookback)
    capacity = max(coverage * baseline, capacity_buffer * peak)
    incumbent = min(capacity, coverage * baseline)
    return InventoryCalibration(baseline, incumbent, capacity)


def concentration(values: Sequence[float]) -> float:
    """Return the Herfindahl concentration of nonnegative quantities."""

    total = sum(values)
    if total <= 0:
        return 0.0
    return sum((value / total) ** 2 for value in values)


def assert_no_post_solve_leakage(field_names: Iterable[str]) -> None:
    """Reject decision-state schemas containing post-solve information."""

    leaked = FORBIDDEN_INPUT_FIELDS & set(field_names)
    if leaked:
        raise ValueError(f"post-solve leakage fields are forbidden: {sorted(leaked)}")
