"""Programmatic Gamma-cardinality scenario construction."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable

from .events import EventAtom


REGIMES = {
    "R0_NOMINAL": (),
    "R1_DEMAND_ONLY": ("E1", "E2"),
    "R2_REGIONAL_ONLY": ("E3", "E4"),
    "R3_NETWORK_ONLY": ("E5", "E6"),
    "R4_DEMAND_PLUS_REGIONAL": ("E1", "E2", "E3", "E4"),
    "R5_DEMAND_PLUS_NETWORK": ("E1", "E2", "E5", "E6"),
    "R6_REGIONAL_PLUS_NETWORK": ("E3", "E4", "E5", "E6"),
    "R7_FULL_COMPOUND": ("E1", "E2", "E3", "E4", "E5", "E6"),
}


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    active_events: tuple[str, ...]
    event_types: tuple[str, ...]
    demand_multiplier: tuple[tuple[float, ...], ...]
    route_availability: tuple[tuple[float, ...], ...]


def enumerate_scenarios(
    instance: dict[str, Any], events: Iterable[EventAtom], gamma: int = 2
) -> list[Scenario]:
    """Enumerate empty through Gamma-event combinations in stable order."""

    catalog = sorted(events, key=lambda event: event.event_id)
    if gamma < 0 or gamma > len(catalog):
        raise ValueError("invalid Gamma")
    scenarios: list[Scenario] = []
    sequence = 0
    for size in range(gamma + 1):
        for active in combinations(catalog, size):
            demand = [
                [1.0 for _ in instance["product_ids"]]
                for _ in instance["region_ids"]
            ]
            losses = [
                [0.0 for _ in instance["region_ids"]]
                for _ in instance["depot_ids"]
            ]
            for event in active:
                for r, j, shock in event.demand_shocks:
                    demand[r][j] += shock
                for i, r, loss in event.route_losses:
                    losses[i][r] = min(1.0, losses[i][r] + loss)
            availability = [[1.0 - value for value in row] for row in losses]
            active_ids = tuple(event.event_id for event in active)
            suffix = "_".join(active_ids)
            scenario_id = f"S{sequence:02d}" + (f"_{suffix}" if suffix else "")
            scenario = Scenario(
                scenario_id,
                active_ids,
                tuple(sorted({event.event_type for event in active})),
                tuple(tuple(row) for row in demand),
                tuple(tuple(row) for row in availability),
            )
            validate_scenario(instance, scenario, gamma)
            scenarios.append(scenario)
            sequence += 1
    return scenarios


def validate_scenario(
    instance: dict[str, Any], scenario: Scenario, gamma: int = 2
) -> None:
    if len(scenario.active_events) > gamma:
        raise ValueError("scenario exceeds Gamma")
    if len(scenario.active_events) != len(set(scenario.active_events)):
        raise ValueError("event effect applied more than once")
    for r, row in enumerate(scenario.demand_multiplier):
        for j, multiplier in enumerate(row):
            if float(instance["base_demand"][r][j]) * multiplier < 0:
                raise ValueError("negative scenario demand")
    if any(
        value < 0 or value > 1
        for row in scenario.route_availability
        for value in row
    ):
        raise ValueError("invalid route availability")


def scenarios_for_regime(
    full_catalog: list[Scenario], regime: str
) -> list[Scenario]:
    allowed = set(REGIMES[regime])
    return [s for s in full_catalog if set(s.active_events) <= allowed]
