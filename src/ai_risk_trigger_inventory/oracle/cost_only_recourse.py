"""Exact Stage-1-only robust recourse evaluation for the frozen Paper-2 model."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from time import perf_counter
from typing import Any


@dataclass(frozen=True)
class CostOnlyRecourseResult:
    status: str
    robust_recourse_cost: float
    joint_block_objective: float
    block_optimum_costs: dict[tuple[int, tuple[int, ...]], float]
    worst_scenario: tuple[tuple[int, int], ...]
    runtime: float
    solver_dispatches: int = 1


def compose_robust_recourse_cost(
    scenarios: list[tuple[tuple[int, int], ...]],
    block_optima: dict[tuple[int, tuple[int, ...]], float],
    product_count: int,
) -> tuple[float, tuple[tuple[int, int], ...]]:
    """Combine exact product/block optima using the frozen scenario mapping."""

    values = [
        (
            sum(
                block_optima[
                    (
                        product,
                        tuple(
                            region
                            for region, item in scenario
                            if item == product
                        ),
                    )
                ]
                for product in range(product_count)
            ),
            scenario,
        )
        for scenario in scenarios
    ]
    return max(values, key=lambda item: item[0])


def evaluate_cost_only_robust_recourse(
    instance: Any,
    x: list[list[float]],
    gamma: int,
) -> CostOnlyRecourseResult:
    """Run the frozen Stage-1 LP and omit its quadratic reporting tie-break."""

    import gurobipy as gp
    from gurobipy import GRB
    from robust_inventory_reconfiguration.scenarios import (
        enumerate_scenario_components,
    )
    from robust_inventory_reconfiguration.solver_profile import (
        apply_formal_solver_profile,
    )

    if len(x) != instance.num_depots or any(
        len(row) != instance.num_products for row in x
    ):
        raise ValueError("inventory has incompatible dimensions")
    if gamma < 0 or gamma > instance.num_regions:
        raise ValueError("gamma must be between zero and the number of regions")

    depots = range(instance.num_depots)
    regions = range(instance.num_regions)
    model = gp.Model(f"cost_only_service_{instance.name}_g{gamma}")
    model.Params.OutputFlag = 0
    apply_formal_solver_profile(model, mixed_integer=False)
    costs: dict[tuple[int, tuple[int, ...]], Any] = {}

    for product in range(instance.num_products):
        for product_gamma in range(gamma + 1):
            for shocked_regions in combinations(regions, product_gamma):
                key = (product, shocked_regions)
                q = model.addVars(
                    depots,
                    regions,
                    lb=0,
                    name=f"q_{product}_{'_'.join(map(str, shocked_regions))}",
                )
                u = model.addVars(
                    regions,
                    lb=0,
                    name=f"u_{product}_{'_'.join(map(str, shocked_regions))}",
                )
                e = model.addVar(
                    lb=0,
                    name=f"e_{product}_{'_'.join(map(str, shocked_regions))}",
                )
                shocked = set(shocked_regions)
                scenario_demand = []
                for region in regions:
                    demand = instance.base_demand[region][product]
                    if region in shocked:
                        demand += instance.demand_deviation[region][product]
                    scenario_demand.append(demand)
                    model.addConstr(
                        gp.quicksum(q[depot, region] for depot in depots)
                        + u[region]
                        >= demand
                    )
                for depot in depots:
                    model.addConstr(
                        gp.quicksum(q[depot, region] for region in regions)
                        <= x[depot][product]
                    )
                model.addConstr(
                    gp.quicksum(u[region] for region in regions) - e
                    <= (1.0 - instance.service_level[product])
                    * sum(scenario_demand)
                )
                costs[key] = (
                    gp.quicksum(
                        instance.transport_cost[depot][region][product]
                        * q[depot, region]
                        for depot in depots
                        for region in regions
                    )
                    + gp.quicksum(
                        instance.shortage_penalty[region][product] * u[region]
                        for region in regions
                    )
                    + instance.service_penalty[product] * e
                )

    model.setObjective(gp.quicksum(costs.values()), GRB.MINIMIZE)
    started = perf_counter()
    model.optimize()
    runtime = perf_counter() - started
    if model.Status != GRB.OPTIMAL:
        raise RuntimeError(f"cost-only Stage-1 recourse failed: {model.Status}")
    block_optima = {key: float(cost.getValue()) for key, cost in costs.items()}
    robust_cost, worst_scenario = compose_robust_recourse_cost(
        enumerate_scenario_components(instance, gamma),
        block_optima,
        instance.num_products,
    )
    return CostOnlyRecourseResult(
        status="OPTIMAL",
        robust_recourse_cost=robust_cost,
        joint_block_objective=float(model.ObjVal),
        block_optimum_costs=block_optima,
        worst_scenario=worst_scenario,
        runtime=runtime,
    )
