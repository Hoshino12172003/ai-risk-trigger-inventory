"""Diagnose the frozen KEEP reporting tie-break for one authorized state."""

from __future__ import annotations

import argparse
from itertools import combinations
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
FROZEN_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
STATE_ID = "pichincha-pichincha-context-2-20140106"
OPTIMALITY_TOLERANCE = 1e-7


def _status_name(grb: Any, status: int) -> str:
    names = {
        grb.LOADED: "LOADED",
        grb.OPTIMAL: "OPTIMAL",
        grb.INFEASIBLE: "INFEASIBLE",
        grb.INF_OR_UNBD: "INF_OR_UNBD",
        grb.UNBOUNDED: "UNBOUNDED",
        grb.NUMERIC: "NUMERIC",
        grb.SUBOPTIMAL: "SUBOPTIMAL",
    }
    return names.get(status, f"STATUS_{status}")


def _optional_attr(model: Any, name: str) -> float | int | None:
    try:
        value = model.getAttr(name)
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return float(value) if isinstance(value, float) else int(value)
    except Exception:
        return None


def _absolute_range(values: list[float]) -> dict[str, float | int | None]:
    nonzero = [abs(float(value)) for value in values if abs(float(value)) > 0]
    return {
        "min_nonzero": min(nonzero) if nonzero else None,
        "max": max(nonzero) if nonzero else None,
        "nonzero_count": len(nonzero),
    }


def _model_coefficient_ranges(model: Any) -> dict[str, Any]:
    model.update()
    matrix: list[float] = []
    for constraint in model.getConstrs():
        row = model.getRow(constraint)
        matrix.extend(row.getCoeff(index) for index in range(row.size()))
    variables = model.getVars()
    finite_bounds = [
        value
        for variable in variables
        for value in (variable.LB, variable.UB)
        if math.isfinite(value) and value != 0
    ]
    return {
        "matrix_coefficient_range": _absolute_range(matrix),
        "linear_objective_coefficient_range": _absolute_range(
            [variable.Obj for variable in variables]
        ),
        "rhs_range": _absolute_range(
            [constraint.RHS for constraint in model.getConstrs()]
        ),
        "finite_nonzero_variable_bound_range": _absolute_range(finite_bounds),
    }


def exact_robust_recourse_cost(
    scenarios: list[tuple[tuple[int, int], ...]],
    block_optima: dict[tuple[int, tuple[int, ...]], float],
    product_count: int,
) -> float:
    """Compose the frozen robust cost from exact independent block optima."""

    return max(
        sum(
            block_optima[
                (product, tuple(region for region, item in scenario if item == product))
            ]
            for product in range(product_count)
        )
        for scenario in scenarios
    )


def _solve_audit(model: Any, grb: Any, started: float) -> dict[str, Any]:
    status = int(model.Status)
    return {
        "status_code": status,
        "status": _status_name(grb, status),
        "runtime_seconds": perf_counter() - started,
        "solver_runtime_seconds": float(model.Runtime),
        "objective_value": float(model.ObjVal) if model.SolCount else None,
        "objective_bound": _optional_attr(model, "ObjBound"),
        "solution_count": int(model.SolCount),
        "iteration_count": _optional_attr(model, "IterCount"),
        "barrier_iteration_count": _optional_attr(model, "BarIterCount"),
        "primal_max_violation": _optional_attr(model, "MaxVio"),
        "constraint_violation": _optional_attr(model, "ConstrVio"),
        "bound_violation": _optional_attr(model, "BoundVio"),
        "dual_violation": _optional_attr(model, "DualVio"),
        "kappa": _optional_attr(model, "Kappa"),
        "kappa_exact": _optional_attr(model, "KappaExact"),
    }


def _solver_parameters(model: Any) -> dict[str, Any]:
    return {
        "NumericFocus": int(model.Params.NumericFocus),
        "OptimalityTol": float(model.Params.OptimalityTol),
        "FeasibilityTol": float(model.Params.FeasibilityTol),
        "BarConvTol": float(model.Params.BarConvTol),
        "QCPDual": int(model.Params.QCPDual),
    }


def _optimize_with_log(model: Any, path: Path) -> tuple[float, str]:
    model.Params.LogFile = str(path)
    model.Params.OutputFlag = 1
    started = perf_counter()
    model.optimize()
    elapsed = perf_counter() - started
    model.Params.OutputFlag = 0
    model.Params.LogFile = ""
    return elapsed, path.read_text(encoding="utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument(
        "--payloads",
        type=Path,
        default=ROOT / "artifacts/decision_sensitivity_pilot/dry_run_oracle_payloads.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts/decision_sensitivity_pilot",
    )
    args = parser.parse_args()
    checkout = args.checkout.resolve()
    if subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
    ).strip() != FROZEN_SHA:
        raise SystemExit("frozen checkout SHA mismatch")
    if subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=checkout, text=True
    ).strip():
        raise SystemExit("frozen checkout is not clean")

    entries = json.loads(args.payloads.read_text(encoding="utf-8"))
    entry = next(item for item in entries if item["state_id"] == STATE_ID)
    payload = entry["payloads"][0]
    if payload["decision_mode"] != "KEEP":
        raise SystemExit("diagnostic payload is not KEEP")

    source = str(checkout / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    import gurobipy as gp
    from gurobipy import GRB
    from robust_inventory_reconfiguration.instance import InventoryInstance
    from robust_inventory_reconfiguration.scenarios import enumerate_scenario_components
    from robust_inventory_reconfiguration.solver_profile import (
        FORMAL_SOLVER_PROFILE_ID,
        apply_formal_solver_profile,
    )

    instance = InventoryInstance.from_dict(payload["instance"])
    x0 = payload["x0"]
    gamma = int(payload["gamma"])
    depots = range(instance.num_depots)
    regions = range(instance.num_regions)
    model = gp.Model(f"keep_tiebreak_diagnostic_{STATE_ID}")
    model.Params.OutputFlag = 0
    apply_formal_solver_profile(model, mixed_integer=False)
    costs: dict[tuple[int, tuple[int, ...]], Any] = {}
    shortages: dict[tuple[int, tuple[int, ...]], Any] = {}

    for product in range(instance.num_products):
        for product_gamma in range(gamma + 1):
            for shocked_regions in combinations(regions, product_gamma):
                key = (product, shocked_regions)
                q = model.addVars(
                    depots, regions, lb=0,
                    name=f"q_{product}_{'_'.join(map(str, shocked_regions))}",
                )
                u = model.addVars(
                    regions, lb=0,
                    name=f"u_{product}_{'_'.join(map(str, shocked_regions))}",
                )
                e = model.addVar(
                    lb=0, name=f"e_{product}_{'_'.join(map(str, shocked_regions))}"
                )
                shocked = set(shocked_regions)
                scenario_demand = []
                for region in regions:
                    demand = instance.base_demand[region][product]
                    if region in shocked:
                        demand += instance.demand_deviation[region][product]
                    scenario_demand.append(demand)
                    model.addConstr(
                        gp.quicksum(q[depot, region] for depot in depots) + u[region]
                        >= demand
                    )
                for depot in depots:
                    model.addConstr(
                        gp.quicksum(q[depot, region] for region in regions)
                        <= x0[depot][product]
                    )
                model.addConstr(
                    gp.quicksum(u[region] for region in regions) - e
                    <= (1.0 - instance.service_level[product]) * sum(scenario_demand)
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
                shortages[key] = u

    model.setObjective(gp.quicksum(costs.values()), GRB.MINIMIZE)
    model.update()
    coefficient_ranges = _model_coefficient_ranges(model)
    dispatches = 1
    stage1_started = perf_counter()
    model.optimize()
    stage1 = _solve_audit(model, GRB, stage1_started)
    if model.Status != GRB.OPTIMAL:
        classification = "KEEP_CORE_RECOURSE_FAIL"
        block_optima: dict[tuple[int, tuple[int, ...]], float] = {}
        robust_cost = None
    else:
        block_optima = {key: float(cost.getValue()) for key, cost in costs.items()}
        scenarios = enumerate_scenario_components(instance, gamma)
        robust_cost = exact_robust_recourse_cost(
            scenarios, block_optima, instance.num_products
        )
        classification = None

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    stage1.update(
        {
            "state_id": STATE_ID,
            "frozen_commit": FROZEN_SHA,
            "solver_profile": FORMAL_SOLVER_PROFILE_ID,
            "stage": "linear recourse cost optimization",
            "joint_objective_definition": "sum of all independent block recourse costs",
            "joint_objective_value": stage1["objective_value"],
            "exact_robust_recourse_cost": robust_cost,
            "block_count": len(block_optima),
            "block_optimum_costs": {
                f"product={key[0]};shocked_regions={','.join(map(str, key[1]))}": value
                for key, value in block_optima.items()
            },
            **coefficient_ranges,
        }
    )
    (output / "keep_stage1_lp_audit.json").write_text(
        json.dumps(stage1, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    if classification is not None:
        root = {
            "state_id": STATE_ID,
            "classification": classification,
            "cost_only_keep_evaluator": "COST_ONLY_KEEP_EVALUATOR_NOT_ESTABLISHED",
            "solver_dispatch_count": dispatches,
        }
        (output / "keep_failure_root_cause.json").write_text(
            json.dumps(root, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        raise SystemExit(classification)

    for key, cost in costs.items():
        model.addConstr(cost <= block_optima[key] + OPTIMALITY_TOLERANCE)
    quadratic = gp.quicksum(
        shortages[key][region] * shortages[key][region]
        for key in shortages
        for region in regions
    )
    model.setObjective(quadratic, GRB.MINIMIZE)
    model.update()
    stage2_coefficient_ranges = _model_coefficient_ranges(model)
    possible_demand = [
        instance.base_demand[region][product]
        + instance.demand_deviation[region][product]
        for region in regions
        for product in range(instance.num_products)
    ]
    cost_coefficients = [
        value
        for values in (
            [
                instance.transport_cost[depot][region][product]
                for depot in depots
                for region in regions
                for product in range(instance.num_products)
            ],
            [
                instance.shortage_penalty[region][product]
                for region in regions
                for product in range(instance.num_products)
            ],
            list(instance.service_penalty),
        )
        for value in values
        if value > 0
    ]
    max_demand = max(possible_demand)
    objective_scale = max(1.0, max_demand * max_demand)
    scale_audit = {
        "demand_definition": "base_demand + demand_deviation per region-product",
        "max_demand": max_demand,
        "median_demand": statistics.median(possible_demand),
        "max_shortage_variable_theoretical_scale": max_demand,
        "max_shortage_penalty": max(map(max, instance.shortage_penalty)),
        "max_service_penalty": max(instance.service_penalty),
        "estimated_max_single_shortage_squared": max_demand * max_demand,
        "largest_cost_coefficient": max(cost_coefficients),
        "smallest_nonzero_cost_coefficient": min(cost_coefficients),
        "largest_to_smallest_nonzero_cost_coefficient_ratio": (
            max(cost_coefficients) / min(cost_coefficients)
        ),
        "reporting_objective_scale": objective_scale,
    }

    with tempfile.TemporaryDirectory(prefix="keep-tiebreak-diagnostic-") as temp:
        original_log = Path(temp) / "original.log"
        dispatches += 1
        original_started = perf_counter()
        _, original_log_text = _optimize_with_log(model, original_log)
        original = _solve_audit(model, GRB, original_started)
        original["parameters"] = _solver_parameters(model)
        original["objective_scale_divisor"] = 1.0
        original["solver_log"] = original_log_text

        model.reset()
        model.setObjective(quadratic / objective_scale, GRB.MINIMIZE)
        model.update()
        rescaled_log = Path(temp) / "rescaled.log"
        dispatches += 1
        rescaled_started = perf_counter()
        _, rescaled_log_text = _optimize_with_log(model, rescaled_log)
        rescaled = _solve_audit(model, GRB, rescaled_started)
        rescaled["parameters"] = _solver_parameters(model)
        rescaled["objective_scale_divisor"] = objective_scale
        rescaled["unscaled_objective_value"] = (
            rescaled["objective_value"] * objective_scale
            if rescaled["objective_value"] is not None
            else None
        )
        rescaled["solver_log"] = rescaled_log_text

    if original["status"] == "SUBOPTIMAL" and rescaled["status"] == "OPTIMAL":
        classification = "REPORTING_QP_NUMERICAL_SCALING_CONFIRMED"
    elif original["status"] != "OPTIMAL" and rescaled["status"] != "OPTIMAL":
        classification = "REPORTING_QP_UNRESOLVED"
    else:
        classification = "REPORTING_QP_DIAGNOSTIC_OTHER"

    qp_audit = {
        "state_id": STATE_ID,
        "stage": "quadratic reporting tie-break",
        "cost_fixing_tolerance": OPTIMALITY_TOLERANCE,
        "cost_fixing_constraints_unchanged": True,
        "coefficient_ranges": {
            **stage2_coefficient_ranges,
            "mathematical_quadratic_objective_coefficient_original": 1.0,
            "mathematical_quadratic_objective_coefficient_rescaled": 1.0
            / objective_scale,
            "gurobi_log_diagonal_qobjective_coefficient_original": 2.0,
            "gurobi_log_diagonal_qobjective_coefficient_rescaled": 2.0
            / objective_scale,
        },
        "frozen_profile_original": original,
        "objective_rescaled_same_constraints": rescaled,
        "scale_diagnostic": scale_audit,
    }
    (output / "keep_reporting_qp_audit.json").write_text(
        json.dumps(qp_audit, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    root = {
        "state_id": STATE_ID,
        "failed_stage": "Stage 2 quadratic reporting tie-break",
        "stage1_status": stage1["status"],
        "stage1_exact_robust_recourse_cost": robust_cost,
        "original_stage2_status": original["status"],
        "rescaled_stage2_status": rescaled["status"],
        "classification": classification,
        "cost_only_keep_evaluator": "COST_ONLY_KEEP_EVALUATOR_FEASIBLE",
        "essential_for_decision_value": ["robust recourse cost"],
        "reporting_only_under_degeneracy": [
            "canonical shortage allocation",
            "minimum fill rate",
            "worst region identity",
            "transportation/shortage/service decomposition",
        ],
        "solver_dispatch_count": dispatches,
        "frozen_checkout_clean_after": not bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=checkout, text=True
            ).strip()
        ),
    }
    (output / "keep_failure_root_cause.json").write_text(
        json.dumps(root, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(root, indent=2), flush=True)


if __name__ == "__main__":
    main()
