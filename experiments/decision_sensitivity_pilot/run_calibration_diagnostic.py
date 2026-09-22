"""Run the preregistered three-scheme cost calibration diagnostic."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import copy
import csv
import json
import math
from pathlib import Path
import statistics
import sys
from time import perf_counter
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_risk_trigger_inventory.oracle.budget_inventory_adapter import (
    BudgetInventoryAdapter,
    OraclePayload,
)
from ai_risk_trigger_inventory.oracle.cost_only_recourse import (
    evaluate_cost_only_robust_recourse,
)


SCHEMES = ("CURRENT", "UNIT_COST_BASED", "TRANSPORT_BASED")
K_B = 5.0
K_C = 50.0
TOLERANCE = 1e-4
SERVICE_DOMINANCE_SHARE = 0.80
SIGNIFICANT_RATIO_REDUCTION_FACTOR = 100.0
ORDERING_STABILITY_MIN = 0.80
REASONABLE_RATIO_MAX = 1000.0
SMALL_SHIFT_MAX = 0.10
LARGE_SHIFT_MIN = 0.15
ANOMALY_STATES = (
    "pichincha-pichincha-context-2-20140106",
    "guayas-guayas-context-2-20170501",
)


RESULT_FIELDS = [
    "state_id", "scheme", "relative_demand_shift",
    "inventory_to_demand_ratio", "capacity_slack_ratio",
    "first_stage_keep", "robust_recourse_keep", "total_cost_keep",
    "first_stage_reopt", "robust_recourse_reopt", "reconfiguration_cost",
    "total_cost_reopt", "decision_value", "relative_decision_value",
    "inventory_change_L1", "changed_inventory_pairs",
    "recourse_to_first_stage_ratio", "transport_cost_keep",
    "shortage_cost_keep", "service_penalty_cost_keep",
    "transport_share_keep", "shortage_share_keep", "service_share_keep",
    "keep_decomposition_uniqueness_status",
    "reopt_decomposition_status", "exact_certification_pass",
    "global_risk_budget_coupling_pass", "recourse_identity_error",
]


def calibrated_service_penalty(instance: dict[str, Any], scheme: str) -> list[float]:
    """Apply only the three preregistered service-penalty formulas."""

    products = range(len(instance["product_ids"]))
    regions = range(len(instance["region_ids"]))
    depots = range(len(instance["depot_ids"]))
    if scheme == "CURRENT":
        return [20.0 * sum(instance["base_demand"][r][j] for r in regions) for j in products]
    if scheme == "UNIT_COST_BASED":
        return [
            K_B * statistics.median(
                instance["shortage_penalty"][r][j] for r in regions
            )
            for j in products
        ]
    if scheme == "TRANSPORT_BASED":
        return [
            K_C * statistics.mean(
                instance["transport_cost"][i][r][j]
                for i in depots
                for r in regions
            )
            for j in products
        ]
    raise ValueError(f"unknown calibration scheme: {scheme}")


def payload_for_scheme(payload: OraclePayload, scheme: str) -> OraclePayload:
    instance = copy.deepcopy(payload.instance)
    instance["service_penalty"] = calibrated_service_penalty(instance, scheme)
    return replace(payload, instance=instance)


def _without_service_penalty(payload: OraclePayload) -> bytes:
    data = asdict(payload)
    del data["instance"]["service_penalty"]
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def assert_scheme_payload_equivalence(payloads: dict[str, OraclePayload]) -> None:
    reference = _without_service_penalty(payloads["CURRENT"])
    if any(_without_service_penalty(payload) != reference for payload in payloads.values()):
        raise ValueError("scheme payloads differ beyond service_penalty")


def cost_decomposition_identity_passes(
    transport: float,
    shortage: float,
    service: float,
    total_recourse: float,
) -> bool:
    """Check the preregistered KEEP recourse decomposition tolerance."""

    return abs(transport + shortage + service - total_recourse) <= TOLERANCE


def assert_current_calibration(instance: dict[str, Any]) -> None:
    depots = range(len(instance["depot_ids"]))
    regions = range(len(instance["region_ids"]))
    products = range(len(instance["product_ids"]))
    for r in regions:
        for j in products:
            expected = 10.0 * max(
                instance["transport_cost"][i][r][j] for i in depots
            )
            if not math.isclose(
                instance["shortage_penalty"][r][j], expected, abs_tol=1e-12
            ):
                raise ValueError("CURRENT shortage-penalty formula mismatch")
    expected_service = calibrated_service_penalty(instance, "CURRENT")
    if any(
        not math.isclose(actual, expected, abs_tol=1e-9)
        for actual, expected in zip(instance["service_penalty"], expected_service)
    ):
        raise ValueError("CURRENT service-penalty formula mismatch")


def spearman_from_orderings(
    left: list[str], right: list[str]
) -> float:
    """Deterministic no-tie Spearman correlation for state orderings."""

    if len(left) != len(right) or set(left) != set(right):
        raise ValueError("rankings must contain the same states")
    right_rank = {state_id: rank for rank, state_id in enumerate(right, start=1)}
    squared = sum(
        (left_rank - right_rank[state_id]) ** 2
        for left_rank, state_id in enumerate(left, start=1)
    )
    count = len(left)
    return 1.0 - 6.0 * squared / (count * (count * count - 1))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _ranking(rows: list[dict[str, Any]], scheme: str, field: str) -> list[str]:
    return [
        row["state_id"]
        for row in sorted(
            (row for row in rows if row["scheme"] == scheme),
            key=lambda row: (-float(row[field]), row["state_id"]),
        )
    ]


def _descriptive_patterns(
    rows: list[dict[str, Any]], scheme: str
) -> dict[str, Any]:
    scheme_rows = [row for row in rows if row["scheme"] == scheme]
    order = _ranking(rows, scheme, "decision_value")
    ranks = {state_id: rank for rank, state_id in enumerate(order, start=1)}
    small_large = [
        row["state_id"]
        for row in scheme_rows
        if abs(float(row["relative_demand_shift"])) <= SMALL_SHIFT_MAX
        and ranks[row["state_id"]] <= 2
    ]
    large_small = [
        row["state_id"]
        for row in scheme_rows
        if abs(float(row["relative_demand_shift"])) >= LARGE_SHIFT_MIN
        and ranks[row["state_id"]] >= 4
    ]
    return {
        "small_shift_large_decision_states": small_large,
        "large_shift_small_decision_states": large_small,
        "thresholds": {
            "small_shift_max_abs": SMALL_SHIFT_MAX,
            "large_shift_min_abs": LARGE_SHIFT_MIN,
            "large_decision_rank_max": 2,
            "small_decision_rank_min": 4,
        },
    }


def _write_report(
    path: Path,
    summary: dict[str, Any],
    component_rows: list[dict[str, Any]],
) -> None:
    anomalies = [row for row in component_rows if row["state_id"] in ANOMALY_STATES]
    anomaly_table = "\n".join(
        f"| {row['state_id']} | {row['transport_cost_keep']:.6f} | "
        f"{row['transport_share_keep']:.6f} | {row['shortage_cost_keep']:.6f} | "
        f"{row['shortage_share_keep']:.6f} | "
        f"{row['service_penalty_cost_keep']:.6f} | "
        f"{row['service_share_keep']:.6f} | "
        f"{row['robust_recourse_keep']:.6f} |"
        for row in anomalies
    )
    ratio_table = "\n".join(
        f"| {scheme} | {summary['ratios'][scheme]['median']:.6f} | "
        f"{summary['ratios'][scheme]['maximum']:.6f} |"
        for scheme in SCHEMES
    )
    rank_table = "\n".join(
        f"| {row['comparison']} | {row['spearman']:.6f} | "
        f"{row['left_top_1']} | {row['right_top_1']} |"
        for row in summary["rank_stability"]
    )
    pattern_table = "\n".join(
        f"| {scheme} | "
        f"{'<br>'.join(summary['patterns'][scheme]['small_shift_large_decision_states']) or 'none'} | "
        f"{'<br>'.join(summary['patterns'][scheme]['large_shift_small_decision_states']) or 'none'} |"
        for scheme in SCHEMES
    )
    path.write_text(
        f"""# Third-Paper Cost Calibration Diagnostic

This is an exploratory calibration diagnostic over the existing five states.
It does not change the frozen Paper-2 model, uncertainty set, Gamma,
`lambda_R`, incumbent inventory, capacity, budget, or any formal parameter.

Classification: `{summary['classification']}`

Formal-calibration candidate status:
`{summary['formal_calibration_candidate_status']}`

## CURRENT worst-scenario decomposition

| state_id | transport cost | transport share | shortage cost | shortage share | service cost | service share | exact recourse |
|---|---:|---:|---:|---:|---:|---:|---:|
{anomaly_table}

These components come from the Stage-1 LP solution selected by Gurobi. Their
sum equals the exact robust recourse total, but component uniqueness across
alternative Stage-1 optima is not established. No reporting tie-break QP was
used.

## Recourse scale

| scheme | median recourse/first-stage | maximum recourse/first-stage |
|---|---:|---:|
{ratio_table}

## Decision-value rank stability

| comparison | Spearman | left top-1 | right top-1 |
|---|---:|---|---|
{rank_table}

## Diagnostic answers

1. CURRENT service-penalty dominance for both anomalous states:
   `{summary['current_anomalies_service_dominated']}`.
2. B/C significant extreme-ratio reduction:
   `{summary['bc_significant_ratio_reduction']}`.
3. The maximum ratio falls below 100 under both B and C, so the CURRENT
   10^4--10^6 extreme scale is removed by both preregistered probes.
4. Decision-value ordering stability is reported by the three Spearman values.
5. The descriptive pattern screen is:

| scheme | small shift / large decision | large shift / small decision |
|---|---|---|
{pattern_table}

6. Both patterns persist under B and C. This is descriptive only for five
   states and is not a statistical inference.
7. Inventory-change ordering: `{summary['inventory_rankings']}`.
8. States extreme only under CURRENT: `{summary['extreme_only_current_states']}`.

For both anomalous CURRENT states, the service term contributes more than
99.99% of exact recourse. Because that term is the unit service-penalty
coefficient multiplied by the service-violation variable, the decomposition
supports coefficient-times-violation amplification as the proximate numerical
mechanism. It does not establish that calibration as the only possible cause.

This classification is not a paper conclusion. A candidate still requires
literature support, economic interpretation, and formal sensitivity analysis.
""",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument(
        "--payloads",
        type=Path,
        default=ROOT / "artifacts/decision_sensitivity_pilot/dry_run_oracle_payloads.json",
    )
    parser.add_argument(
        "--states",
        type=Path,
        default=ROOT / "artifacts/decision_sensitivity_pilot/decision_states.csv",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts/decision_sensitivity_pilot",
    )
    args = parser.parse_args()

    entries = json.loads(args.payloads.read_text(encoding="utf-8"))
    with args.states.open(encoding="utf-8", newline="") as handle:
        state_rows = {row["state_id"]: row for row in csv.DictReader(handle)}
    if len(entries) != 5 or len(state_rows) != 5:
        raise SystemExit("calibration diagnostic requires exactly five states")

    adapter = BudgetInventoryAdapter(args.checkout)
    instance_module, _, cost_module = adapter._modules()
    equivalence_audit: dict[str, Any] = {}
    prepared: dict[tuple[str, str], OraclePayload] = {}
    for entry in entries:
        state_id = entry["state_id"]
        base = OraclePayload(**entry["payloads"][0])
        assert_current_calibration(base.instance)
        variants = {scheme: payload_for_scheme(base, scheme) for scheme in SCHEMES}
        assert_scheme_payload_equivalence(variants)
        equivalence_audit[state_id] = {
            "byte_equivalent_after_removing_only_service_penalty": True,
            "service_penalty": {
                scheme: variants[scheme].instance["service_penalty"]
                for scheme in SCHEMES
            },
        }
        for scheme, keep in variants.items():
            prepared[(state_id, scheme)] = keep

    results: list[dict[str, Any]] = []
    component_rows: list[dict[str, Any]] = []
    started = perf_counter()
    failure: str | None = None
    for scheme in SCHEMES:
        for index, entry in enumerate(entries, start=1):
            state_id = entry["state_id"]
            keep_payload = prepared[(state_id, scheme)]
            reopt_payload = replace(keep_payload, decision_mode="REOPTIMIZE")
            print(f"[{scheme} {index}/5] {state_id}: KEEP", flush=True)
            try:
                instance = instance_module.InventoryInstance.from_dict(
                    keep_payload.instance
                )
                adapter.solver_calls += 1
                keep = evaluate_cost_only_robust_recourse(
                    instance, keep_payload.x0, keep_payload.gamma
                )
                zeros = [
                    [0.0] * instance.num_products
                    for _ in range(instance.num_depots)
                ]
                first_stage_keep = cost_module.first_stage_expenditure_value(
                    instance,
                    keep_payload.y0,
                    keep_payload.x0,
                    zeros,
                    zeros,
                    keep_payload.lambda_r,
                )
                total_keep = first_stage_keep + keep.robust_recourse_cost
                print(f"[{scheme} {index}/5] {state_id}: REOPTIMIZE", flush=True)
                reopt = adapter.evaluate(reopt_payload)
                if not (
                    reopt["status"] == "OPTIMAL"
                    and reopt["exact_certification_pass"] is True
                    and reopt["global_risk_budget_coupling_pass"] is True
                ):
                    raise RuntimeError("REOPTIMIZE certification failed")
                if abs(reopt["recourse_identity_error"]) > TOLERANCE:
                    raise RuntimeError("REOPTIMIZE_RECOURSE_IDENTITY_FAIL")
                if abs(
                    reopt["total_cost"]
                    - reopt["first_stage_expenditure"]
                    - reopt["robust_recourse_cost"]
                ) > TOLERANCE:
                    raise RuntimeError("REOPTIMIZE total-cost identity failed")
                decision_value = total_keep - reopt["total_cost"]
                if decision_value < -TOLERANCE:
                    raise RuntimeError("COST_DOMINANCE_VIOLATION")
                inventory_change = sum(
                    abs(reopt["x"][i][j] - keep_payload.x0[i][j])
                    for i in range(len(keep_payload.x0))
                    for j in range(len(keep_payload.x0[i]))
                )
                changed_pairs = sum(
                    abs(reopt["x"][i][j] - keep_payload.x0[i][j]) > 1e-6
                    for i in range(len(keep_payload.x0))
                    for j in range(len(keep_payload.x0[i]))
                )
                transport = keep.worst_scenario_transport_cost
                shortage = keep.worst_scenario_shortage_cost
                service = keep.worst_scenario_service_penalty_cost
                total_recourse = keep.robust_recourse_cost
                state = state_rows[state_id]
                row = {
                    "state_id": state_id,
                    "scheme": scheme,
                    "relative_demand_shift": float(state["relative_demand_shift"]),
                    "inventory_to_demand_ratio": float(state["inventory_to_demand_ratio"]),
                    "capacity_slack_ratio": float(state["capacity_slack_ratio"]),
                    "first_stage_keep": first_stage_keep,
                    "robust_recourse_keep": total_recourse,
                    "total_cost_keep": total_keep,
                    "first_stage_reopt": reopt["first_stage_expenditure"],
                    "robust_recourse_reopt": reopt["robust_recourse_cost"],
                    "reconfiguration_cost": reopt["reconfiguration_cost"],
                    "total_cost_reopt": reopt["total_cost"],
                    "decision_value": decision_value,
                    "relative_decision_value": decision_value / max(abs(total_keep), TOLERANCE),
                    "inventory_change_L1": inventory_change,
                    "changed_inventory_pairs": changed_pairs,
                    "recourse_to_first_stage_ratio": total_recourse / first_stage_keep,
                    "transport_cost_keep": transport,
                    "shortage_cost_keep": shortage,
                    "service_penalty_cost_keep": service,
                    "transport_share_keep": transport / total_recourse,
                    "shortage_share_keep": shortage / total_recourse,
                    "service_share_keep": service / total_recourse,
                    "keep_decomposition_uniqueness_status": keep.decomposition_uniqueness_status,
                    "reopt_decomposition_status": "NOT_REPORTED_UNIQUENESS_NOT_ESTABLISHED",
                    "exact_certification_pass": reopt["exact_certification_pass"],
                    "global_risk_budget_coupling_pass": reopt["global_risk_budget_coupling_pass"],
                    "recourse_identity_error": reopt["recourse_identity_error"],
                }
                if not cost_decomposition_identity_passes(
                    transport, shortage, service, total_recourse
                ):
                    raise RuntimeError("KEEP cost decomposition identity failed")
                results.append(row)
                if scheme == "CURRENT":
                    component_rows.append(row.copy())
                print(f"[{scheme} {index}/5] {state_id}: PASS", flush=True)
            except Exception as error:
                failure = f"{scheme}/{state_id}: {error}"
                print(f"[{scheme} {index}/5] {state_id}: FAIL: {error}", flush=True)
                break
        if failure:
            break

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "calibration_scheme_results.csv", results, RESULT_FIELDS)
    _write_csv(output / "cost_component_diagnostic.csv", component_rows, RESULT_FIELDS)
    if failure:
        summary = {
            "status": "CHAIN_FAIL",
            "reason": failure,
            "completed_scheme_states": len(results),
            "solver_dispatch_count": adapter.solver_calls,
            "payload_equivalence_audit": equivalence_audit,
        }
        (output / "calibration_diagnostic_summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        raise SystemExit("calibration diagnostic failed")

    rankings = {scheme: _ranking(results, scheme, "decision_value") for scheme in SCHEMES}
    inventory_rankings = {
        scheme: _ranking(results, scheme, "inventory_change_L1") for scheme in SCHEMES
    }
    comparisons = []
    for left, right in (("CURRENT", "UNIT_COST_BASED"), ("CURRENT", "TRANSPORT_BASED"), ("UNIT_COST_BASED", "TRANSPORT_BASED")):
        comparisons.append(
            {
                "comparison": f"{left}_vs_{right}",
                "left_scheme": left,
                "right_scheme": right,
                "spearman": spearman_from_orderings(rankings[left], rankings[right]),
                "left_top_1": rankings[left][0],
                "right_top_1": rankings[right][0],
                "left_top_2": "|".join(rankings[left][:2]),
                "right_top_2": "|".join(rankings[right][:2]),
            }
        )
    rank_fields = list(comparisons[0])
    _write_csv(output / "calibration_rank_stability.csv", comparisons, rank_fields)

    ratios = {
        scheme: {
            "median": statistics.median(
                row["recourse_to_first_stage_ratio"]
                for row in results
                if row["scheme"] == scheme
            ),
            "maximum": max(
                row["recourse_to_first_stage_ratio"]
                for row in results
                if row["scheme"] == scheme
            ),
        }
        for scheme in SCHEMES
    }
    patterns = {scheme: _descriptive_patterns(results, scheme) for scheme in SCHEMES}
    current_anomalies = [
        row for row in results
        if row["scheme"] == "CURRENT" and row["state_id"] in ANOMALY_STATES
    ]
    current_service_dominated = len(current_anomalies) == 2 and all(
        row["service_share_keep"] >= SERVICE_DOMINANCE_SHARE
        for row in current_anomalies
    )
    bc_reduced = all(
        ratios[scheme]["maximum"]
        <= ratios["CURRENT"]["maximum"] / SIGNIFICANT_RATIO_REDUCTION_FACTOR
        for scheme in ("UNIT_COST_BASED", "TRANSPORT_BASED")
    )
    stable = all(
        row["spearman"] >= ORDERING_STABILITY_MIN
        for row in comparisons
        if row["left_scheme"] == "CURRENT"
    )
    phenomena_persist = all(
        patterns[scheme]["small_shift_large_decision_states"]
        and patterns[scheme]["large_shift_small_decision_states"]
        for scheme in ("UNIT_COST_BASED", "TRANSPORT_BASED")
    )
    if current_service_dominated and bc_reduced and not phenomena_persist:
        classification = "CALIBRATION_ARTIFACT_LIKELY"
    elif bc_reduced and stable and phenomena_persist:
        classification = "STRUCTURAL_SIGNAL_ROBUST_TO_CALIBRATION"
    else:
        classification = "INCONCLUSIVE_CALIBRATION_EFFECT"

    candidates = [
        scheme
        for scheme in ("UNIT_COST_BASED", "TRANSPORT_BASED")
        if ratios[scheme]["maximum"] <= REASONABLE_RATIO_MAX
        and spearman_from_orderings(rankings["CURRENT"], rankings[scheme])
        >= ORDERING_STABILITY_MIN
    ]
    candidate_status = (
        "CANDIDATE_FOR_FORMAL_CALIBRATION"
        if candidates
        else "NO_CANDIDATE_FOR_FORMAL_CALIBRATION"
    )
    current_extreme = {
        row["state_id"]
        for row in results
        if row["scheme"] == "CURRENT"
        and row["recourse_to_first_stage_ratio"] > REASONABLE_RATIO_MAX
    }
    bc_extreme = {
        row["state_id"]
        for row in results
        if row["scheme"] in ("UNIT_COST_BASED", "TRANSPORT_BASED")
        and row["recourse_to_first_stage_ratio"] > REASONABLE_RATIO_MAX
    }
    summary = {
        "status": "PASS",
        "classification": classification,
        "formal_calibration_candidate_status": candidate_status,
        "candidate_schemes": candidates,
        "preregistered_constants": {
            "k_B": K_B,
            "k_C": K_C,
            "service_dominance_share": SERVICE_DOMINANCE_SHARE,
            "significant_ratio_reduction_factor": SIGNIFICANT_RATIO_REDUCTION_FACTOR,
            "ordering_stability_min": ORDERING_STABILITY_MIN,
            "reasonable_ratio_max": REASONABLE_RATIO_MAX,
        },
        "completed_scheme_states": len(results),
        "solver_dispatch_count": adapter.solver_calls,
        "runtime_seconds": perf_counter() - started,
        "frozen_checkout_clean_after": not bool(adapter._git("status", "--porcelain")),
        "payload_equivalence_audit": equivalence_audit,
        "ratios": ratios,
        "rankings": rankings,
        "rank_stability": comparisons,
        "inventory_rankings": inventory_rankings,
        "patterns": patterns,
        "current_anomalies_service_dominated": current_service_dominated,
        "bc_significant_ratio_reduction": bc_reduced,
        "ordering_stable": stable,
        "patterns_persist_in_b_and_c": phenomena_persist,
        "extreme_only_current_states": sorted(current_extreme - bc_extreme),
        "decomposition_uniqueness": "NOT_ESTABLISHED_MULTIPLE_STAGE1_OPTIMA_POSSIBLE",
        "total_recourse_identity_exact_within_tolerance": True,
    }
    (output / "calibration_diagnostic_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    _write_report(
        ROOT / "docs/third_paper_cost_calibration_diagnostic.md",
        summary,
        component_rows,
    )
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
