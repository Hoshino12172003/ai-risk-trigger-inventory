"""Mathematical parameter schema and Stage-1-to-model notation audit metadata."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping, Sequence

from .events import EVENT_TYPES, EventAtom
from .uncertainty_set import EventVector


@dataclass(frozen=True)
class EventImpactParameters:
    """Externally supplied calibrated impacts accepted by the Stage-2 model."""

    event_ids: tuple[str, ...]
    event_types: tuple[str, ...]
    warehouse_ids: tuple[str, ...]
    region_ids: tuple[str, ...]
    product_ids: tuple[str, ...]
    baseline_demand: tuple[tuple[float, ...], ...]
    demand_impacts: tuple[tuple[tuple[float, ...], ...], ...]
    route_loss_impacts: tuple[tuple[tuple[float, ...], ...], ...]

    def __post_init__(self) -> None:
        self.validate()

    @classmethod
    def from_stage1(
        cls, instance: dict[str, Any], events: Sequence[EventAtom]
    ) -> "EventImpactParameters":
        ordered = sorted(events, key=lambda event: event.event_id)
        nr, nj = len(instance["region_ids"]), len(instance["product_ids"])
        ni = len(instance["depot_ids"])
        demand = []
        route = []
        for event in ordered:
            event_demand = [[0.0 for _ in range(nj)] for _ in range(nr)]
            event_route = [[0.0 for _ in range(nr)] for _ in range(ni)]
            for r, j, value in event.demand_shocks:
                event_demand[r][j] = float(value)
            for i, r, value in event.route_losses:
                event_route[i][r] = float(value)
            demand.append(tuple(tuple(row) for row in event_demand))
            route.append(tuple(tuple(row) for row in event_route))
        return cls(
            tuple(event.event_id for event in ordered),
            tuple(event.event_type for event in ordered),
            tuple(instance["depot_ids"]), tuple(instance["region_ids"]),
            tuple(instance["product_ids"]),
            tuple(tuple(map(float, row)) for row in instance["base_demand"]),
            tuple(demand), tuple(route),
        )

    @property
    def event_type_map(self) -> dict[str, str]:
        return dict(zip(self.event_ids, self.event_types))

    def validate(self) -> None:
        ne, ni, nr, nj = (
            len(self.event_ids), len(self.warehouse_ids),
            len(self.region_ids), len(self.product_ids),
        )
        for name, values in (
            ("event_ids", self.event_ids), ("warehouse_ids", self.warehouse_ids),
            ("region_ids", self.region_ids), ("product_ids", self.product_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must be unique")
        if len(self.event_types) != ne or set(self.event_types) - set(EVENT_TYPES):
            raise ValueError("event types must form the declared three-type partition")
        if len(self.baseline_demand) != nr or any(len(row) != nj for row in self.baseline_demand):
            raise ValueError("baseline demand dimension mismatch")
        if any(value < 0 or not isfinite(value) for row in self.baseline_demand for value in row):
            raise ValueError("baseline demand must be finite and nonnegative")
        if len(self.demand_impacts) != ne or any(
            len(matrix) != nr or any(len(row) != nj for row in matrix)
            for matrix in self.demand_impacts
        ):
            raise ValueError("demand impact dimension mismatch")
        if any(value < 0 or not isfinite(value) for matrix in self.demand_impacts for row in matrix for value in row):
            raise ValueError("demand impacts must be finite and nonnegative")
        if len(self.route_loss_impacts) != ne or any(
            len(matrix) != ni or any(len(row) != nr for row in matrix)
            for matrix in self.route_loss_impacts
        ):
            raise ValueError("route-loss impact dimension mismatch")
        if any(
            value < 0 or value > 1 or not isfinite(value)
            for matrix in self.route_loss_impacts for row in matrix for value in row
        ):
            raise ValueError("each event route-loss impact must lie in [0,1]")

    def _xi(self, xi: EventVector | Mapping[str, int] | Sequence[int]) -> tuple[int, ...]:
        if isinstance(xi, EventVector):
            if xi.event_ids != self.event_ids:
                raise ValueError("event vector IDs do not match impact parameters")
            return tuple(map(int, xi.indicators))
        if isinstance(xi, Mapping):
            if set(xi) != set(self.event_ids):
                raise ValueError("xi keys do not match impact parameters")
            values = tuple(xi[event_id] for event_id in self.event_ids)
        else:
            values = tuple(xi)
        if len(values) != len(self.event_ids):
            raise ValueError("xi dimension mismatch")
        if any(type(value) not in (int, bool) or value not in (0, 1) for value in values):
            raise ValueError("every xi_e must be binary")
        return tuple(map(int, values))

    def demand_multiplier(
        self, xi: EventVector | Mapping[str, int] | Sequence[int]
    ) -> tuple[tuple[float, ...], ...]:
        values = self._xi(xi)
        return tuple(
            tuple(
                1.0 + sum(
                    self.demand_impacts[e][r][j] * values[e]
                    for e in range(len(self.event_ids))
                )
                for j in range(len(self.product_ids))
            )
            for r in range(len(self.region_ids))
        )

    def demand(
        self, xi: EventVector | Mapping[str, int] | Sequence[int]
    ) -> tuple[tuple[float, ...], ...]:
        multiplier = self.demand_multiplier(xi)
        return tuple(
            tuple(self.baseline_demand[r][j] * multiplier[r][j] for j in range(len(self.product_ids)))
            for r in range(len(self.region_ids))
        )

    def route_availability(
        self, xi: EventVector | Mapping[str, int] | Sequence[int]
    ) -> tuple[tuple[float, ...], ...]:
        values = self._xi(xi)
        return tuple(
            tuple(
                max(0.0, 1.0 - sum(
                    self.route_loss_impacts[e][i][r] * values[e]
                    for e in range(len(self.event_ids))
                ))
                for r in range(len(self.region_ids))
            )
            for i in range(len(self.warehouse_ids))
        )


MODEL_NOTATION = {
    "sets": {"I": "warehouses", "R": "demand regions", "J": "products", "E": "event atoms"},
    "first_stage_variables": {"y_i": "y[i]", "x_ij": "x[i,j]", "a+_ij": "plus[i,j]", "a-_ij": "minus[i,j]"},
    "event_variable": {"xi_e": "EventVector.indicators[e]"},
    "second_stage_variables": {"q_irj(xi)": "q[i,r,j]", "u_rj(xi)": "u[r,j]", "e_j(xi)": "e[j]"},
}


IMPLEMENTATION_MATH_MAPPING = (
    ("warehouse volume/capacity", "sum_j volume_j x_ij <= capacity_i y_i"),
    ("inventory upper bound/fixed-cost linkage", "x_ij <= upper_ij y_i"),
    ("reconfiguration accounting", "x_ij - x0_ij = a+_ij - a-_ij"),
    ("financial budget", "fixed + inventory + friction <= B"),
    ("demand coverage", "sum_i q_irj(xi) + u_rj(xi) >= d_rj(xi)"),
    ("inventory availability", "sum_r q_irj(xi) <= x_ij"),
    ("route service", "q_irj(xi) <= a_ir(xi) d_rj(xi)"),
    ("aggregate product service", "sum_r u_rj(xi) - e_j(xi) <= (1-alpha_j) sum_r d_rj(xi)"),
    ("recourse objective", "sum c_irj q_irj + sum p_rj u_rj + sum pi_j e_j"),
    ("robust epigraph", "theta >= recourse_cost(xi) for each xi"),
    ("full objective", "fixed + inventory + friction + theta"),
)
