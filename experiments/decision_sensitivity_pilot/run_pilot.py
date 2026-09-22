"""Validate a five-state dry-run or stop at the unverified oracle boundary."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_risk_trigger_inventory.data.decision_sensitivity import (
    FORBIDDEN_INPUT_FIELDS,
    MIN_BASELINE_DEMAND,
    TARGET_STATES,
    assert_no_post_solve_leakage,
)
from ai_risk_trigger_inventory.oracle.decision_sensitivity import (
    BlockedOracleAdapter,
    OracleIntegrationBlocked,
)
from ai_risk_trigger_inventory.oracle.budget_inventory_adapter import (
    OraclePayload,
    assert_comparable_payloads,
)
from ai_risk_trigger_inventory.oracle.provenance import OptimizationOracleProvenance


REQUIRED_STATE_FIELDS = {
    "state_id",
    "state",
    "selected_stores",
    "selected_families",
    "baseline_demand",
    "current_demand",
    "absolute_demand_shift",
    "relative_demand_shift",
    "positive_demand_shift",
    "negative_demand_shift",
    "inventory_to_demand_ratio",
    "capacity_slack_ratio",
    "demand_concentration",
    "incumbent_inventory_concentration",
    "transport_cost_cv",
    "transport_substitutability_proxy",
    "incumbent_inventory_source",
    "oracle_repository",
    "oracle_commit",
}


def validate_dry_run(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        field_names = reader.fieldnames or []
    return validate_dry_run_rows(rows, field_names)


def validate_dry_run_rows(
    rows: list[dict[str, str]], field_names: list[str]
) -> dict[str, object]:
    """Validate already parsed rows without filesystem or solver side effects."""

    assert_no_post_solve_leakage(field_names)
    if len(rows) != 5:
        raise ValueError(f"dry-run requires exactly 5 states, found {len(rows)}")
    missing = REQUIRED_STATE_FIELDS - set(field_names)
    if missing:
        raise ValueError(f"decision states missing required fields: {sorted(missing)}")
    if len({row["state_id"] for row in rows}) != len(rows):
        raise ValueError("decision-state identifiers must be unique")
    provenance = OptimizationOracleProvenance()
    numeric_fields = {
        "baseline_demand",
        "current_demand",
        "absolute_demand_shift",
        "relative_demand_shift",
        "positive_demand_shift",
        "negative_demand_shift",
        "inventory_to_demand_ratio",
        "capacity_slack_ratio",
        "demand_concentration",
        "incumbent_inventory_concentration",
        "transport_cost_cv",
        "transport_substitutability_proxy",
    }
    for row in rows:
        numeric = {field: float(row[field]) for field in numeric_fields}
        if not all(math.isfinite(value) for value in numeric.values()):
            raise ValueError("all dry-run numeric features must be finite")
        baseline = numeric["baseline_demand"]
        current = numeric["current_demand"]
        if baseline < MIN_BASELINE_DEMAND:
            raise ValueError("baseline demand violates the fixed denominator rule")
        expected_absolute = current - baseline
        expected_relative = expected_absolute / baseline
        if not math.isclose(numeric["absolute_demand_shift"], expected_absolute, rel_tol=1e-9):
            raise ValueError("absolute demand shift is inconsistent")
        if not math.isclose(numeric["relative_demand_shift"], expected_relative, rel_tol=1e-9):
            raise ValueError("relative demand shift is inconsistent")
        if not math.isclose(numeric["positive_demand_shift"], max(expected_relative, 0.0), rel_tol=1e-9):
            raise ValueError("positive demand shift is inconsistent")
        if not math.isclose(numeric["negative_demand_shift"], min(expected_relative, 0.0), rel_tol=1e-9, abs_tol=1e-12):
            raise ValueError("negative demand shift is inconsistent")
        if row["state"] not in TARGET_STATES:
            raise ValueError("dry-run contains a non-pilot state")
        if not 4 <= len(row["selected_stores"].split("|")) <= 6:
            raise ValueError("each context must contain 4--6 selected stores")
        if not 2 <= len(row["selected_families"].split("|")) <= 3:
            raise ValueError("each context must contain 2--3 selected families")
        if row["incumbent_inventory_source"] != "CALIBRATED":
            raise ValueError("every incumbent inventory must be labeled CALIBRATED")
        if row["oracle_repository"] != provenance.repository or row["oracle_commit"] != provenance.commit_sha:
            raise ValueError("frozen oracle provenance mismatch")
    return {
        "status": "PASS",
        "constructed_states": 5,
        "executed_states": 0,
        "solver_calls": 0,
        "leakage_fields": sorted(FORBIDDEN_INPUT_FIELDS & set(field_names)),
    }


def validate_payload_file(path: Path, expected_state_ids: set[str]) -> dict[str, object]:
    entries = json.loads(path.read_text(encoding="utf-8"))
    if len(entries) != 5:
        raise ValueError(f"dry-run requires 5 payload pairs, found {len(entries)}")
    if {entry["state_id"] for entry in entries} != expected_state_ids:
        raise ValueError("payload state IDs do not match decision states")
    for entry in entries:
        if len(entry["payloads"]) != 2:
            raise ValueError("each state requires one KEEP and one REOPTIMIZE payload")
        keep = OraclePayload(**entry["payloads"][0])
        reoptimize = OraclePayload(**entry["payloads"][1])
        assert_comparable_payloads(keep, reoptimize)
        instance = keep.instance
        depots = len(instance["depot_ids"])
        regions = len(instance["region_ids"])
        products = len(instance["product_ids"])
        if (depots, regions, products) != (2, 5, 3):
            raise ValueError("dry-run oracle dimensions must be 2x5x3")
        if len(keep.x0) != depots or any(len(row) != products for row in keep.x0):
            raise ValueError("x0 dimensions do not match oracle instance")
        if len(keep.y0) != depots:
            raise ValueError("y0 dimensions do not match oracle instance")
        for field in ("base_demand", "demand_deviation", "shortage_penalty"):
            matrix = instance[field]
            if len(matrix) != regions or any(len(row) != products for row in matrix):
                raise ValueError(f"{field} dimensions do not match oracle instance")
        transport = instance["transport_cost"]
        if len(transport) != depots or any(
            len(plane) != regions
            or any(len(row) != products for row in plane)
            for plane in transport
        ):
            raise ValueError("transport cost dimensions do not match oracle instance")
        numeric_values = [
            value
            for matrix in (instance["base_demand"], instance["demand_deviation"], keep.x0)
            for row in matrix
            for value in row
        ]
        numeric_values.extend(instance["capacity"])
        numeric_values.extend(instance["fixed_depot_cost"])
        numeric_values.extend(instance["service_penalty"])
        numeric_values.extend(instance["product_volume"])
        numeric_values.extend(
            value
            for matrix in (
                instance["inventory_cost"],
                instance["shortage_penalty"],
            )
            for row in matrix
            for value in row
        )
        numeric_values.extend(
            value
            for plane in transport
            for row in plane
            for value in row
        )
        if not all(math.isfinite(float(value)) and float(value) >= 0 for value in numeric_values):
            raise ValueError("oracle payload values must be finite and nonnegative")
        if not all(0.0 <= float(value) <= 1.0 for value in instance["service_level"]):
            raise ValueError("service levels must use a 0--1 scale")
        if not (
            math.isfinite(keep.budget)
            and keep.budget > 0
            and math.isfinite(keep.lambda_r)
            and keep.lambda_r >= 0
            and 0 <= keep.gamma <= regions
        ):
            raise ValueError("budget, lambda, or Gamma scaling is invalid")
        fixed_cost = sum(
            instance["fixed_depot_cost"][i] * keep.y0[i] for i in range(depots)
        )
        inventory_cost = sum(
            instance["inventory_cost"][i][j] * keep.x0[i][j]
            for i in range(depots)
            for j in range(products)
        )
        if keep.budget < fixed_cost + inventory_cost:
            raise ValueError("calibrated incumbent exceeds the shared financial budget")
        if instance["provenance"].get("inventory_nodes") != "CALIBRATED_NOT_OBSERVED":
            raise ValueError("oracle payload inventory nodes must be labeled calibrated")
    return {
        "payload_pairs": 5,
        "payload_dimensions": {"inventory_nodes": 2, "demand_regions": 5, "families": 3},
        "keep_reoptimize_comparable": True,
        "units_and_cost_scaling_valid": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("states", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--payloads",
        type=Path,
        default=Path("artifacts/decision_sensitivity_pilot/dry_run_oracle_payloads.json"),
    )
    args = parser.parse_args()
    if args.dry_run:
        try:
            result = validate_dry_run(args.states)
            with args.states.open(encoding="utf-8", newline="") as handle:
                state_ids = {row["state_id"] for row in csv.DictReader(handle)}
            result.update(validate_payload_file(args.payloads, state_ids))
        except (FileNotFoundError, KeyError, ValueError) as error:
            print(
                json.dumps(
                    {
                        "status": "BLOCKED",
                        "reason": str(error),
                        "executed_states": 0,
                        "solver_calls": 0,
                    },
                    indent=2,
                )
            )
            raise SystemExit(2) from error
        print(json.dumps(result, indent=2))
        return

    adapter = BlockedOracleAdapter()
    try:
        adapter.evaluate({})
    except OracleIntegrationBlocked as error:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "reason": str(error),
                    "executed_states": 0,
                    "solver_calls": adapter.solver_calls,
                },
                indent=2,
            )
        )
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
