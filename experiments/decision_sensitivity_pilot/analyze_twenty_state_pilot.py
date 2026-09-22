"""Analyze MAIN 20-state evidence and calibration stability separately."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd
import numpy as np

from ai_risk_trigger_inventory.evaluation.decision_sensitivity import (
    StructuralEvidence,
    classify_structural_signal,
)
from analyze_results import STRUCTURAL_FEATURES, _fit, _matched_pairs


def _pair_keys(frame: pd.DataFrame) -> set[tuple[str, str]]:
    return {
        tuple(sorted((row.left_state_id, row.right_state_id)))
        for row in frame.itertuples()
    }


def analyze(main_path: Path, sensitivity_path: Path, output: Path) -> dict:
    main = pd.read_csv(main_path)
    sensitivity = pd.read_csv(sensitivity_path)
    if len(main) != 20 or len(sensitivity) != 20:
        raise ValueError("analysis requires exactly 20 MAIN and 20 sensitivity rows")
    if main["state_id"].nunique() != 20 or set(main["state_id"]) != set(
        sensitivity["state_id"]
    ):
        raise ValueError("MAIN and sensitivity must contain the same 20 unique states")
    if not main["calibration_role"].eq("PRIMARY_PILOT_CALIBRATION").all():
        raise ValueError("classification input must use only MAIN calibration")
    for label, frame in (("MAIN", main), ("sensitivity", sensitivity)):
        if not (
            frame["oracle_status"].eq("OPTIMAL").all()
            and frame["exact_certification_pass"].eq(True).all()
            and frame["global_risk_budget_coupling_pass"].eq(True).all()
            and frame["recourse_identity_error"].abs().le(1e-4).all()
        ):
            raise ValueError(f"{label} contains uncertified oracle results")

    model_a, _ = _fit(main, "A_relative_shift", ["relative_demand_shift"])
    model_b, _ = _fit(main, "B_positive_shift", ["positive_demand_shift"])
    model_c, coefficients_c = _fit(
        main,
        "C_demand_plus_structure",
        ["relative_demand_shift", *STRUCTURAL_FEATURES],
    )
    structural_design = np.column_stack(
        [
            np.ones(len(main)),
            main[["relative_demand_shift", *STRUCTURAL_FEATURES]].to_numpy(
                dtype=float
            ),
        ]
    )
    model_c["design_matrix_rank"] = int(np.linalg.matrix_rank(structural_design))
    model_c["design_matrix_condition_number"] = float(
        np.linalg.cond(structural_design)
    )
    pearson = float(main["relative_demand_shift"].corr(main["decision_value"]))
    spearman = float(
        main["relative_demand_shift"].corr(
            main["decision_value"], method="spearman"
        )
    )
    model_a["pearson_correlation"] = pearson
    model_a["spearman_correlation"] = spearman
    models = pd.DataFrame([model_a, model_b, model_c])
    pairs = _matched_pairs(main)
    demand_only_r2 = max(float(model_a["r2"]), float(model_b["r2"]))
    adjusted_gain = float(
        model_c["adjusted_r2"]
        - max(float(model_a["adjusted_r2"]), float(model_b["adjusted_r2"]))
    )
    evidence = StructuralEvidence(
        executed_states=20,
        demand_only_r2=demand_only_r2,
        adjusted_r2_gain=adjusted_gain,
        matched_pairs=len(pairs),
        represented_states=int(main["state"].nunique()),
        represented_family_sets=int(main["selected_families"].nunique()),
    )
    classification = classify_structural_signal(evidence)

    main_order = main.sort_values(
        ["decision_value", "state_id"], ascending=[False, True]
    )["state_id"].tolist()
    sensitivity_order = sensitivity.sort_values(
        ["decision_value", "state_id"], ascending=[False, True]
    )["state_id"].tolist()
    aligned = main[["state_id", "decision_value"]].merge(
        sensitivity[["state_id", "decision_value"]],
        on="state_id",
        suffixes=("_main", "_sensitivity"),
        validate="one_to_one",
    )
    rank_spearman = float(
        aligned["decision_value_main"].corr(
            aligned["decision_value_sensitivity"], method="spearman"
        )
    )
    sensitivity_pairs = _matched_pairs(sensitivity)
    main_keys = _pair_keys(pairs)
    sensitivity_keys = _pair_keys(sensitivity_pairs)
    stability = pd.DataFrame(
        [
            {
                "comparison": "UNIT_COST_BASED_vs_TRANSPORT_BASED",
                "spearman": rank_spearman,
                "main_top_1": main_order[0],
                "sensitivity_top_1": sensitivity_order[0],
                "main_top_5": "|".join(main_order[:5]),
                "sensitivity_top_5": "|".join(sensitivity_order[:5]),
                "main_bottom_5": "|".join(main_order[-5:]),
                "sensitivity_bottom_5": "|".join(sensitivity_order[-5:]),
                "main_matched_pairs": len(main_keys),
                "sensitivity_matched_pairs": len(sensitivity_keys),
                "persistent_matched_pairs": len(main_keys & sensitivity_keys),
            }
        ]
    )
    top_overlap = len(set(main_order[:5]) & set(sensitivity_order[:5]))
    bottom_overlap = len(set(main_order[-5:]) & set(sensitivity_order[-5:]))

    high_cutoff = float(main["decision_value"].quantile(0.75))
    small_large = main[
        (main["relative_demand_shift"].abs() <= 0.05)
        & (main["decision_value"] >= high_cutoff)
    ]["state_id"].tolist()
    large_cutoff = float(main["positive_demand_shift"].quantile(0.75))
    low_cutoff = float(main["decision_value"].quantile(0.25))
    large_small = main[
        (main["positive_demand_shift"] >= large_cutoff)
        & (main["decision_value"] <= low_cutoff)
    ]["state_id"].tolist()
    summary = {
        "status": "PASS",
        "calibration_for_classification": "PRIMARY_PILOT_CALIBRATION",
        "main_scheme": "UNIT_COST_BASED",
        "sensitivity_scheme": "TRANSPORT_BASED",
        "executed_states_main": 20,
        "executed_states_sensitivity": 20,
        "certification_count": 40,
        "classification": classification,
        "demand_only_r2": demand_only_r2,
        "model_a_r2": float(model_a["r2"]),
        "model_b_r2": float(model_b["r2"]),
        "structural_model_r2": float(model_c["r2"]),
        "structural_model_adjusted_r2": float(model_c["adjusted_r2"]),
        "structural_adjusted_r2_gain": adjusted_gain,
        "pearson": pearson,
        "spearman": spearman,
        "matched_pairs": len(pairs),
        "calibration_rank_spearman": rank_spearman,
        "persistent_matched_pairs": len(main_keys & sensitivity_keys),
        "matched_pair_persistence_rate": (
            len(main_keys & sensitivity_keys) / len(main_keys)
            if main_keys else 1.0
        ),
        "top_5_overlap": top_overlap,
        "bottom_5_overlap": bottom_overlap,
        "small_shift_large_consequence_states": small_large,
        "large_shift_small_consequence_states": large_small,
        "represented_states": sorted(main["state"].unique().tolist()),
        "represented_family_sets": sorted(
            main["selected_families"].unique().tolist()
        ),
        "top_structural_features_by_absolute_standardized_coefficient": (
            coefficients_c.assign(
                magnitude=coefficients_c["standardized_coefficient"].abs()
            )
            .sort_values("magnitude", ascending=False)["feature"]
            .head(3)
            .tolist()
        ),
        "thresholds_unchanged": True,
        "structural_design_matrix_rank": model_c["design_matrix_rank"],
        "structural_design_matrix_condition_number": model_c[
            "design_matrix_condition_number"
        ],
    }
    execution_path = output / "twenty_state_execution_audit.json"
    if execution_path.is_file():
        execution = json.loads(execution_path.read_text(encoding="utf-8"))
        summary.update(
            {
                "constructed_states": execution["constructed_states"],
                "solver_dispatch_count": execution["solver_dispatch_count"],
                "runtime_seconds": execution["runtime_seconds"],
                "oracle_commit": execution["oracle_commit"],
                "frozen_checkout_clean_after": execution[
                    "frozen_checkout_clean_after"
                ],
            }
        )
    output.mkdir(parents=True, exist_ok=True)
    models.to_csv(output / "twenty_state_models.csv", index=False)
    pairs.to_csv(output / "twenty_state_matched_pairs.csv", index=False)
    stability.to_csv(output / "twenty_state_calibration_stability.csv", index=False)
    (output / "twenty_state_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    report = f"""# Twenty-State Structural Pilot Report

Status: exploratory pilot; not a formal paper experiment.

- MAIN calibration: `UNIT_COST_BASED` (`PRIMARY_PILOT_CALIBRATION`)
- Sensitivity calibration: `TRANSPORT_BASED`
- MAIN classification: `{classification}`
- MAIN matched pairs: {len(pairs)}
- Calibration decision-value rank Spearman: {rank_spearman:.6f}
- Persistent matched pairs: {len(main_keys & sensitivity_keys)}
- Top-five overlap: {top_overlap}/5
- Bottom-five overlap: {bottom_overlap}/5
- Small-shift / large-consequence states: {small_large or 'none'}
- Large-shift / small-consequence states: {large_small or 'none'}

| model | R2 | adjusted R2 | MAE | Pearson | Spearman |
|---|---:|---:|---:|---:|---:|
| A relative shift | {model_a['r2']:.6f} | {model_a['adjusted_r2']:.6f} | {model_a['mae']:.6f} | {pearson:.6f} | {spearman:.6f} |
| B positive shift | {model_b['r2']:.6f} | {model_b['adjusted_r2']:.6f} | {model_b['mae']:.6f} | n/a | n/a |
| C demand + structure | {model_c['r2']:.6f} | {model_c['adjusted_r2']:.6f} | {model_c['mae']:.6f} | n/a | n/a |

Best demand-only R2 is {demand_only_r2:.6f}; the structural adjusted-R2 gain
is {adjusted_gain:.6f}. Model C's design matrix rank is
{model_c['design_matrix_rank']} and its condition number is
{model_c['design_matrix_condition_number']:.6f}.

The classification uses only the 20 MAIN rows and the unchanged preregistered
thresholds. Sensitivity rows are used only for ranking, top/bottom stability,
and matched-pair persistence. `UNIT_COST_BASED` remains a pilot calibration,
not a final paper parameter. Favorita inventory nodes, inventory, capacity,
transport, shortage, and reconfiguration quantities remain calibrated rather
than observed.
"""
    (ROOT / "docs/twenty_state_structural_pilot_report.md").write_text(
        report, encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main", required=True, type=Path)
    parser.add_argument("--sensitivity", required=True, type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts/decision_sensitivity_pilot",
    )
    args = parser.parse_args()
    print(json.dumps(analyze(args.main, args.sensitivity, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
