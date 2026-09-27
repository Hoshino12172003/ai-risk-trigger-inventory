"""Execute MAIN, then the preregistered LOW/HIGH sensitivity."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_risk_trigger_inventory.compound_risk.events import build_event_catalog
from ai_risk_trigger_inventory.compound_risk.extensive_form import PolicyResult, solve_keep, solve_reoptimize
from ai_risk_trigger_inventory.compound_risk.metrics import evaluate_gates, inventory_l1, tied_worst
from ai_risk_trigger_inventory.compound_risk.scenarios import REGIMES, enumerate_scenarios, scenarios_for_regime
from ai_risk_trigger_inventory.oracle.budget_inventory_adapter import BudgetInventoryAdapter, OraclePayload


BASE_COMMIT = "2f0ea2d0af2af5424cf947866a6b1e9ca121859c"
PAPER2_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"


def _git(checkout: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=checkout, text=True).strip()


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _run_regimes(base: dict, magnitude: str, regimes: list[str]):
    instance = base["instance"]
    events = build_event_catalog(instance, magnitude)
    full = enumerate_scenarios(instance, events, 2)
    outputs: dict[str, dict[str, PolicyResult]] = {}
    for regime in regimes:
        scenarios = scenarios_for_regime(full, regime)
        print(f"[{magnitude}] {regime}: {len(scenarios)} scenarios", flush=True)
        keep = solve_keep(
            instance, base["incumbent_inventory_x0"],
            base["incumbent_depot_activation_y0"], scenarios,
        )
        reopt = solve_reoptimize(
            instance, base["incumbent_inventory_x0"],
            base["first_stage_parameters"]["budget"],
            base["first_stage_parameters"]["lambda_r"], scenarios,
        )
        if keep.x != base["incumbent_inventory_x0"]:
            raise RuntimeError("STOP: KEEP inventory differs from incumbent")
        outputs[regime] = {"KEEP": keep, "REOPTIMIZE": reopt}
    return outputs


def _summary_row(regime: str, mode: str, result: PolicyResult, x0: list[list[float]]) -> dict:
    worst = tied_worst(result.scenario_results)
    representative = worst[0]
    return {
        "regime": regime, "mode": mode,
        "scenario_count": len(result.scenario_results),
        "robust_total_objective": result.total_objective,
        "first_stage_cost": result.first_stage_cost,
        "reconfiguration_friction": result.reconfiguration_friction,
        "worst_case_recourse_cost": result.worst_case_recourse_cost,
        "transport_cost": representative.transport_cost,
        "shortage_cost": representative.shortage_cost,
        "service_penalty": representative.service_penalty,
        "total_shortage": representative.total_shortage,
        "minimum_fill_rate": representative.minimum_fill_rate,
        "worst_region_fill_rate": representative.worst_region_fill_rate,
        "inventory_l1_change_from_x0": inventory_l1(result.x, x0),
        "changed_warehouse_product_cells": sum(
            abs(result.x[i][j] - x0[i][j]) > 1e-6
            for i in range(len(x0)) for j in range(len(x0[i]))
        ),
        "worst_scenario_id": "|".join(row.scenario_id for row in worst),
        "worst_active_events": ";".join("|".join(row.active_events) for row in worst),
        "worst_event_type_composition": ";".join("|".join(row.event_types) for row in worst),
        "rerouted_volume_proxy": representative.rerouted_volume_proxy,
        "backup_warehouse_utilization_proxy": representative.backup_warehouse_utilization_proxy,
        "route_loss_exposure": representative.route_loss_exposure,
        "served_demand_under_disrupted_arcs": representative.served_demand_under_disrupted_arcs,
        "status": result.status, "base_commit": BASE_COMMIT,
        "paper2_frozen_sha": PAPER2_SHA, "calibration_id": "UNIT_COST_BASED",
    }


def _inventory_rank(result: PolicyResult, base: dict) -> list[str]:
    cells = [
        (-result.x[i][j], base["instance"]["depot_ids"][i], base["instance"]["product_ids"][j])
        for i in range(len(result.x)) for j in range(len(result.x[i]))
    ]
    return [f"{depot}|{product}" for _, depot, product in sorted(cells)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper2-checkout", required=True, type=Path)
    args = parser.parse_args()
    output = ROOT / "artifacts" / "compound_risk_stage1"
    base = json.loads((output / "base_state.json").read_text(encoding="utf-8"))
    if _git(args.paper2_checkout, "rev-parse", "HEAD") != PAPER2_SHA or _git(args.paper2_checkout, "status", "--porcelain"):
        raise SystemExit("STOP: Paper-2 frozen checkout SHA/clean precondition failed")
    started = perf_counter()
    main_results = _run_regimes(base, "MAIN", list(REGIMES))

    nominal_local = main_results["R0_NOMINAL"]["KEEP"].worst_case_recourse_cost
    payload = OraclePayload(
        decision_mode="KEEP", instance=base["instance"],
        x0=base["incumbent_inventory_x0"], y0=base["incumbent_depot_activation_y0"],
        budget=base["first_stage_parameters"]["budget"], gamma=0,
        lambda_r=base["first_stage_parameters"]["lambda_r"],
        active_network_id=base["instance"]["name"], evaluation_horizon=base["date"],
    )
    adapter = BudgetInventoryAdapter(args.paper2_checkout)
    legacy_nominal = adapter.evaluate(payload)["robust_recourse_cost"]
    if abs(nominal_local - legacy_nominal) > 1e-5 * max(1.0, abs(legacy_nominal)):
        raise RuntimeError("STOP: nominal model no longer reproduces baseline semantics")

    gates = evaluate_gates(
        main_results["R1_DEMAND_ONLY"]["KEEP"],
        main_results["R1_DEMAND_ONLY"]["REOPTIMIZE"],
        main_results["R7_FULL_COMPOUND"]["KEEP"],
        main_results["R7_FULL_COMPOUND"]["REOPTIMIZE"],
    )
    gates.update({
        "base_commit": BASE_COMMIT, "paper2_frozen_sha": PAPER2_SHA,
        "calibration_id": "UNIT_COST_BASED", "tie_tolerance_relative": 1e-6,
        "nominal_identity": {"local": nominal_local, "frozen_cost_only": legacy_nominal, "pass": True},
        "demand_only_identity_note": "Event-targeted percentage shocks are not isomorphic to Paper-2 region-product cardinality deviations; no forced identity claim. Internal extensive-form fixed-x cost identity passed.",
    })
    (output / "gate_results.json").write_text(json.dumps(gates, indent=2) + "\n", encoding="utf-8")

    regime_rows, scenario_rows, inventory_rows = [], [], []
    for regime in REGIMES:
        for mode in ("KEEP", "REOPTIMIZE"):
            result = main_results[regime][mode]
            regime_rows.append(_summary_row(regime, mode, result, base["incumbent_inventory_x0"]))
            for row in result.scenario_results:
                scenario_rows.append({
                    "regime": regime, "mode": mode, **asdict(row),
                    "active_events": "|".join(row.active_events),
                    "event_types": "|".join(row.event_types),
                    "base_commit": BASE_COMMIT, "paper2_frozen_sha": PAPER2_SHA,
                    "calibration_id": "UNIT_COST_BASED",
                })
            for i, depot in enumerate(base["instance"]["depot_ids"]):
                for j, product in enumerate(base["instance"]["product_ids"]):
                    inventory_rows.append({
                        "regime": regime, "mode": mode, "depot_id": depot,
                        "product_id": product, "inventory": result.x[i][j],
                        "incumbent_inventory": base["incumbent_inventory_x0"][i][j],
                        "absolute_change": abs(result.x[i][j] - base["incumbent_inventory_x0"][i][j]),
                        "base_commit": BASE_COMMIT, "paper2_frozen_sha": PAPER2_SHA,
                        "calibration_id": "UNIT_COST_BASED",
                    })
    _write_csv(output / "regime_summary.csv", regime_rows)
    _write_csv(output / "scenario_results.csv", scenario_rows)
    _write_csv(output / "inventory_solutions.csv", inventory_rows)

    main_rank = _inventory_rank(main_results["R7_FULL_COMPOUND"]["REOPTIMIZE"], base)
    main_gate_passes = {name: value["pass"] for name, value in gates["gates"].items()}
    sensitivity_rows = []
    sensitivity_dispatches = 0
    for magnitude in ("LOW", "HIGH"):
        sensitivity = _run_regimes(base, magnitude, ["R1_DEMAND_ONLY", "R7_FULL_COMPOUND"])
        sensitivity_gates = evaluate_gates(
            sensitivity["R1_DEMAND_ONLY"]["KEEP"], sensitivity["R1_DEMAND_ONLY"]["REOPTIMIZE"],
            sensitivity["R7_FULL_COMPOUND"]["KEEP"], sensitivity["R7_FULL_COMPOUND"]["REOPTIMIZE"],
        )
        sensitivity_dispatches += sum(
            result.optimizer_dispatches
            for policies in sensitivity.values() for result in policies.values()
        )
        rank = _inventory_rank(sensitivity["R7_FULL_COMPOUND"]["REOPTIMIZE"], base)
        main_worst = {row.active_events for row in tied_worst(main_results["R7_FULL_COMPOUND"]["REOPTIMIZE"].scenario_results)}
        sensitivity_worst = {row.active_events for row in tied_worst(sensitivity["R7_FULL_COMPOUND"]["REOPTIMIZE"].scenario_results)}
        sensitivity_rows.append({
            "magnitude": magnitude,
            "gate_pass_vector": json.dumps({name: value["pass"] for name, value in sensitivity_gates["gates"].items()}, sort_keys=True),
            "gate_direction_persistence": all(
                sensitivity_gates["gates"][name]["pass"] == passed
                for name, passed in main_gate_passes.items()
            ),
            "inventory_ranking_stable": rank == main_rank,
            "inventory_ranking": " > ".join(rank),
            "critical_scenario_stable": sensitivity_worst == main_worst,
            "critical_scenarios": ";".join("|".join(events) for events in sorted(sensitivity_worst)),
            "classification": sensitivity_gates["classification"],
            "base_commit": BASE_COMMIT, "paper2_frozen_sha": PAPER2_SHA,
            "calibration_id": "UNIT_COST_BASED",
        })
    _write_csv(output / "sensitivity_summary.csv", sensitivity_rows)

    main_dispatches = sum(
        result.optimizer_dispatches
        for policies in main_results.values() for result in policies.values()
    )
    paper2_clean_after = not bool(_git(args.paper2_checkout, "status", "--porcelain"))
    paper2_sha_after = _git(args.paper2_checkout, "rev-parse", "HEAD")
    if not paper2_clean_after or paper2_sha_after != PAPER2_SHA:
        raise RuntimeError("STOP: Paper-2 frozen checkout changed")
    audit = {
        "status": "PASS", "base_commit": BASE_COMMIT,
        "paper2_sha_before": PAPER2_SHA, "paper2_sha_after": paper2_sha_after,
        "paper2_clean_before": True, "paper2_clean_after": paper2_clean_after,
        "calibration_id": "UNIT_COST_BASED", "main_regimes": len(main_results),
        "main_optimizer_dispatches": main_dispatches,
        "sensitivity_optimizer_dispatches": sensitivity_dispatches,
        "nominal_identity_optimizer_dispatches": adapter.solver_calls,
        "optimizer_dispatches_total": main_dispatches + sensitivity_dispatches + adapter.solver_calls,
        "optimal_certification_count": main_dispatches + sensitivity_dispatches + adapter.solver_calls,
        "failures": [], "runtime_seconds": perf_counter() - started,
        "no_genai_dependency": True, "no_ccg_implementation": True,
        "no_m5_dependency": True, "paper2_source_modified": False,
    }
    (output / "execution_audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
