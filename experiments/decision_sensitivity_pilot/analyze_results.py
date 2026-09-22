"""Analyze completed decision-sensitivity oracle results with simple models."""

from __future__ import annotations

import argparse
from itertools import combinations
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ai_risk_trigger_inventory.evaluation.decision_sensitivity import (
    MATCH_ABSOLUTE_GAP_MEDIAN_MULTIPLIER,
    MATCH_DECISION_VALUE_RATIO_MIN,
    MATCH_SHIFT_TOLERANCE,
    StructuralEvidence,
    classify_structural_signal,
)
from ai_risk_trigger_inventory.oracle.decision_sensitivity import calculate_decision_value
from ai_risk_trigger_inventory.oracle.provenance import OptimizationOracleProvenance


STRUCTURAL_FEATURES = [
    "inventory_to_demand_ratio",
    "capacity_slack_ratio",
    "demand_concentration",
    "incumbent_inventory_concentration",
    "transport_cost_cv",
    "transport_substitutability_proxy",
    "promotion_intensity",
    "holiday_flag",
]
REQUIRED_ORACLE_FIELDS = {
    "state_id",
    "inventory_change_L1",
    "changed_inventory_pairs",
    "shortage_cost_keep",
    "shortage_cost_reopt",
    "transport_cost_keep",
    "transport_cost_reopt",
    "service_violation_keep",
    "service_violation_reopt",
    "total_cost_keep",
    "total_cost_reopt",
    "oracle_repository",
    "oracle_commit",
    "oracle_status",
}


def _adjusted_r2(r2: float, observations: int, predictors: int) -> float:
    if observations <= predictors + 1:
        return float("nan")
    return 1.0 - (1.0 - r2) * (observations - 1) / (observations - predictors - 1)


def _fit(frame: pd.DataFrame, name: str, features: list[str]) -> tuple[dict[str, object], pd.DataFrame]:
    clean = frame.dropna(subset=features + ["decision_value"])
    feature_matrix = clean[features].to_numpy(dtype=float)
    outcome = clean["decision_value"].to_numpy(dtype=float)
    design = np.column_stack([np.ones(len(clean)), feature_matrix])
    fitted, *_ = np.linalg.lstsq(design, outcome, rcond=None)
    coefficients_array = fitted[1:]
    predicted = design @ fitted
    residual_sum = float(np.square(outcome - predicted).sum())
    total_sum = float(np.square(outcome - outcome.mean()).sum())
    r2 = 1.0 - residual_sum / total_sum if total_sum > 0 else 0.0
    row = {
        "model": name,
        "observations": len(clean),
        "features": "|".join(features),
        "r2": r2,
        "adjusted_r2": _adjusted_r2(r2, len(clean), len(features)),
        "mae": float(np.abs(outcome - predicted).mean()),
        "pearson_correlation": float("nan"),
        "spearman_correlation": float("nan"),
    }
    coefficients = pd.DataFrame(
        {
            "model": name,
            "feature": features,
            "coefficient": coefficients_array,
            "standardized_coefficient": [
                coefficient * clean[feature].std(ddof=0) / clean["decision_value"].std(ddof=0)
                if clean["decision_value"].std(ddof=0) > 0
                else float("nan")
                for feature, coefficient in zip(features, coefficients_array, strict=True)
            ],
        }
    )
    return row, coefficients


def _matched_pairs(frame: pd.DataFrame) -> pd.DataFrame:
    positive_values = frame.loc[frame["decision_value"] > 0, "decision_value"]
    gap_threshold = max(
        1e-9,
        MATCH_ABSOLUTE_GAP_MEDIAN_MULTIPLIER * float(positive_values.median()),
    ) if not positive_values.empty else 1e-9
    rows = []
    for left_index, right_index in combinations(frame.index, 2):
        left = frame.loc[left_index]
        right = frame.loc[right_index]
        shift_gap = abs(left["relative_demand_shift"] - right["relative_demand_shift"])
        if shift_gap > MATCH_SHIFT_TOLERANCE:
            continue
        low = min(left["decision_value"], right["decision_value"])
        high = max(left["decision_value"], right["decision_value"])
        ratio = high / low if low > 0 else (float("inf") if high > 0 else 1.0)
        absolute_gap = high - low
        if ratio < MATCH_DECISION_VALUE_RATIO_MIN and absolute_gap < gap_threshold:
            continue
        rows.append(
            {
                "left_state_id": left["state_id"],
                "right_state_id": right["state_id"],
                "left_state": left["state"],
                "right_state": right["state"],
                "left_shift": left["relative_demand_shift"],
                "right_shift": right["relative_demand_shift"],
                "shift_gap": shift_gap,
                "left_decision_value": left["decision_value"],
                "right_decision_value": right["decision_value"],
                "decision_value_ratio": ratio,
                "decision_value_absolute_gap": absolute_gap,
                "absolute_gap_threshold": gap_threshold,
            }
        )
    return pd.DataFrame(rows)


def _scatter(frame: pd.DataFrame, x: str, title: str, path: Path) -> None:
    fig, axis = plt.subplots(figsize=(7, 5))
    for state, group in frame.groupby("state"):
        axis.scatter(group[x], group["decision_value"], label=state, alpha=0.8)
    axis.set(xlabel=x.replace("_", " "), ylabel="decision value", title=title)
    axis.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _grouped_scatter(frame: pd.DataFrame, grouping: str, title: str, path: Path) -> None:
    bin_count = min(3, len(frame))
    labels = ["low", "medium", "high"][:bin_count]
    groups = pd.qcut(frame[grouping].rank(method="first"), q=bin_count, labels=labels)
    fig, axis = plt.subplots(figsize=(7, 5))
    for label in groups.dropna().unique():
        subset = frame[groups == label]
        axis.scatter(
            subset["relative_demand_shift"],
            subset["decision_value"],
            label=f"{grouping}: {label}",
            alpha=0.8,
        )
    axis.set(xlabel="relative demand shift", ylabel="decision value", title=title)
    axis.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def analyze(states_path: Path, results_path: Path, output_dir: Path) -> str:
    states = pd.read_csv(states_path)
    results = pd.read_csv(results_path)
    if states.empty or results.empty:
        raise ValueError("BLOCKED: real decision states and oracle results are required")
    if len(states) < 20:
        raise ValueError("BLOCKED: analysis requires at least 20 completed states")
    missing = REQUIRED_ORACLE_FIELDS - set(results.columns)
    if missing:
        raise ValueError(f"oracle results missing required fields: {sorted(missing)}")
    provenance = OptimizationOracleProvenance()
    if not (
        results["oracle_repository"].eq(provenance.repository).all()
        and results["oracle_commit"].eq(provenance.commit_sha).all()
    ):
        raise ValueError("oracle result provenance does not match the frozen commit")
    if not results["oracle_status"].eq("OPTIMAL").all():
        raise ValueError("all oracle states must be OPTIMAL before analysis")
    calculated_values = np.array([
        calculate_decision_value(keep, reopt)
        for keep, reopt in zip(
            results["total_cost_keep"], results["total_cost_reopt"], strict=True
        )
    ])
    if "decision_value" in results and results["decision_value"].notna().any():
        supplied = results["decision_value"].to_numpy(dtype=float)
        if not np.allclose(supplied, calculated_values, rtol=1e-9, atol=1e-9):
            raise ValueError("supplied decision values do not equal C_keep - C_reopt")
    if (calculated_values < -1e-8).any():
        raise ValueError("negative decision value indicates an oracle evaluation inconsistency")
    results["decision_value"] = calculated_values
    frame = states.merge(results, on="state_id", validate="one_to_one")
    if len(frame) != len(states):
        raise ValueError("every decision state must have exactly one oracle result")

    model_a, coefficients_a = _fit(frame, "A_relative_shift", ["relative_demand_shift"])
    model_b, coefficients_b = _fit(frame, "B_positive_shift", ["positive_demand_shift"])
    model_c, coefficients_c = _fit(
        frame,
        "C_demand_plus_structure",
        ["relative_demand_shift", *STRUCTURAL_FEATURES],
    )
    pearson = float(frame["relative_demand_shift"].corr(frame["decision_value"], method="pearson"))
    spearman = float(frame["relative_demand_shift"].corr(frame["decision_value"], method="spearman"))
    model_a["pearson_correlation"] = pearson
    model_a["spearman_correlation"] = spearman
    models = pd.DataFrame([model_a, model_b, model_c])
    pairs = _matched_pairs(frame)

    output_dir.mkdir(parents=True, exist_ok=True)
    figure_dir = output_dir / "figures"
    figure_dir.mkdir(exist_ok=True)
    models.to_csv(output_dir / "sensitivity_models.csv", index=False)
    pairs.to_csv(output_dir / "matched_state_pairs.csv", index=False)
    pd.concat([coefficients_a, coefficients_b, coefficients_c]).to_csv(
        output_dir / "feature_summary.csv", index=False
    )
    _scatter(frame, "relative_demand_shift", "Relative demand shift vs decision value", figure_dir / "relative_shift_vs_decision_value.png")
    _scatter(frame, "positive_demand_shift", "Positive demand shift vs decision value", figure_dir / "positive_shift_vs_decision_value.png")
    _scatter(frame, "capacity_slack_ratio", "Capacity slack vs decision value", figure_dir / "capacity_slack_vs_decision_value.png")
    _grouped_scatter(frame, "capacity_slack_ratio", "Demand shift by capacity slack", figure_dir / "shift_by_capacity_slack.png")
    _grouped_scatter(frame, "inventory_to_demand_ratio", "Demand shift by inventory coverage", figure_dir / "shift_by_inventory_coverage.png")

    adjusted_gain = float(model_c["adjusted_r2"] - max(model_a["adjusted_r2"], model_b["adjusted_r2"]))
    evidence = StructuralEvidence(
        executed_states=len(frame),
        demand_only_r2=max(float(model_a["r2"]), float(model_b["r2"])),
        adjusted_r2_gain=adjusted_gain,
        matched_pairs=len(pairs),
        represented_states=frame["state"].nunique(),
        represented_family_sets=frame["selected_families"].nunique(),
    )
    classification = classify_structural_signal(evidence)
    high_value_cutoff = float(frame["decision_value"].quantile(0.75))
    low_value_cutoff = float(frame["decision_value"].quantile(0.25))
    large_shift_cutoff = float(frame["positive_demand_shift"].quantile(0.75))
    small_shift_high_value = frame[
        (frame["relative_demand_shift"].abs() <= MATCH_SHIFT_TOLERANCE)
        & (frame["decision_value"] >= high_value_cutoff)
    ]["state_id"].tolist()
    large_shift_low_value = frame[
        (frame["positive_demand_shift"] >= large_shift_cutoff)
        & (frame["decision_value"] <= low_value_cutoff)
    ]["state_id"].tolist()
    top_structure = (
        coefficients_c.assign(
            absolute_standardized=coefficients_c["standardized_coefficient"].abs()
        )
        .sort_values("absolute_standardized", ascending=False)["feature"]
        .head(3)
        .tolist()
    )
    proceed = (
        "yes, as a carefully scoped follow-up"
        if classification != "WEAK_OR_NO_STRUCTURAL_SIGNAL"
        else "not on structural-signal grounds without additional evidence"
    )
    report = f"""# Decision-Sensitivity Feasibility Report

Status: exploratory pilot, not a formal experiment.

- Executed states: {len(frame)}
- Demand-only best R2: {evidence.demand_only_r2:.4f}
- Adjusted R2 gain from structure: {adjusted_gain:.4f}
- Matched state pairs: {len(pairs)}
- Pearson correlation: {pearson:.4f}
- Spearman correlation: {spearman:.4f}
- Final classification: `{classification}`

The classification uses preregistered thresholds in
`src/ai_risk_trigger_inventory/evaluation/decision_sensitivity.py`.
It is feasibility evidence only and does not authorize a formal experiment.

## Required questions

1. Demand magnitude alone explains a best in-sample R2 of {evidence.demand_only_r2:.4f}.
2. Adding registered structure changes adjusted R2 by {adjusted_gain:.4f}.
3. Small-shift/high-value state IDs: {small_shift_high_value or "none under the registered rule"}.
4. Large-shift/low-value state IDs: {large_shift_low_value or "none under the registered rule"}.
5. Largest absolute standardized structural coefficients: {top_structure}.
6. Represented states: {sorted(frame["state"].unique())}; this does not by itself prove equal stability.
7. Proceed to a formal study: {proceed}. A separate authorization is still required.
"""
    (output_dir / "decision_sensitivity_report.md").write_text(report, encoding="utf-8")
    return classification


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--states", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/decision_sensitivity_pilot"),
    )
    args = parser.parse_args()
    try:
        classification = analyze(args.states, args.results, args.output_dir)
    except (FileNotFoundError, KeyError, ValueError) as error:
        print(f"BLOCKED: {error}")
        raise SystemExit(2) from error
    print(classification)


if __name__ == "__main__":
    main()
