"""Thin adapter for a verified checkout of the frozen Paper 2 oracle."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import importlib
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from typing import Any, Literal

from .cost_only_recourse import evaluate_cost_only_robust_recourse
from .provenance import OptimizationOracleProvenance


DecisionMode = Literal["KEEP", "REOPTIMIZE"]


@dataclass(frozen=True)
class OraclePayload:
    decision_mode: DecisionMode
    instance: dict[str, Any]
    x0: list[list[float]]
    y0: list[int]
    budget: float
    gamma: int
    lambda_r: float
    active_network_id: str
    evaluation_horizon: str


def make_comparable_payloads(payload: OraclePayload) -> tuple[OraclePayload, OraclePayload]:
    """Return payloads whose only difference is the decision mode."""

    return replace(payload, decision_mode="KEEP"), replace(
        payload, decision_mode="REOPTIMIZE"
    )


def assert_comparable_payloads(keep: OraclePayload, reoptimize: OraclePayload) -> None:
    keep_values = asdict(keep)
    reoptimize_values = asdict(reoptimize)
    keep_mode = keep_values.pop("decision_mode")
    reoptimize_mode = reoptimize_values.pop("decision_mode")
    if keep_mode != "KEEP" or reoptimize_mode != "REOPTIMIZE":
        raise ValueError("payload decision modes are invalid")
    if keep_values != reoptimize_values:
        raise ValueError("KEEP and REOPTIMIZE payloads differ beyond decision_mode")


def total_cost_once(
    fixed_cost: float,
    inventory_cost: float,
    reconfiguration_cost: float,
    robust_recourse_cost: float,
) -> float:
    """Compose total cost with the frozen reconfiguration friction exactly once."""

    return fixed_cost + inventory_cost + reconfiguration_cost + robust_recourse_cost


class BudgetInventoryAdapter:
    """Load and call the frozen package without copying or modifying its source."""

    def __init__(self, checkout: Path) -> None:
        self.checkout = checkout.resolve()
        self.provenance = OptimizationOracleProvenance()
        self.solver_calls = 0
        self._assert_clean_frozen_checkout()

    def _git(self, *arguments: str) -> str:
        return subprocess.check_output(
            ["git", *arguments], cwd=self.checkout, text=True
        ).strip()

    def _assert_clean_frozen_checkout(self) -> None:
        if self._git("rev-parse", "HEAD") != self.provenance.commit_sha:
            raise RuntimeError("frozen oracle checkout commit mismatch")
        if self._git("status", "--porcelain"):
            raise RuntimeError("frozen oracle checkout must remain clean and read-only")

    def _modules(self):
        source = str(self.checkout / "src")
        if source not in sys.path:
            sys.path.insert(0, source)
        instance_module = importlib.import_module(
            "robust_inventory_reconfiguration.instance"
        )
        benders_module = importlib.import_module(
            "robust_inventory_reconfiguration.product_risk_budget_benders"
        )
        cost_module = importlib.import_module(
            "robust_inventory_reconfiguration.reconfiguration_model"
        )
        return instance_module, benders_module, cost_module

    def evaluate(self, payload: OraclePayload) -> dict[str, Any]:
        """Execute one requested policy; callers must obtain separate authorization."""

        if payload.decision_mode not in ("KEEP", "REOPTIMIZE"):
            raise ValueError("unsupported oracle decision mode")
        self._assert_clean_frozen_checkout()
        instance_module, benders_module, cost_module = self._modules()
        instance = instance_module.InventoryInstance.from_dict(payload.instance)
        if payload.decision_mode == "KEEP":
            zeros = [
                [0.0] * instance.num_products for _ in range(instance.num_depots)
            ]
            self.solver_calls += 1
            service = evaluate_cost_only_robust_recourse(
                instance, payload.x0, payload.gamma
            )
            first_stage = cost_module.first_stage_expenditure_value(
                instance,
                payload.y0,
                payload.x0,
                zeros,
                zeros,
                payload.lambda_r,
            )
            fixed_cost = sum(
                instance.fixed_depot_cost[i] * payload.y0[i]
                for i in range(instance.num_depots)
            )
            inventory_cost = sum(
                instance.inventory_cost[i][j] * payload.x0[i][j]
                for i in range(instance.num_depots)
                for j in range(instance.num_products)
            )
            total = total_cost_once(
                fixed_cost,
                inventory_cost,
                0.0,
                service.robust_recourse_cost,
            )
            if abs(total - (first_stage + service.robust_recourse_cost)) > 1e-7:
                raise RuntimeError("KEEP cost decomposition mismatch")
            result = {
                "decision_mode": "KEEP",
                "status": "OPTIMAL",
                "total_cost": total,
                "first_stage_expenditure": first_stage,
                "robust_recourse_cost": service.robust_recourse_cost,
                "reconfiguration_cost": 0.0,
                "x": payload.x0,
                "y": payload.y0,
                "reporting_service_metrics_status": "UNAVAILABLE_REPORTING_TIEBREAK_UNRESOLVED",
                "runtime": service.runtime,
                "joint_block_objective": service.joint_block_objective,
                "worst_scenario": service.worst_scenario,
                "oracle_dispatches": 1,
            }
        else:
            self.solver_calls += 1
            started = perf_counter()
            solved = benders_module.solve_prb_benders(
                instance,
                payload.x0,
                payload.budget,
                payload.gamma,
                payload.lambda_r,
            )
            core_runtime = perf_counter() - started
            if (
                solved.status != "OPTIMAL"
                or not solved.exact_certification_pass
                or not solved.global_risk_budget_coupling_pass
            ):
                raise RuntimeError("frozen PRB-Benders result is not exactly certified")
            solution = solved.solution
            self.solver_calls += 1
            audit_started = perf_counter()
            service = evaluate_cost_only_robust_recourse(
                instance, solution.x, payload.gamma
            )
            audit_runtime = perf_counter() - audit_started
            recourse_error = service.robust_recourse_cost - solution.robust_recourse_cost
            if abs(recourse_error) > 1e-4:
                raise RuntimeError("REOPTIMIZE_RECOURSE_IDENTITY_FAIL")
            result = {
                "decision_mode": "REOPTIMIZE",
                "status": solved.status,
                "total_cost": solution.objective,
                "first_stage_expenditure": solution.first_stage_expenditure,
                "robust_recourse_cost": solution.robust_recourse_cost,
                "reconfiguration_cost": solution.reconfiguration_cost,
                "x": solution.x,
                "y": solution.y,
                "a_plus": solution.a_plus,
                "a_minus": solution.a_minus,
                "reporting_service_metrics_status": "UNAVAILABLE_REPORTING_TIEBREAK_UNRESOLVED",
                "runtime": core_runtime + audit_runtime,
                "core_runtime": core_runtime,
                "cost_only_audit_runtime": audit_runtime,
                "oracle_reported_runtime": solved.total_runtime,
                "exact_certification_pass": solved.exact_certification_pass,
                "global_risk_budget_coupling_pass": solved.global_risk_budget_coupling_pass,
                "global_risk_budget_coupling_error": solved.global_risk_budget_coupling_error,
                "master_solve_count": solved.master_solve_count,
                "recourse_identity_error": recourse_error,
                "cost_only_robust_recourse_audit": service.robust_recourse_cost,
                "oracle_dispatches": 2,
            }
        self._assert_clean_frozen_checkout()
        return result
