"""Local exact finite-scenario benchmark; deliberately no CCG or PRB calls."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any

from .scenarios import Scenario


@dataclass(frozen=True)
class ScenarioRecourse:
    scenario_id: str
    active_events: tuple[str, ...]
    event_types: tuple[str, ...]
    total_recourse_cost: float
    transport_cost: float
    shortage_cost: float
    service_penalty: float
    total_shortage: float
    minimum_fill_rate: float
    worst_region_fill_rate: float
    rerouted_volume_proxy: float
    backup_warehouse_utilization_proxy: float
    route_loss_exposure: float
    served_demand_under_disrupted_arcs: float


@dataclass(frozen=True)
class PolicyResult:
    mode: str
    status: str
    total_objective: float
    first_stage_cost: float
    reconfiguration_friction: float
    worst_case_recourse_cost: float
    x: list[list[float]]
    y: list[int]
    scenario_results: tuple[ScenarioRecourse, ...]
    runtime_seconds: float
    optimizer_dispatches: int


def _solver_model(name: str, mixed_integer: bool = False):
    try:
        import gurobipy as gp
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Stage 1 exact benchmark requires gurobipy") from exc
    model = gp.Model(name)
    model.Params.OutputFlag = 0
    model.Params.Threads = 1
    model.Params.Seed = 0
    model.Params.OptimalityTol = 1e-9
    model.Params.FeasibilityTol = 1e-9
    if mixed_integer:
        model.Params.MIPGap = 0.0
        model.Params.IntFeasTol = 1e-9
    return model


def _assigned_depot(instance: dict[str, Any], region: int) -> int:
    return min(
        range(len(instance["depot_ids"])),
        key=lambda i: (
            sum(instance["transport_cost"][i][region])
            / len(instance["product_ids"]),
            instance["depot_ids"][i],
        ),
    )


def evaluate_exact_recourse(
    instance: dict[str, Any],
    x: list[list[float]],
    scenarios: list[Scenario],
) -> tuple[tuple[ScenarioRecourse, ...], float, int]:
    """Solve separable cost-only LPs jointly, then a linear shortage tie-break."""

    import gurobipy as gp
    from gurobipy import GRB

    depots = range(len(instance["depot_ids"]))
    regions = range(len(instance["region_ids"]))
    products = range(len(instance["product_ids"]))
    model = _solver_model("compound_risk_exact_recourse")
    q: dict[str, Any] = {}
    u: dict[str, Any] = {}
    e: dict[str, Any] = {}
    transport: dict[str, Any] = {}
    shortage: dict[str, Any] = {}
    service: dict[str, Any] = {}
    costs: dict[str, Any] = {}
    demands: dict[str, list[list[float]]] = {}

    for scenario in scenarios:
        sid = scenario.scenario_id
        demand = [
            [
                float(instance["base_demand"][r][j])
                * scenario.demand_multiplier[r][j]
                for j in products
            ]
            for r in regions
        ]
        demands[sid] = demand
        q[sid] = model.addVars(depots, regions, products, lb=0, name=f"q_{sid}")
        u[sid] = model.addVars(regions, products, lb=0, name=f"u_{sid}")
        e[sid] = model.addVars(products, lb=0, name=f"e_{sid}")
        for r in regions:
            for j in products:
                model.addConstr(
                    gp.quicksum(q[sid][i, r, j] for i in depots) + u[sid][r, j]
                    >= demand[r][j]
                )
                for i in depots:
                    model.addConstr(
                        q[sid][i, r, j]
                        <= scenario.route_availability[i][r] * demand[r][j]
                    )
        for i in depots:
            for j in products:
                model.addConstr(
                    gp.quicksum(q[sid][i, r, j] for r in regions) <= x[i][j]
                )
        for j in products:
            model.addConstr(
                gp.quicksum(u[sid][r, j] for r in regions) - e[sid][j]
                <= (1.0 - instance["service_level"][j])
                * sum(demand[r][j] for r in regions)
            )
        transport[sid] = gp.quicksum(
            instance["transport_cost"][i][r][j] * q[sid][i, r, j]
            for i in depots for r in regions for j in products
        )
        shortage[sid] = gp.quicksum(
            instance["shortage_penalty"][r][j] * u[sid][r, j]
            for r in regions for j in products
        )
        service[sid] = gp.quicksum(
            instance["service_penalty"][j] * e[sid][j] for j in products
        )
        costs[sid] = transport[sid] + shortage[sid] + service[sid]

    started = perf_counter()
    model.setObjective(gp.quicksum(costs.values()), GRB.MINIMIZE)
    model.optimize()
    if model.Status != GRB.OPTIMAL:
        raise RuntimeError(f"exact recourse did not reach OPTIMAL: {model.Status}")
    exact_costs = {sid: float(expr.getValue()) for sid, expr in costs.items()}
    for sid, optimum in exact_costs.items():
        tolerance = 1e-8 * max(1.0, abs(optimum))
        model.addConstr(costs[sid] <= optimum + tolerance)
    model.setObjective(
        gp.quicksum(u[s.scenario_id][r, j] for s in scenarios for r in regions for j in products),
        GRB.MINIMIZE,
    )
    model.optimize()
    if model.Status != GRB.OPTIMAL:
        raise RuntimeError(f"linear shortage tie-break did not reach OPTIMAL: {model.Status}")

    results: list[ScenarioRecourse] = []
    for scenario in scenarios:
        sid = scenario.scenario_id
        demand = demands[sid]
        transport_value = float(transport[sid].getValue())
        shortage_value = float(shortage[sid].getValue())
        service_value = float(service[sid].getValue())
        recourse = transport_value + shortage_value + service_value
        total_shortage = sum(u[sid][r, j].X for r in regions for j in products)
        cell_fill = [
            1.0 if demand[r][j] == 0 else 1.0 - u[sid][r, j].X / demand[r][j]
            for r in regions for j in products
        ]
        region_fill = []
        for r in regions:
            region_demand = sum(demand[r])
            region_shortage = sum(u[sid][r, j].X for j in products)
            region_fill.append(
                1.0 if region_demand == 0 else 1.0 - region_shortage / region_demand
            )
        rerouted = sum(
            q[sid][i, r, j].X
            for r in regions for j in products for i in depots
            if i != _assigned_depot(instance, r)
        )
        served = sum(q[sid][i, r, j].X for i in depots for r in regions for j in products)
        disrupted_served = sum(
            q[sid][i, r, j].X
            for i in depots for r in regions for j in products
            if scenario.route_availability[i][r] < 1.0
        )
        exposure = sum(
            (1.0 - scenario.route_availability[i][r]) * demand[r][j]
            for i in depots for r in regions for j in products
        )
        if abs(recourse - float(costs[sid].getValue())) > 1e-5:
            raise RuntimeError("recourse component identity failed")
        results.append(
            ScenarioRecourse(
                sid, scenario.active_events, scenario.event_types, recourse,
                transport_value, shortage_value, service_value, total_shortage,
                min(cell_fill), min(region_fill), rerouted,
                0.0 if served == 0 else rerouted / served,
                exposure, disrupted_served,
            )
        )
    return tuple(results), perf_counter() - started, 2


def solve_keep(
    instance: dict[str, Any], x0: list[list[float]], y0: list[int],
    scenarios: list[Scenario],
) -> PolicyResult:
    results, runtime, dispatches = evaluate_exact_recourse(instance, x0, scenarios)
    first_stage = sum(
        instance["fixed_depot_cost"][i] * y0[i]
        for i in range(len(y0))
    ) + sum(
        instance["inventory_cost"][i][j] * x0[i][j]
        for i in range(len(x0)) for j in range(len(x0[i]))
    )
    worst = max(row.total_recourse_cost for row in results)
    return PolicyResult(
        "KEEP", "OPTIMAL", first_stage + worst, first_stage, 0.0, worst,
        [list(row) for row in x0], list(y0), results, runtime, dispatches,
    )


def solve_reoptimize(
    instance: dict[str, Any], x0: list[list[float]], budget: float,
    lambda_r: float, scenarios: list[Scenario],
) -> PolicyResult:
    """Solve the exact robust extensive form and audit its recourse at fixed x."""

    import gurobipy as gp
    from gurobipy import GRB

    depots = range(len(instance["depot_ids"]))
    regions = range(len(instance["region_ids"]))
    products = range(len(instance["product_ids"]))
    model = _solver_model("compound_risk_extensive_form", mixed_integer=True)
    y = model.addVars(depots, vtype=GRB.BINARY, name="y")
    x = model.addVars(depots, products, lb=0, name="x")
    plus = model.addVars(depots, products, lb=0, name="a_plus")
    minus = model.addVars(depots, products, lb=0, name="a_minus")
    for i in depots:
        model.addConstr(
            gp.quicksum(instance["product_volume"][j] * x[i, j] for j in products)
            <= instance["capacity"][i] * y[i]
        )
        for j in products:
            model.addConstr(x[i, j] <= instance["inventory_upper_bound"][i][j] * y[i])
            model.addConstr(x[i, j] - x0[i][j] == plus[i, j] - minus[i, j])
    base_first_stage = gp.quicksum(
        instance["fixed_depot_cost"][i] * y[i] for i in depots
    ) + gp.quicksum(
        instance["inventory_cost"][i][j] * x[i, j] for i in depots for j in products
    )
    friction = lambda_r * gp.quicksum(
        instance["inventory_cost"][i][j] * (plus[i, j] + minus[i, j])
        for i in depots for j in products
    )
    first_stage = base_first_stage + friction
    model.addConstr(first_stage <= budget)
    theta = model.addVar(lb=0, name="theta")
    for scenario in scenarios:
        sid = scenario.scenario_id
        q = model.addVars(depots, regions, products, lb=0, name=f"q_{sid}")
        u = model.addVars(regions, products, lb=0, name=f"u_{sid}")
        e = model.addVars(products, lb=0, name=f"e_{sid}")
        demand = [
            [instance["base_demand"][r][j] * scenario.demand_multiplier[r][j]
             for j in products]
            for r in regions
        ]
        for r in regions:
            for j in products:
                model.addConstr(gp.quicksum(q[i, r, j] for i in depots) + u[r, j] >= demand[r][j])
                for i in depots:
                    model.addConstr(q[i, r, j] <= scenario.route_availability[i][r] * demand[r][j])
        for i in depots:
            for j in products:
                model.addConstr(gp.quicksum(q[i, r, j] for r in regions) <= x[i, j])
        for j in products:
            model.addConstr(
                gp.quicksum(u[r, j] for r in regions) - e[j]
                <= (1.0 - instance["service_level"][j]) * sum(demand[r][j] for r in regions)
            )
        scenario_cost = gp.quicksum(
            instance["transport_cost"][i][r][j] * q[i, r, j]
            for i in depots for r in regions for j in products
        ) + gp.quicksum(
            instance["shortage_penalty"][r][j] * u[r, j]
            for r in regions for j in products
        ) + gp.quicksum(instance["service_penalty"][j] * e[j] for j in products)
        model.addConstr(theta >= scenario_cost)
    model.setObjective(first_stage + theta, GRB.MINIMIZE)
    started = perf_counter()
    model.optimize()
    if model.Status != GRB.OPTIMAL:
        raise RuntimeError(f"extensive form did not reach exact OPTIMAL: {model.Status}")
    solved_x = [[x[i, j].X for j in products] for i in depots]
    solved_y = [int(round(y[i].X)) for i in depots]
    model_runtime = perf_counter() - started
    scenario_results, audit_runtime, audit_dispatches = evaluate_exact_recourse(
        instance, solved_x, scenarios
    )
    robust = max(row.total_recourse_cost for row in scenario_results)
    if abs(robust - theta.X) > 1e-4 * max(1.0, abs(theta.X)):
        raise RuntimeError("extensive-form recourse identity failed")
    friction_value = float(friction.getValue())
    base_value = float(base_first_stage.getValue())
    objective = base_value + friction_value + robust
    if abs(objective - model.ObjVal) > 1e-4 * max(1.0, abs(model.ObjVal)):
        raise RuntimeError("reconfiguration friction or total cost identity failed")
    return PolicyResult(
        "REOPTIMIZE", "OPTIMAL", objective, base_value, friction_value, robust,
        solved_x, solved_y, scenario_results, model_runtime + audit_runtime,
        1 + audit_dispatches,
    )
