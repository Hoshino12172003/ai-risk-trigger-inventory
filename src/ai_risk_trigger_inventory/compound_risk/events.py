"""Deterministic construction of the six preregistered event atoms."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


EVENT_TYPES = (
    "FLASH_DEMAND_SURGE",
    "REGIONAL_EMERGENCY_DISRUPTION",
    "TRANSPORT_NETWORK_DISRUPTION",
)


@dataclass(frozen=True)
class EventAtom:
    event_id: str
    event_type: str
    demand_shocks: tuple[tuple[int, int, float], ...]
    route_losses: tuple[tuple[int, int, float], ...]
    selection_note: str

    def to_dict(self, instance: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "demand_shocks": [
                {
                    "region_id": instance["region_ids"][r],
                    "product_id": instance["product_ids"][j],
                    "fraction": shock,
                }
                for r, j, shock in self.demand_shocks
            ],
            "route_losses": [
                {
                    "depot_id": instance["depot_ids"][i],
                    "region_id": instance["region_ids"][r],
                    "fraction": loss,
                }
                for i, r, loss in self.route_losses
            ],
            "selection_note": self.selection_note,
        }


MAGNITUDES = {
    "LOW": {
        "flash": (0.20, 0.30),
        "regional_demand": (0.10, 0.15),
        "regional_route": (0.25, 0.25),
        "network_route": (0.50, 0.75),
    },
    "MAIN": {
        "flash": (0.40, 0.60),
        "regional_demand": (0.20, 0.30),
        "regional_route": (0.50, 0.50),
        "network_route": (0.75, 1.00),
    },
    "HIGH": {
        "flash": (0.60, 0.80),
        "regional_demand": (0.30, 0.40),
        "regional_route": (0.75, 0.75),
        "network_route": (1.00, 1.00),
    },
}


def _assigned_depots(instance: dict[str, Any]) -> dict[int, int]:
    """Return the calibrated minimum-transport-cost node for every region."""

    result: dict[int, int] = {}
    for r, _ in enumerate(instance["region_ids"]):
        candidates = []
        for i, depot_id in enumerate(instance["depot_ids"]):
            mean_cost = sum(instance["transport_cost"][i][r]) / len(
                instance["product_ids"]
            )
            candidates.append((mean_cost, depot_id, i))
        result[r] = min(candidates)[2]
    return result


def build_event_catalog(
    instance: dict[str, Any], magnitude: str = "MAIN"
) -> list[EventAtom]:
    """Build exactly six atoms without inspecting optimization results."""

    if magnitude not in MAGNITUDES:
        raise ValueError(f"unknown magnitude: {magnitude}")
    if len(instance["region_ids"]) != 5 or len(instance["product_ids"]) != 3:
        raise ValueError("Stage 1 requires five regions and three products")
    m = MAGNITUDES[magnitude]
    cells = sorted(
        (
            (-float(instance["base_demand"][r][j]), instance["region_ids"][r],
             instance["product_ids"][j], r, j)
            for r in range(5)
            for j in range(3)
        )
    )
    _, _, _, e1r, e1j = cells[0]
    preferred = [cell for cell in cells if cell[3] != e1r and cell[4] != e1j]
    fallback = [cell for cell in cells if cell[3] != e1r]
    e2_cell = (preferred or fallback)[0]
    e2r, e2j = e2_cell[3], e2_cell[4]
    e2_note = (
        "highest baseline-demand cell with region and product different from E1"
        if preferred
        else "product diversity infeasible; highest cell with region different from E1"
    )

    stable_regions = sorted(range(5), key=lambda r: instance["region_ids"][r])
    group_a, middle, group_b = stable_regions[:2], stable_regions[2], stable_regions[3:]
    assigned = _assigned_depots(instance)
    regional_routes = {(assigned[r], r) for r in (*group_a, *group_b)}
    e5_route = (assigned[middle], middle)
    e6_candidates = sorted(
        (
            (instance["depot_ids"][i], instance["region_ids"][r], i, r)
            for i in range(len(instance["depot_ids"]))
            for r in range(5)
            if i != e5_route[0]
            and r != e5_route[1]
            and (i, r) not in regional_routes
        )
    )
    if not e6_candidates:
        e6_candidates = sorted(
            (
                (instance["depot_ids"][i], instance["region_ids"][r], i, r)
                for i in range(len(instance["depot_ids"]))
                for r in range(5)
                if i != e5_route[0] and r != e5_route[1]
            )
        )
    if not e6_candidates:
        raise ValueError("unable to select a distinct E6 warehouse-region route")
    e6_route = (e6_candidates[0][2], e6_candidates[0][3])

    def regional_shocks(regions: list[int], fraction: float):
        return tuple((r, j, fraction) for r in regions for j in range(3))

    return [
        EventAtom(
            "E1", "FLASH_DEMAND_SURGE", ((e1r, e1j, m["flash"][0]),), (),
            "highest baseline-demand region-product pair",
        ),
        EventAtom(
            "E2", "FLASH_DEMAND_SURGE", ((e2r, e2j, m["flash"][1]),), (),
            e2_note,
        ),
        EventAtom(
            "E3", "REGIONAL_EMERGENCY_DISRUPTION",
            regional_shocks(group_a, m["regional_demand"][0]),
            tuple((assigned[r], r, m["regional_route"][0]) for r in group_a),
            "first two lexically sorted stable region IDs and their assigned routes",
        ),
        EventAtom(
            "E4", "REGIONAL_EMERGENCY_DISRUPTION",
            regional_shocks(group_b, m["regional_demand"][1]),
            tuple((assigned[r], r, m["regional_route"][1]) for r in group_b),
            "last two lexically sorted stable region IDs and their assigned routes",
        ),
        EventAtom(
            "E5", "TRANSPORT_NETWORK_DISRUPTION", (),
            ((e5_route[0], e5_route[1], m["network_route"][0]),),
            "assigned route of the middle lexically sorted region",
        ),
        EventAtom(
            "E6", "TRANSPORT_NETWORK_DISRUPTION", (),
            ((e6_route[0], e6_route[1], m["network_route"][1]),),
            "first stable different-warehouse/different-region route avoiding E3/E4 assigned routes where possible",
        ),
    ]
