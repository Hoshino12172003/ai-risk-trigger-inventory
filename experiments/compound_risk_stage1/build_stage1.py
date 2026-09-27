"""Build the preregistered base state, event catalog, and 22 scenarios."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import statistics
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_risk_trigger_inventory.compound_risk.events import build_event_catalog
from ai_risk_trigger_inventory.compound_risk.scenarios import enumerate_scenarios


BASE_COMMIT = "2f0ea2d0af2af5424cf947866a6b1e9ca121859c"
PAPER2_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
CALIBRATION_ID = "UNIT_COST_BASED"


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def select_base_state(rows: list[dict[str, str]]) -> dict[str, str]:
    promotion_median = statistics.median(float(row["promotion_intensity"]) for row in rows)
    normal = [
        row for row in rows
        if row["holiday_flag"].lower() == "false"
        and float(row["promotion_intensity"]) <= promotion_median
    ]
    if not normal:
        raise RuntimeError("STOP: existing artifact cannot identify a normal week")
    return min(
        normal,
        key=lambda row: (
            abs(float(row["relative_demand_shift"])), row["state"],
            row["week_start"], row["state_id"],
        ),
    )


def main() -> None:
    source = ROOT / "artifacts" / "decision_sensitivity_pilot"
    output = ROOT / "artifacts" / "compound_risk_stage1"
    with (source / "twenty_state_candidates.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    selected = select_base_state(rows)
    payload_entries = json.loads((source / "twenty_state_payloads.json").read_text(encoding="utf-8"))
    entry = next(item for item in payload_entries if item["state_id"] == selected["state_id"])
    payload = entry["payloads"][0]
    instance = payload["instance"]
    instance["service_penalty"] = [
        5.0 * statistics.median(instance["shortage_penalty"][r][j] for r in range(5))
        for j in range(3)
    ]
    base = {
        "base_commit": BASE_COMMIT,
        "paper2_frozen_sha": PAPER2_SHA,
        "calibration_role": "PRIMARY_PILOT_CALIBRATION",
        "calibration_id": CALIBRATION_ID,
        "selection_rule": "existing normal week, then abs(relative_demand_shift), state lexical, date ascending, stable ID",
        "normal_week_definition": "holiday_flag false and promotion_intensity <= median of twenty-state candidates",
        "state_id": selected["state_id"],
        "date": selected["week_start"],
        "state": selected["state"],
        "relative_demand_shift": float(selected["relative_demand_shift"]),
        "demand_regions": instance["region_ids"],
        "product_families": instance["product_ids"],
        "baseline_demand": instance["base_demand"],
        "incumbent_inventory_x0": payload["x0"],
        "incumbent_depot_activation_y0": payload["y0"],
        "first_stage_parameters": {
            "budget": payload["budget"], "lambda_r": payload["lambda_r"],
            "capacity": instance["capacity"],
            "inventory_upper_bound": instance["inventory_upper_bound"],
            "fixed_depot_cost": instance["fixed_depot_cost"],
            "inventory_cost": instance["inventory_cost"],
            "product_volume": instance["product_volume"],
        },
        "instance": instance,
    }
    events = build_event_catalog(instance, "MAIN")
    scenarios = enumerate_scenarios(instance, events, gamma=2)
    if len(events) != 6 or len(scenarios) != 22:
        raise RuntimeError("STOP: unexpected event or scenario count")
    output.mkdir(parents=True, exist_ok=True)
    (output / "base_state.json").write_text(json.dumps(base, indent=2) + "\n", encoding="utf-8")
    event_output = {
        "base_commit": BASE_COMMIT, "paper2_frozen_sha": PAPER2_SHA,
        "calibration_id": CALIBRATION_ID, "magnitude": "MAIN",
        "events": [event.to_dict(instance) for event in events],
    }
    (output / "event_catalog.json").write_text(json.dumps(event_output, indent=2) + "\n", encoding="utf-8")
    _write_csv(
        output / "scenario_catalog.csv",
        [
            {
                "scenario_id": scenario.scenario_id,
                "active_events": "|".join(scenario.active_events),
                "event_types": "|".join(scenario.event_types),
                "demand_multiplier_matrix": json.dumps(scenario.demand_multiplier, separators=(",", ":")),
                "route_availability_matrix": json.dumps(scenario.route_availability, separators=(",", ":")),
                "base_commit": BASE_COMMIT,
                "paper2_frozen_sha": PAPER2_SHA,
                "calibration_id": CALIBRATION_ID,
            }
            for scenario in scenarios
        ],
    )
    print(json.dumps({"state_id": selected["state_id"], "events": 6, "scenarios": 22}, indent=2))


if __name__ == "__main__":
    main()
