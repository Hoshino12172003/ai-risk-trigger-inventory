"""Solver-free event-driven budgeted uncertainty-set utilities."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import comb
from typing import Mapping, Sequence

from .events import EVENT_TYPES


FLASH_DEMAND_SURGE, REGIONAL_EMERGENCY_DISRUPTION, TRANSPORT_NETWORK_DISRUPTION = EVENT_TYPES


def scenario_count(n: int, gamma: int) -> int:
    """Return sum(k=0..Gamma) C(n,k) without enumerating realizations."""

    if not isinstance(n, int) or n < 0:
        raise ValueError("event count must be a nonnegative integer")
    if not isinstance(gamma, int) or gamma < 0:
        raise ValueError("Gamma must be a nonnegative integer")
    if gamma > n:
        raise ValueError("Gamma cannot exceed the number of events")
    return sum(comb(n, k) for k in range(gamma + 1))


@dataclass(frozen=True)
class EventVector:
    """One realization xi; a scenario is this concrete event vector."""

    event_ids: tuple[str, ...]
    indicators: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.event_ids) != len(self.indicators):
            raise ValueError("event IDs and xi dimensions differ")
        if len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("event IDs must be unique")
        if any(type(value) not in (int, bool) or value not in (0, 1) for value in self.indicators):
            raise ValueError("every xi_e must be binary")

    @property
    def active_events(self) -> tuple[str, ...]:
        return tuple(
            event_id for event_id, value in zip(self.event_ids, self.indicators)
            if value == 1
        )

    def as_dict(self) -> dict[str, int]:
        return dict(zip(self.event_ids, map(int, self.indicators)))


@dataclass(frozen=True)
class EventBudgetSpec:
    """Global Gamma and optional event-type cardinality budgets."""

    gamma: int
    gamma_d: int | None = None
    gamma_r: int | None = None
    gamma_n: int | None = None

    def __post_init__(self) -> None:
        values = (self.gamma, self.gamma_d, self.gamma_r, self.gamma_n)
        if type(self.gamma) is not int or self.gamma < 0:
            raise ValueError("Gamma must be a nonnegative integer")
        if any(value is not None and (type(value) is not int or value < 0) for value in values[1:]):
            raise ValueError("type-specific budgets must be nonnegative integers or None")

    @property
    def category_budgets(self) -> dict[str, int | None]:
        return {
            FLASH_DEMAND_SURGE: self.gamma_d,
            REGIONAL_EMERGENCY_DISRUPTION: self.gamma_r,
            TRANSPORT_NETWORK_DISRUPTION: self.gamma_n,
        }

    def validate_event_types(self, event_types: Mapping[str, str]) -> None:
        if not event_types:
            if self.gamma != 0:
                raise ValueError("nonzero Gamma requires at least one event")
            return
        if len(set(event_types)) != len(event_types):  # pragma: no cover - Mapping keys unique
            raise ValueError("event IDs must be unique")
        unknown = sorted(set(event_types.values()) - set(EVENT_TYPES))
        if unknown:
            raise ValueError(f"unknown event types: {unknown}")
        if self.gamma > len(event_types):
            raise ValueError("Gamma cannot exceed the number of events")

    def is_admissible(
        self,
        event_types: Mapping[str, str],
        xi: EventVector | Mapping[str, int] | Sequence[int],
    ) -> bool:
        self.validate_event_types(event_types)
        event_ids = tuple(sorted(event_types))
        if isinstance(xi, EventVector):
            if xi.event_ids != event_ids:
                raise ValueError("event vector IDs do not match stable event order")
            values = xi.indicators
        elif isinstance(xi, Mapping):
            if set(xi) != set(event_ids):
                raise ValueError("xi keys must equal the event catalog IDs")
            values = tuple(xi[event_id] for event_id in event_ids)
        else:
            values = tuple(xi)
            if len(values) != len(event_ids):
                raise ValueError("xi has incompatible dimension")
        if any(type(value) not in (int, bool) or value not in (0, 1) for value in values):
            raise ValueError("every xi_e must be binary")
        if sum(values) > self.gamma:
            return False
        for event_type, cap in self.category_budgets.items():
            if cap is not None and sum(
                value for event_id, value in zip(event_ids, values)
                if event_types[event_id] == event_type
            ) > cap:
                return False
        return True

    def count(self, event_types: Mapping[str, str]) -> int:
        """Count admissible xi analytically, including optional category caps."""

        self.validate_event_types(event_types)
        sizes = {
            event_type: sum(value == event_type for value in event_types.values())
            for event_type in EVENT_TYPES
        }
        caps = {
            event_type: sizes[event_type] if cap is None else min(cap, sizes[event_type])
            for event_type, cap in self.category_budgets.items()
        }
        total = 0
        for kd in range(caps[FLASH_DEMAND_SURGE] + 1):
            for kr in range(caps[REGIONAL_EMERGENCY_DISRUPTION] + 1):
                for kn in range(caps[TRANSPORT_NETWORK_DISRUPTION] + 1):
                    if kd + kr + kn <= self.gamma:
                        total += (
                            comb(sizes[FLASH_DEMAND_SURGE], kd)
                            * comb(sizes[REGIONAL_EMERGENCY_DISRUPTION], kr)
                            * comb(sizes[TRANSPORT_NETWORK_DISRUPTION], kn)
                        )
        return total

    def enumerate(self, event_types: Mapping[str, str]) -> list[EventVector]:
        """Enumerate a small catalog; callers must not use this for large libraries."""

        self.validate_event_types(event_types)
        if self.count(event_types) > 1_000_000:
            raise ValueError("enumeration is limited to at most one million vectors; use count()")
        event_ids = tuple(sorted(event_types))
        vectors: list[EventVector] = []
        for size in range(self.gamma + 1):
            for active in combinations(range(len(event_ids)), size):
                active_set = set(active)
                vector = EventVector(
                    event_ids,
                    tuple(int(index in active_set) for index in range(len(event_ids))),
                )
                if self.is_admissible(event_types, vector):
                    vectors.append(vector)
        return vectors
