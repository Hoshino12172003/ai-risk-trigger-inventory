"""Build frozen Stage-3D route calibration artifacts without ML or optimization."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_risk_trigger_inventory.calibration.event_mapping import (
    DemandCalibrationLookup,
    SEVERITY_TO_QUANTILE,
)
from ai_risk_trigger_inventory.calibration.route_calibration import (
    ROUTE_CALIBRATION,
    ROUTE_SENSITIVITY_GRID,
    evidence_rows,
    route_calibration_rows,
)


STAGE3C_COMMIT = "1620f4787347ef59e8d421e2dca89beef296aa4d"
PAPER2_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _directory_hashes(path: Path) -> dict[str, str]:
    return {
        str(file.relative_to(ROOT)).replace("\\", "/"): _digest(file)
        for file in sorted(path.rglob("*")) if file.is_file()
    }


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False, lineterminator="\n")


def _route_state_for_severity(severity: str) -> str:
    return "MODERATE_DISRUPTION" if severity == "MODERATE" else "SEVERE_DISRUPTION"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper2-checkout", required=True, type=Path)
    args = parser.parse_args()

    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=args.paper2_checkout, text=True).strip() != PAPER2_SHA:
        raise SystemExit("STOP: Paper-2 frozen SHA mismatch")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=args.paper2_checkout, text=True).strip():
        raise SystemExit("STOP: Paper-2 frozen checkout is dirty")

    prior_dirs = {
        "stage1": ROOT / "artifacts" / "compound_risk_stage1",
        "stage2": ROOT / "artifacts" / "compound_risk_stage2",
        "stage3a": ROOT / "artifacts" / "risk_impact_calibration_stage3",
        "stage3c": ROOT / "artifacts" / "risk_impact_mapping_stage3c",
    }
    prior_before = {name: _directory_hashes(path) for name, path in prior_dirs.items()}
    lookup = DemandCalibrationLookup.from_stage3a_artifacts(prior_dirs["stage3a"])

    regional_rows = []
    transport_rows = []
    for severity in SEVERITY_TO_QUANTILE:
        route_state = _route_state_for_severity(severity)
        route = ROUTE_CALIBRATION[route_state]
        demand = lookup.lookup("D4_GENERIC_EVENT_PROXY", severity)
        common_route = {
            "severity": severity,
            "route_state": route_state,
            "delta_A_lower": route["delta_A_lower"],
            "delta_A_main": route["delta_A_main"],
            "delta_A_upper": route["delta_A_upper"],
            "route_provenance": route["provenance"],
            "route_evidence_status": route["evidence_status"],
            "explicit_closure_rule": "explicit closure evidence overrides route state to CLOSURE",
            "explicit_closure_delta_A": 1.0,
        }
        regional_rows.append({
            "risk_type": "REGIONAL_EMERGENCY_DISRUPTION",
            "demand_calibration_stratum": "D4_GENERIC_EVENT_PROXY",
            "demand_quantile_level": demand["quantile_level"],
            "delta_D": demand["delta_D"],
            "demand_provenance": "INDIRECT_RETAIL_PROXY",
            "demand_status": "INDIRECT_PROXY",
            **common_route,
        })
        transport_rows.append({
            "risk_type": "TRANSPORT_NETWORK_DISRUPTION",
            "delta_D": 0.0,
            "demand_rule": "NO_DEMAND_IMPACT",
            **common_route,
        })

    output = ROOT / "artifacts" / "route_risk_calibration_stage3d"
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "route_calibration_mapping.csv", route_calibration_rows())
    _write_csv(output / "route_sensitivity_grid.csv", list(ROUTE_SENSITIVITY_GRID))
    evidence = evidence_rows()
    _write_csv(output / "evidence_table.csv", evidence)
    _write_csv(output / "regional_emergency_complete_mapping.csv", regional_rows)
    _write_csv(output / "transport_disruption_mapping.csv", transport_rows)

    prior_after = {name: _directory_hashes(path) for name, path in prior_dirs.items()}
    unchanged = {name: prior_before[name] == prior_after[name] for name in prior_dirs}
    paper2_sha_after = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=args.paper2_checkout, text=True,
    ).strip()
    paper2_clean_after = not bool(subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=args.paper2_checkout, text=True,
    ).strip())
    if not all(unchanged.values()) or paper2_sha_after != PAPER2_SHA or not paper2_clean_after:
        raise SystemExit("STOP: prior artifacts or frozen Paper-2 checkout changed")

    evidence_counts = {
        level: sum(row["evidence_level"] == level for row in evidence)
        for level in ("LEVEL_1", "LEVEL_2", "LEVEL_3")
    }
    audit = {
        "status": "STAGE_3D_ROUTE_CALIBRATION_PASS",
        "full_event_parameter_mapping_status": "COMPLETE_FOR_CURRENT_MODEL",
        "real_world_calibration_claim": False,
        "route_calibration_characterization": "literature-anchored; not Ecuador-specific empirical estimation",
        "stage3c_base_commit": STAGE3C_COMMIT,
        "paper2_frozen_sha_before": PAPER2_SHA,
        "paper2_frozen_sha_after": paper2_sha_after,
        "paper2_clean_before": True,
        "paper2_clean_after": paper2_clean_after,
        "optimizer_dispatches": 0,
        "ml_training_dispatches": 0,
        "genai_dispatches": 0,
        "route_state_count": len(ROUTE_CALIBRATION),
        "sensitivity_scenario_count": len(ROUTE_SENSITIVITY_GRID),
        "regional_mapping_rows": len(regional_rows),
        "transport_mapping_rows": len(transport_rows),
        "evidence_rows": len(evidence),
        "evidence_counts_by_level": evidence_counts,
        "prior_artifacts_unchanged": unchanged,
        "prior_artifact_hashes_before": prior_before,
        "prior_artifact_hashes_after": prior_after,
        "delta_A_definition": "planning-period effective route-service loss fraction",
        "availability_semantics": "a_ir(xi)=max(0,1-sum_e delta_A[e,i,r]*xi_e)",
        "excluded_extensions": [
            "duration correction function", "alternative-route correction function",
            "geographic transfer model", "traffic-flow simulation",
        ],
    }
    (output / "execution_audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": audit["status"],
        "full_event_parameter_mapping_status": audit["full_event_parameter_mapping_status"],
        "evidence_counts_by_level": evidence_counts,
        "optimizer_dispatches": 0,
        "ml_training_dispatches": 0,
    }, indent=2))


if __name__ == "__main__":
    main()
