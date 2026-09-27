"""Build Stage-3C mapping artifacts without optimization or model training."""

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
    AffectedArc,
    DemandCalibrationLookup,
    EventMetadata,
    SEVERITY_TO_QUANTILE,
    build_demand_severity_rows,
    map_event_to_demand_impact,
    map_event_to_route_impacts,
    metadata_as_dict,
    route_severity_rows,
)


STAGE3_COMMIT = "1e4ba587e0aff54d7c38e97f69a6cd34c1d3a77b"
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


def _metadata(
    event_id: str,
    risk_type: str,
    event_subtype: str,
    severity: str,
    arcs: tuple[AffectedArc, ...] = (),
) -> EventMetadata:
    return EventMetadata(
        event_id=event_id,
        risk_type=risk_type,
        event_subtype=event_subtype,
        region_scope=("EXAMPLE_REGION",),
        severity=severity,
        start_time="2030-01-01T00:00:00Z",
        expected_duration="ONE_PLANNING_PERIOD_EQUIVALENT",
        evidence_source="SOLVER_FREE_MAPPING_EXAMPLE",
        affected_arcs=arcs,
    )


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
    }
    prior_before = {name: _directory_hashes(path) for name, path in prior_dirs.items()}
    stage3a = prior_dirs["stage3a"]
    lookup = DemandCalibrationLookup.from_stage3a_artifacts(stage3a)

    demand_rows = build_demand_severity_rows(lookup)
    route_rows = route_severity_rows()
    regional_rows = []
    for severity in SEVERITY_TO_QUANTILE:
        demand = lookup.lookup("D4_GENERIC_EVENT_PROXY", severity)
        route_state = "MODERATE_DISRUPTION" if severity == "MODERATE" else "SEVERE_DISRUPTION"
        formal_route = next(row for row in route_rows if row["route_state"] == route_state)
        regional_rows.append({
            "risk_type": "REGIONAL_EMERGENCY_DISRUPTION",
            "severity": severity,
            "demand_calibration_stratum": "D4_GENERIC_EVENT_PROXY",
            "demand_quantile_level": demand["quantile_level"],
            "delta_D": demand["delta_D"],
            "demand_provenance": "INDIRECT_RETAIL_PROXY",
            "demand_status": "INDIRECT_PROXY",
            "route_state": route_state,
            "delta_A": formal_route["delta_A"],
            "route_provenance": formal_route["provenance"],
            "route_status": formal_route["status"],
        })

    arc_a = (AffectedArc("WH_EXAMPLE_1", "REGION_EXAMPLE_1"),)
    arc_b = (AffectedArc("WH_EXAMPLE_2", "REGION_EXAMPLE_2"),)
    examples_metadata = {
        "A_FLASH_PROMOTION_HIGH": _metadata(
            "EX_A", "FLASH_DEMAND_SURGE", "flash promotion", "HIGH",
        ),
        "B_PUBLIC_EVENT_EXTREME": _metadata(
            "EX_B", "FLASH_DEMAND_SURGE", "scheduled public event", "EXTREME",
        ),
        "C_REGIONAL_EMERGENCY_HIGH": _metadata(
            "EX_C", "REGIONAL_EMERGENCY_DISRUPTION", "regional emergency", "HIGH", arc_a,
        ),
        "D_CONFIRMED_CLOSURE": _metadata(
            "EX_D", "TRANSPORT_NETWORK_DISRUPTION", "confirmed full road closure", "EXTREME", arc_b,
        ),
    }
    examples = {
        "A": {
            "metadata": metadata_as_dict(examples_metadata["A_FLASH_PROMOTION_HIGH"]),
            "demand_mapping": map_event_to_demand_impact(examples_metadata["A_FLASH_PROMOTION_HIGH"], lookup),
        },
        "B": {
            "metadata": metadata_as_dict(examples_metadata["B_PUBLIC_EVENT_EXTREME"]),
            "demand_mapping": map_event_to_demand_impact(examples_metadata["B_PUBLIC_EVENT_EXTREME"], lookup),
        },
        "C": {
            "metadata": metadata_as_dict(examples_metadata["C_REGIONAL_EMERGENCY_HIGH"]),
            "demand_mapping": map_event_to_demand_impact(examples_metadata["C_REGIONAL_EMERGENCY_HIGH"], lookup),
            "route_mapping": map_event_to_route_impacts(examples_metadata["C_REGIONAL_EMERGENCY_HIGH"]),
        },
        "D": {
            "metadata": metadata_as_dict(examples_metadata["D_CONFIRMED_CLOSURE"]),
            "route_mapping": map_event_to_route_impacts(examples_metadata["D_CONFIRMED_CLOSURE"]),
        },
    }

    output = ROOT / "artifacts" / "risk_impact_mapping_stage3c"
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "demand_severity_mapping.csv", demand_rows)
    _write_csv(output / "route_severity_mapping.csv", route_rows)
    _write_csv(output / "regional_emergency_mapping.csv", regional_rows)
    (output / "mapping_examples.json").write_text(json.dumps(examples, indent=2) + "\n", encoding="utf-8")

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

    audit = {
        "status": "STAGE_3C_MAPPING_PASS_WITH_ROUTE_GAP",
        "stage3_base_commit": STAGE3_COMMIT,
        "paper2_frozen_sha_before": PAPER2_SHA,
        "paper2_frozen_sha_after": paper2_sha_after,
        "paper2_clean_before": True,
        "paper2_clean_after": paper2_clean_after,
        "optimizer_dispatches": 0,
        "ml_training_dispatches": 0,
        "severity_to_quantile": dict(SEVERITY_TO_QUANTILE),
        "q99_used": False,
        "product_specific_mapping_used": False,
        "demand_mapping_rows": len(demand_rows),
        "route_formal_state_rows": sum(row["mapping_role"] == "FORMAL_STATE" for row in route_rows),
        "route_stress_reference_rows": sum(row["mapping_role"] == "REFERENCE_ONLY" for row in route_rows),
        "regional_emergency_mapping_rows": len(regional_rows),
        "unresolved_route_states": ["MODERATE_DISRUPTION", "SEVERE_DISRUPTION"],
        "prior_artifacts_unchanged": unchanged,
        "prior_artifact_hashes_before": prior_before,
        "prior_artifact_hashes_after": prior_after,
        "duration_treatment": "metadata only; folded into one planning-period effective service fraction",
    }
    (output / "execution_audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": audit["status"],
        "demand_mapping_rows": audit["demand_mapping_rows"],
        "route_formal_state_rows": audit["route_formal_state_rows"],
        "regional_emergency_mapping_rows": audit["regional_emergency_mapping_rows"],
    }, indent=2))


if __name__ == "__main__":
    main()
