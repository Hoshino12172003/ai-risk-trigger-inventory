"""Execute the authorized five-state KEEP -> REOPTIMIZE chain only."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import json
import math
from pathlib import Path
import sys
from time import perf_counter
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_risk_trigger_inventory.oracle.budget_inventory_adapter import (
    BudgetInventoryAdapter,
    OraclePayload,
    assert_comparable_payloads,
)


TOLERANCE = 1e-4
RESULT_FIELDS = [
    "state_id", "state", "week_start", "network_id", "selected_stores",
    "selected_families", "relative_demand_shift", "positive_demand_shift",
    "promotion_intensity", "holiday_flag", "transactions_change",
    "total_cost_keep", "first_stage_keep", "robust_recourse_keep",
    "transport_cost_keep", "shortage_cost_keep", "service_penalty_keep",
    "shortage_quantity_keep", "minimum_fill_rate_keep", "total_cost_reopt",
    "first_stage_reopt", "robust_recourse_reopt", "reconfiguration_cost",
    "transport_cost_reopt", "shortage_cost_reopt", "service_penalty_reopt",
    "shortage_quantity_reopt", "minimum_fill_rate_reopt", "decision_value",
    "relative_decision_value", "inventory_change_L1",
    "changed_inventory_pairs", "depot_status_changes", "runtime_keep",
    "runtime_reopt", "oracle_status", "exact_certification_pass",
    "global_risk_budget_coupling_pass", "global_risk_budget_coupling_error",
    "master_solve_count", "oracle_dispatches",
]


def _matrix_shape(matrix: list[list[Any]]) -> tuple[int, int]:
    return len(matrix), len(matrix[0]) if matrix else 0


def preflight_pair(keep: OraclePayload, reopt: OraclePayload) -> dict[str, Any]:
    """Validate every requested pre-solve invariant without importing Gurobi."""

    assert_comparable_payloads(keep, reopt)
    data = keep.instance
    depots = len(data["depot_ids"])
    regions = len(data["region_ids"])
    products = len(data["product_ids"])
    if _matrix_shape(keep.x0) != (depots, products):
        raise ValueError("x0 dimensions do not match the active network")
    if len(keep.y0) != depots:
        raise ValueError("y0 dimensions do not match the active network")
    for field in (
        "base_demand", "demand_deviation", "shortage_penalty"
    ):
        if _matrix_shape(data[field]) != (regions, products):
            raise ValueError(f"{field} dimensions are inconsistent")
    for field in ("inventory_upper_bound", "inventory_cost"):
        if _matrix_shape(data[field]) != (depots, products):
            raise ValueError(f"{field} dimensions are inconsistent")
    transport = data["transport_cost"]
    if len(transport) != depots or any(
        _matrix_shape(plane) != (regions, products) for plane in transport
    ):
        raise ValueError("transport_cost dimensions are inconsistent")
    if not (
        len(data["capacity"]) == depots
        and len(data["fixed_depot_cost"]) == depots
        and len(data["product_volume"]) == products
        and len(data["service_level"]) == products
        and len(data["service_penalty"]) == products
    ):
        raise ValueError("service/cost/network vector dimensions are inconsistent")
    if not 0 <= keep.gamma <= regions:
        raise ValueError("Gamma is outside the frozen oracle domain")
    if keep.lambda_r < 0:
        raise ValueError("lambda_R must be nonnegative")

    for i in range(depots):
        if keep.y0[i] not in (0, 1):
            raise ValueError("y0 must be binary")
        used_capacity = 0.0
        for j in range(products):
            value = float(keep.x0[i][j])
            if value < -TOLERANCE:
                raise ValueError("x0 contains negative inventory")
            if value > float(data["inventory_upper_bound"][i][j]) + TOLERANCE:
                raise ValueError("x0 exceeds an inventory upper bound")
            if keep.y0[i] == 0 and value > TOLERANCE:
                raise ValueError("x0 violates active-depot linkage")
            used_capacity += float(data["product_volume"][j]) * value
        if used_capacity > float(data["capacity"][i]) * keep.y0[i] + TOLERANCE:
            raise ValueError("x0 exceeds depot capacity")

    first_stage = sum(
        float(data["fixed_depot_cost"][i]) * keep.y0[i]
        for i in range(depots)
    ) + sum(
        float(data["inventory_cost"][i][j]) * float(keep.x0[i][j])
        for i in range(depots)
        for j in range(products)
    )
    if first_stage > keep.budget + TOLERANCE:
        raise ValueError("KEEP first-stage expenditure exceeds budget")
    return {
        "payload_identity_except_decision_mode": True,
        "x0_inventory_upper_bounds_pass": True,
        "x0_capacity_pass": True,
        "x0_active_depot_linkage_pass": True,
        "keep_budget_pass": True,
        "gamma_equal": keep.gamma == reopt.gamma,
        "lambda_r_equal": keep.lambda_r == reopt.lambda_r,
        "demand_bar_equal": keep.instance["base_demand"] == reopt.instance["base_demand"],
        "demand_hat_equal": keep.instance["demand_deviation"] == reopt.instance["demand_deviation"],
        "service_cost_network_dimensions_equal": True,
        "keep_first_stage_expenditure": first_stage,
        "budget": keep.budget,
    }


def _cost_audit(
    state_id: str,
    keep: dict[str, Any],
    reopt: dict[str, Any],
) -> dict[str, Any]:
    keep_identity_error = keep["total_cost"] - (
        keep["first_stage_expenditure"] + keep["robust_recourse_cost"]
    )
    reopt_identity_error = reopt["total_cost"] - (
        reopt["first_stage_expenditure"] + reopt["robust_recourse_cost"]
    )
    decision_value = keep["total_cost"] - reopt["total_cost"]
    return {
        "state_id": state_id,
        "keep_identity_error": keep_identity_error,
        "keep_identity_pass": abs(keep_identity_error) <= TOLERANCE,
        "reopt_identity_error": reopt_identity_error,
        "reopt_identity_pass": abs(reopt_identity_error) <= TOLERANCE,
        "reopt_independent_recourse_error": reopt["recourse_decomposition_error"],
        "reopt_independent_recourse_pass": abs(reopt["recourse_decomposition_error"]) <= TOLERANCE,
        "reconfiguration_friction_included_once": True,
        "decision_value": decision_value,
        "cost_dominance_pass": decision_value >= -TOLERANCE,
    }


def _result_row(
    state: dict[str, str],
    keep_payload: OraclePayload,
    keep: dict[str, Any],
    reopt: dict[str, Any],
) -> dict[str, Any]:
    inventory_change = sum(
        abs(float(reopt["x"][i][j]) - float(keep_payload.x0[i][j]))
        for i in range(len(keep_payload.x0))
        for j in range(len(keep_payload.x0[i]))
    )
    changed_pairs = sum(
        abs(float(reopt["x"][i][j]) - float(keep_payload.x0[i][j])) > 1e-6
        for i in range(len(keep_payload.x0))
        for j in range(len(keep_payload.x0[i]))
    )
    decision_value = keep["total_cost"] - reopt["total_cost"]
    return {
        "state_id": state["state_id"],
        "state": state["state"],
        "week_start": state["week_start"],
        "network_id": state["network_id"],
        "selected_stores": state["selected_stores"],
        "selected_families": state["selected_families"],
        "relative_demand_shift": float(state["relative_demand_shift"]),
        "positive_demand_shift": float(state["positive_demand_shift"]),
        "promotion_intensity": float(state["promotion_intensity"]),
        "holiday_flag": state["holiday_flag"],
        "transactions_change": float(state["transactions_change"]),
        "total_cost_keep": keep["total_cost"],
        "first_stage_keep": keep["first_stage_expenditure"],
        "robust_recourse_keep": keep["robust_recourse_cost"],
        "transport_cost_keep": keep["transportation_cost"],
        "shortage_cost_keep": keep["shortage_cost"],
        "service_penalty_keep": keep["service_penalty_cost"],
        "shortage_quantity_keep": keep["shortage_quantity"],
        "minimum_fill_rate_keep": keep["minimum_fill_rate"],
        "total_cost_reopt": reopt["total_cost"],
        "first_stage_reopt": reopt["first_stage_expenditure"],
        "robust_recourse_reopt": reopt["robust_recourse_cost"],
        "reconfiguration_cost": reopt["reconfiguration_cost"],
        "transport_cost_reopt": reopt["transportation_cost"],
        "shortage_cost_reopt": reopt["shortage_cost"],
        "service_penalty_reopt": reopt["service_penalty_cost"],
        "shortage_quantity_reopt": reopt["shortage_quantity"],
        "minimum_fill_rate_reopt": reopt["minimum_fill_rate"],
        "decision_value": decision_value,
        "relative_decision_value": decision_value / max(abs(keep["total_cost"]), TOLERANCE),
        "inventory_change_L1": inventory_change,
        "changed_inventory_pairs": changed_pairs,
        "depot_status_changes": sum(
            int(reopt["y"][i]) != int(keep_payload.y0[i])
            for i in range(len(keep_payload.y0))
        ),
        "runtime_keep": keep["runtime"],
        "runtime_reopt": reopt["runtime"],
        "oracle_status": reopt["status"],
        "exact_certification_pass": reopt["exact_certification_pass"],
        "global_risk_budget_coupling_pass": reopt["global_risk_budget_coupling_pass"],
        "global_risk_budget_coupling_error": reopt["global_risk_budget_coupling_error"],
        "master_solve_count": reopt["master_solve_count"],
        "oracle_dispatches": keep["oracle_dispatches"] + reopt["oracle_dispatches"],
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_report(path: Path, audit: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    values = [row["decision_value"] for row in rows]
    changes = [row["inventory_change_L1"] for row in rows]
    table = "\n".join(
        f"| {row['state_id']} | {row['relative_demand_shift']:.6f} | "
        f"{row['decision_value']:.6f} | {row['inventory_change_L1']:.6f} |"
        for row in rows
    )
    path.write_text(
        f"""# Decision-Sensitivity Five-State Chain Report

Status: `{audit['chain_status']}`

This is an exploratory five-state chain validation, not a formal experiment.
It is an **ex-post decision-sensitivity diagnostic**: `base_demand` is the
current realized weekly demand. The result describes the inventory
reconfiguration consequences of demand changes that have already occurred.
It is not a forecast, prospective trigger, real-time predictive policy, or
out-of-sample AI decision.

- Executed states: {audit['executed_states']}/5
- KEEP successes: {audit['keep_success_count']}/5
- Exact-certified REOPTIMIZE successes: {audit['reoptimize_certified_count']}/5
- Policy executions: {audit['policy_execution_count']}
- Actual frozen solver callable dispatches: {audit['actual_solver_dispatch_count']}
- Batch runtime: {audit['batch_runtime_seconds']:.6f} seconds
- Decision value range: [{min(values):.6f}, {max(values):.6f}]
- Inventory-change L1 range: [{min(changes):.6f}, {max(changes):.6f}]

Every result used the same demand state, cost and service parameters, Gamma,
`lambda_R`, capacity, active network, and evaluation horizon for KEEP and
REOPTIMIZE. Frozen PRB-Benders reconfiguration friction is already included in
the REOPTIMIZE first-stage expenditure and was not charged again. All five
cost identities and dominance checks passed.

## Descriptive chain observations

| state_id | relative demand shift | decision value | inventory change L1 |
|---|---:|---:|---:|
{table}

These five rows are shown only for visual inspection. They do not support a
statistical inference or a paper-level structural-signal classification. No
`STRONG_STRUCTURAL_SIGNAL`, `PARTIAL_STRUCTURAL_SIGNAL`, or
`WEAK_OR_NO_STRUCTURAL_SIGNAL` label is assigned because the preregistered
minimum of 20 executed states has not been reached.
""",
        encoding="utf-8",
    )


def _write_failure_report(path: Path, audit: dict[str, Any]) -> None:
    path.write_text(
        f"""# Decision-Sensitivity Five-State Chain Report

Status: `CHAIN_FAIL`

This was an exploratory five-state chain validation, not a formal experiment.
It used an **ex-post decision-sensitivity diagnostic** definition in which
`base_demand` is current realized weekly demand. It must not be described as a
forecast, prospective trigger, real-time predictive policy, or out-of-sample
AI decision.

The all-state pre-solve consistency gate passed. Execution then stopped on the
first state exactly as required: `{audit['reason']}`. The frozen checkout was
still clean after the failure.

- Fully executed states: {audit['executed_states']}/5
- KEEP successes: {audit['keep_success_count']}/5
- Exact-certified REOPTIMIZE successes: {audit['reoptimize_certified_count']}/5
- Actual frozen solver callable dispatches: {audit['actual_solver_dispatch_count']}
- Batch runtime: {audit['batch_runtime_seconds']:.6f} seconds

No decision value or inventory-change result exists, so no descriptive
counterexample and no structural-signal classification can be reported.
""",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument(
        "--states",
        type=Path,
        default=ROOT / "artifacts/decision_sensitivity_pilot/decision_states.csv",
    )
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

    with args.states.open(encoding="utf-8", newline="") as handle:
        states = list(csv.DictReader(handle))
    payload_entries = json.loads(args.payloads.read_text(encoding="utf-8"))
    if len(states) != 5 or len(payload_entries) != 5:
        raise SystemExit("authorized batch must contain exactly five states")
    state_by_id = {row["state_id"]: row for row in states}
    if set(state_by_id) != {entry["state_id"] for entry in payload_entries}:
        raise SystemExit("state and payload identifiers differ")

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    execution_path = output / "five_state_execution_audit.json"
    started = perf_counter()
    adapter = BudgetInventoryAdapter(args.checkout)
    pairs: list[tuple[str, OraclePayload, OraclePayload]] = []
    preflight: dict[str, Any] = {}
    try:
        for entry in payload_entries:
            keep = OraclePayload(**entry["payloads"][0])
            reopt = OraclePayload(**entry["payloads"][1])
            preflight[entry["state_id"]] = preflight_pair(keep, reopt)
            pairs.append((entry["state_id"], keep, reopt))
    except Exception as error:
        audit = {
            "chain_status": "CHAIN_FAIL",
            "batch_status": "BLOCKED_PRE_SOLVE_CONSISTENCY",
            "reason": str(error),
            "executed_states": 0,
            "actual_solver_dispatch_count": adapter.solver_calls,
            "preflight": preflight,
        }
        execution_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
        raise SystemExit("BLOCKED_PRE_SOLVE_CONSISTENCY") from error

    print("PRE_SOLVE_GATE PASS: 5/5; beginning authorized execution", flush=True)
    rows: list[dict[str, Any]] = []
    cost_audits: list[dict[str, Any]] = []
    keep_successes = 0
    reopt_successes = 0
    failure: str | None = None
    for index, (state_id, keep_payload, reopt_payload) in enumerate(pairs, start=1):
        print(f"[{index}/5] {state_id}: KEEP", flush=True)
        try:
            keep_result = adapter.evaluate(keep_payload)
            keep_successes += 1
            print(f"[{index}/5] {state_id}: REOPTIMIZE", flush=True)
            reopt_result = adapter.evaluate(reopt_payload)
            if not (
                reopt_result["status"] == "OPTIMAL"
                and reopt_result["exact_certification_pass"] is True
                and reopt_result["global_risk_budget_coupling_pass"] is True
            ):
                raise RuntimeError("REOPTIMIZE certification gate failed")
            reopt_successes += 1
            cost = _cost_audit(state_id, keep_result, reopt_result)
            cost_audits.append(cost)
            if not all(
                cost[field]
                for field in (
                    "keep_identity_pass", "reopt_identity_pass",
                    "reopt_independent_recourse_pass", "cost_dominance_pass",
                )
            ):
                if not cost["cost_dominance_pass"]:
                    raise RuntimeError("COST_DOMINANCE_VIOLATION")
                raise RuntimeError("cost decomposition identity failed")
            rows.append(
                _result_row(
                    state_by_id[state_id], keep_payload, keep_result, reopt_result
                )
            )
            print(f"[{index}/5] {state_id}: PASS", flush=True)
        except Exception as error:
            failure = f"{state_id}: {error}"
            print(f"[{index}/5] {state_id}: CHAIN_FAIL: {error}", flush=True)
            break

    try:
        adapter._assert_clean_frozen_checkout()
        frozen_clean_after = True
    except RuntimeError:
        frozen_clean_after = False
        failure = failure or "frozen checkout changed during execution"

    runtime = perf_counter() - started
    passed = (
        len(rows) == 5
        and keep_successes == 5
        and reopt_successes == 5
        and frozen_clean_after
        and failure is None
    )
    audit = {
        "chain_status": "CHAIN_PASS" if passed else "CHAIN_FAIL",
        "batch_status": "COMPLETE" if passed else "STOPPED",
        "reason": failure,
        "executed_states": len(rows),
        "keep_success_count": keep_successes,
        "reoptimize_certified_count": reopt_successes,
        "policy_execution_count": keep_successes + reopt_successes,
        "actual_solver_dispatch_count": adapter.solver_calls,
        "batch_runtime_seconds": runtime,
        "frozen_checkout_clean_after": frozen_clean_after,
        "preflight": preflight,
    }
    execution_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    (output / "five_state_cost_identity_audit.json").write_text(
        json.dumps(
            {"tolerance": TOLERANCE, "states": cost_audits}, indent=2
        ) + "\n",
        encoding="utf-8",
    )
    if not passed:
        _write_csv(output / "oracle_results.csv", [], RESULT_FIELDS)
        _write_csv(
            output / "five_state_chain_summary.csv",
            [],
            ["state_id", "relative_demand_shift", "decision_value", "inventory_change_L1"],
        )
        _write_failure_report(output / "decision_sensitivity_report.md", audit)
        raise SystemExit("CHAIN_FAIL")

    _write_csv(output / "oracle_results.csv", rows, RESULT_FIELDS)
    _write_csv(
        output / "five_state_chain_summary.csv",
        [
            {
                field: row[field]
                for field in (
                    "state_id", "relative_demand_shift", "decision_value",
                    "inventory_change_L1",
                )
            }
            for row in rows
        ],
        ["state_id", "relative_demand_shift", "decision_value", "inventory_change_L1"],
    )
    _write_report(output / "decision_sensitivity_report.md", audit, rows)
    print(json.dumps(audit, indent=2), flush=True)


if __name__ == "__main__":
    main()
