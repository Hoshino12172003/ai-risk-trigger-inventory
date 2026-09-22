from dataclasses import asdict
import csv
import json
from pathlib import Path
import sys

import pytest

from ai_risk_trigger_inventory.oracle.budget_inventory_adapter import OraclePayload


EXPERIMENT = (
    Path(__file__).resolve().parents[1]
    / "experiments"
    / "decision_sensitivity_pilot"
)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXPERIMENT))

from run_calibration_diagnostic import (
    assert_scheme_payload_equivalence,
    calibrated_service_penalty,
    cost_decomposition_identity_passes,
    payload_for_scheme,
    spearman_from_orderings,
)


def _instance() -> dict:
    return {
        "depot_ids": ["d1", "d2"],
        "region_ids": ["r1", "r2"],
        "product_ids": ["p1", "p2"],
        "base_demand": [[10.0, 20.0], [30.0, 40.0]],
        "demand_deviation": [[1.0, 2.0], [3.0, 4.0]],
        "transport_cost": [
            [[1.0, 2.0], [3.0, 4.0]],
            [[5.0, 6.0], [7.0, 8.0]],
        ],
        "shortage_penalty": [[50.0, 60.0], [70.0, 80.0]],
        "service_penalty": [800.0, 1200.0],
        "service_level": [0.9, 0.9],
        "capacity": [100.0, 100.0],
        "inventory_upper_bound": [[50.0, 50.0], [50.0, 50.0]],
        "fixed_depot_cost": [1.0, 1.0],
        "inventory_cost": [[1.0, 1.0], [1.0, 1.0]],
        "product_volume": [1.0, 1.0],
    }


def _payload() -> OraclePayload:
    return OraclePayload(
        decision_mode="KEEP",
        instance=_instance(),
        x0=[[10.0, 10.0], [10.0, 10.0]],
        y0=[1, 1],
        budget=100.0,
        gamma=1,
        lambda_r=0.05,
        active_network_id="network",
        evaluation_horizon="2020-01-06/2020-01-12",
    )


def test_calibration_values_are_deterministic() -> None:
    instance = _instance()

    assert calibrated_service_penalty(instance, "CURRENT") == [800.0, 1200.0]
    assert calibrated_service_penalty(instance, "UNIT_COST_BASED") == [300.0, 350.0]
    assert calibrated_service_penalty(instance, "TRANSPORT_BASED") == [200.0, 250.0]


def test_schemes_change_only_service_penalty() -> None:
    base = _payload()
    variants = {
        scheme: payload_for_scheme(base, scheme)
        for scheme in ("CURRENT", "UNIT_COST_BASED", "TRANSPORT_BASED")
    }

    assert_scheme_payload_equivalence(variants)
    original = asdict(base)
    for variant in variants.values():
        changed = asdict(variant)
        original_service = original["instance"].pop("service_penalty")
        changed_service = changed["instance"].pop("service_penalty")
        assert changed == original
        original["instance"]["service_penalty"] = original_service
        assert changed_service == variant.instance["service_penalty"]


def test_payload_equivalence_rejects_non_service_change() -> None:
    variants = {
        scheme: payload_for_scheme(_payload(), scheme)
        for scheme in ("CURRENT", "UNIT_COST_BASED", "TRANSPORT_BASED")
    }
    variants["TRANSPORT_BASED"].instance["capacity"][0] = 99.0

    with pytest.raises(ValueError, match="differ beyond service_penalty"):
        assert_scheme_payload_equivalence(variants)


def test_cost_decomposition_must_sum_to_total_recourse() -> None:
    assert cost_decomposition_identity_passes(10.0, 20.0, 30.0, 60.0)
    assert not cost_decomposition_identity_passes(10.0, 20.0, 30.0, 61.0)


def test_ranking_comparison_is_deterministic() -> None:
    left = ["a", "b", "c", "d", "e"]
    right = ["a", "c", "b", "e", "d"]

    assert spearman_from_orderings(left, right) == 0.8
    assert spearman_from_orderings(left, right) == spearman_from_orderings(
        left, right
    )


def test_generated_diagnostic_is_complete_and_exact_certified() -> None:
    artifact = ROOT / "artifacts" / "decision_sensitivity_pilot"
    with (artifact / "calibration_scheme_results.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    summary = json.loads(
        (artifact / "calibration_diagnostic_summary.json").read_text(
            encoding="utf-8"
        )
    )

    assert len(rows) == 15
    assert all(row["exact_certification_pass"] == "True" for row in rows)
    assert all(
        row["global_risk_budget_coupling_pass"] == "True" for row in rows
    )
    assert all(abs(float(row["recourse_identity_error"])) <= 1e-4 for row in rows)
    assert summary["solver_dispatch_count"] == 45
    assert summary["frozen_checkout_clean_after"] is True
    assert all(
        audit["byte_equivalent_after_removing_only_service_penalty"] is True
        for audit in summary["payload_equivalence_audit"].values()
    )


def test_generated_keep_decompositions_sum_to_exact_recourse() -> None:
    path = (
        ROOT
        / "artifacts"
        / "decision_sensitivity_pilot"
        / "cost_component_diagnostic.csv"
    )
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 5
    for row in rows:
        assert cost_decomposition_identity_passes(
            float(row["transport_cost_keep"]),
            float(row["shortage_cost_keep"]),
            float(row["service_penalty_cost_keep"]),
            float(row["robust_recourse_keep"]),
        )
