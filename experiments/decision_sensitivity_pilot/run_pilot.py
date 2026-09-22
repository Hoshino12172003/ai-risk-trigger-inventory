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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("states", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        try:
            result = validate_dry_run(args.states)
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
