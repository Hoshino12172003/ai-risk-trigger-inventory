"""Run the authorized 20-state MAIN and calibration-sensitivity pilot."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import csv
import json
from pathlib import Path
import sys
from time import perf_counter
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_risk_trigger_inventory.oracle.budget_inventory_adapter import (
    BudgetInventoryAdapter,
    OraclePayload,
)
from ai_risk_trigger_inventory.oracle.provenance import OptimizationOracleProvenance
from execute_five_state_pilot import TOLERANCE, preflight_pair
from run_calibration_diagnostic import payload_for_scheme


SCHEMES = {
    "PRIMARY_PILOT_CALIBRATION": "UNIT_COST_BASED",
    "SENSITIVITY_CALIBRATION": "TRANSPORT_BASED",
}


def _payload_without_service_penalty(payload: OraclePayload) -> str:
    data = asdict(payload)
    del data["instance"]["service_penalty"]
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["state_id"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _result_row(
    state: dict[str, str],
    calibration_role: str,
    calibration_scheme: str,
    payload: OraclePayload,
    keep: dict[str, Any],
    reopt: dict[str, Any],
) -> dict[str, Any]:
    decision_value = float(keep["total_cost"] - reopt["total_cost"])
    inventory_change = sum(
        abs(float(reopt["x"][i][j]) - float(payload.x0[i][j]))
        for i in range(len(payload.x0))
        for j in range(len(payload.x0[i]))
    )
    changed_pairs = sum(
        abs(float(reopt["x"][i][j]) - float(payload.x0[i][j])) > 1e-6
        for i in range(len(payload.x0))
        for j in range(len(payload.x0[i]))
    )
    return {
        **state,
        "calibration_role": calibration_role,
        "calibration_scheme": calibration_scheme,
        "total_cost_keep": keep["total_cost"],
        "first_stage_keep": keep["first_stage_expenditure"],
        "robust_recourse_keep": keep["robust_recourse_cost"],
        "total_cost_reopt": reopt["total_cost"],
        "first_stage_reopt": reopt["first_stage_expenditure"],
        "robust_recourse_reopt": reopt["robust_recourse_cost"],
        "reconfiguration_cost": reopt["reconfiguration_cost"],
        "decision_value": decision_value,
        "relative_decision_value": decision_value
        / max(abs(float(keep["total_cost"])), TOLERANCE),
        "inventory_change_L1": inventory_change,
        "changed_inventory_pairs": changed_pairs,
        "keep_status": keep["status"],
        "oracle_status": reopt["status"],
        "exact_certification_pass": reopt["exact_certification_pass"],
        "global_risk_budget_coupling_pass": reopt[
            "global_risk_budget_coupling_pass"
        ],
        "recourse_identity_error": reopt["recourse_identity_error"],
        "runtime_keep": keep["runtime"],
        "runtime_reopt": reopt["runtime"],
        "oracle_repository": OptimizationOracleProvenance().repository,
        "oracle_commit": OptimizationOracleProvenance().commit_sha,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--states", required=True, type=Path)
    parser.add_argument("--payloads", required=True, type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts/decision_sensitivity_pilot",
    )
    args = parser.parse_args()

    with args.states.open(encoding="utf-8", newline="") as handle:
        states = list(csv.DictReader(handle))
    entries = json.loads(args.payloads.read_text(encoding="utf-8"))
    if len(states) != 20 or len(entries) != 20:
        raise SystemExit("BLOCKED: structural pilot requires exactly 20 states")
    state_by_id = {row["state_id"]: row for row in states}
    if set(state_by_id) != {entry["state_id"] for entry in entries}:
        raise SystemExit("BLOCKED: state and payload IDs differ")

    prepared: dict[tuple[str, str], OraclePayload] = {}
    equivalence: dict[str, bool] = {}
    for entry in entries:
        state_id = entry["state_id"]
        base = OraclePayload(**entry["payloads"][0])
        main = payload_for_scheme(base, SCHEMES["PRIMARY_PILOT_CALIBRATION"])
        sensitivity = payload_for_scheme(
            base, SCHEMES["SENSITIVITY_CALIBRATION"]
        )
        equivalent = (
            _payload_without_service_penalty(main)
            == _payload_without_service_penalty(sensitivity)
        )
        if not equivalent:
            raise SystemExit(f"BLOCKED: calibration payload mismatch for {state_id}")
        equivalence[state_id] = True
        prepared[(state_id, "PRIMARY_PILOT_CALIBRATION")] = main
        prepared[(state_id, "SENSITIVITY_CALIBRATION")] = sensitivity

    adapter = BudgetInventoryAdapter(args.checkout)
    for role in SCHEMES:
        for entry in entries:
            keep_payload = prepared[(entry["state_id"], role)]
            preflight_pair(
                keep_payload,
                replace(keep_payload, decision_mode="REOPTIMIZE"),
            )
    outputs: dict[str, list[dict[str, Any]]] = {
        "PRIMARY_PILOT_CALIBRATION": [],
        "SENSITIVITY_CALIBRATION": [],
    }
    started = perf_counter()
    failure: str | None = None
    for role, scheme in SCHEMES.items():
        for index, entry in enumerate(entries, start=1):
            state_id = entry["state_id"]
            keep_payload = prepared[(state_id, role)]
            reopt_payload = replace(keep_payload, decision_mode="REOPTIMIZE")
            print(f"[{role} {index}/20] {state_id}", flush=True)
            try:
                keep = adapter.evaluate(keep_payload)
                reopt = adapter.evaluate(reopt_payload)
                if not (
                    keep["status"] == "OPTIMAL"
                    and reopt["status"] == "OPTIMAL"
                    and reopt["exact_certification_pass"] is True
                    and reopt["global_risk_budget_coupling_pass"] is True
                ):
                    raise RuntimeError("UNCERTIFIED_ORACLE_RESULT")
                if abs(float(reopt["recourse_identity_error"])) > TOLERANCE:
                    raise RuntimeError("REOPTIMIZE_RECOURSE_IDENTITY_FAIL")
                if abs(
                    float(keep["total_cost"])
                    - float(keep["first_stage_expenditure"])
                    - float(keep["robust_recourse_cost"])
                ) > TOLERANCE:
                    raise RuntimeError("KEEP_COST_IDENTITY_FAIL")
                if abs(
                    float(reopt["total_cost"])
                    - float(reopt["first_stage_expenditure"])
                    - float(reopt["robust_recourse_cost"])
                ) > TOLERANCE:
                    raise RuntimeError("REOPTIMIZE_COST_IDENTITY_FAIL")
                if float(keep["total_cost"] - reopt["total_cost"]) < -TOLERANCE:
                    raise RuntimeError("COST_DOMINANCE_VIOLATION")
                outputs[role].append(
                    _result_row(
                        state_by_id[state_id],
                        role,
                        scheme,
                        keep_payload,
                        keep,
                        reopt,
                    )
                )
            except Exception as error:
                failure = f"{role}/{state_id}: {error}"
                break
        if failure:
            break

    _write_csv(
        args.output_dir / "twenty_state_results_main.csv",
        outputs["PRIMARY_PILOT_CALIBRATION"],
    )
    _write_csv(
        args.output_dir / "twenty_state_results_sensitivity.csv",
        outputs["SENSITIVITY_CALIBRATION"],
    )
    execution = {
        "status": "PASS" if failure is None else "CHAIN_FAIL",
        "failure": failure,
        "constructed_states": 20,
        "main_executed_states": len(outputs["PRIMARY_PILOT_CALIBRATION"]),
        "sensitivity_executed_states": len(
            outputs["SENSITIVITY_CALIBRATION"]
        ),
        "certified_reoptimizations": sum(
            row["exact_certification_pass"] is True
            for rows in outputs.values()
            for row in rows
        ),
        "solver_dispatch_count": adapter.solver_calls,
        "runtime_seconds": perf_counter() - started,
        "oracle_commit": OptimizationOracleProvenance().commit_sha,
        "payload_equivalence_after_removing_service_penalty": equivalence,
        "frozen_checkout_clean_after": not bool(
            adapter._git("status", "--porcelain")
        ),
    }
    (args.output_dir / "twenty_state_execution_audit.json").write_text(
        json.dumps(execution, indent=2) + "\n", encoding="utf-8"
    )
    if failure:
        raise SystemExit(f"CHAIN_FAIL: {failure}")
    print(json.dumps(execution, indent=2), flush=True)


if __name__ == "__main__":
    main()
