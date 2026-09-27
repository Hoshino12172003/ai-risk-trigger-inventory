"""Run preregistered interpretable conditional quantile models when gated in."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_risk_trigger_inventory.calibration.causal_baselines import (
    add_causal_baselines,
    add_event_proxies,
    add_uplift,
    assign_chronological_split,
    build_complete_weekly_panel,
)
from ai_risk_trigger_inventory.calibration.ml_gate import (
    ML_VALUE_THRESHOLDS,
    conditional_ml_is_authorized,
)


PRIMARY_PROXIES = {"P1_PROMOTION", "P2_HOLIDAY_EVENT", "P3_PROMOTION_HOLIDAY_OVERLAP"}
QUANTILES = (0.75, 0.90, 0.95)
RANDOM_SEED = 20260927


def _prepare(data_path: Path, selected_baseline: str, promo_thresholds: tuple[float, float]) -> pd.DataFrame:
    usecols = [
        "date", "store_nbr", "family", "sales", "onpromotion", "state",
        "holiday_any", "holiday_event_count",
    ]
    daily = pd.read_csv(data_path, usecols=usecols, parse_dates=["date"])
    weekly = build_complete_weekly_panel(daily)
    del daily
    weekly = add_causal_baselines(weekly)
    weekly = assign_chronological_split(weekly)
    weekly, _ = add_event_proxies(weekly, promo_thresholds)
    weekly = add_uplift(weekly, selected_baseline)
    weekly["holiday_status"] = np.where(weekly["holiday_any"], "HOLIDAY", "NO_HOLIDAY")
    group = weekly.groupby(["store_nbr", "family"], sort=False, observed=True)["actual_demand"]
    weekly["lag_1_demand"] = group.shift(1)
    weekly["lag_4_demand"] = group.shift(4)
    iso_week = weekly["week_start"].dt.isocalendar().week.astype(float)
    weekly["week_sin"] = np.sin(2 * np.pi * iso_week / 52.0)
    weekly["week_cos"] = np.cos(2 * np.pi * iso_week / 52.0)
    weekly["month_sin"] = np.sin(2 * np.pi * weekly["week_start"].dt.month / 12.0)
    weekly["month_cos"] = np.cos(2 * np.pi * weekly["week_start"].dt.month / 12.0)
    weekly["log_baseline"] = np.log1p(weekly["baseline_demand"])
    weekly["log_lag_1"] = np.log1p(weekly["lag_1_demand"])
    weekly["log_lag_4"] = np.log1p(weekly["lag_4_demand"])
    weekly["log_promotion_intensity"] = np.log1p(weekly["promotion_intensity"])
    weekly["log_holiday_event_count"] = np.log1p(weekly["holiday_event_count"])
    weekly["store_id"] = weekly["store_nbr"].astype(str)
    required = [
        "uplift", "log_baseline", "log_lag_1", "log_lag_4",
        "B1_MEDIAN_4", "B2_MEDIAN_8", "B3_MEDIAN_12",
    ]
    mask = (
        weekly["split"].isin(["TRAIN", "VALIDATION", "TEST"])
        & weekly["event_proxy"].isin(PRIMARY_PROXIES)
        & (weekly["uplift"] > 0)
        & weekly[required].notna().all(axis=1)
    )
    return weekly.loc[mask].copy()


def _pinball(y_true, prediction, quantile: float) -> float:
    from sklearn.metrics import mean_pinball_loss
    return float(mean_pinball_loss(y_true, prediction, alpha=quantile))


def _group_median_q90_error(frame: pd.DataFrame, prediction: np.ndarray) -> tuple[float, int]:
    evaluated = frame[["family", "state"]].copy()
    evaluated["covered"] = frame["uplift"].to_numpy() <= prediction
    errors = []
    for column in ("family", "state"):
        for _, group in evaluated.groupby(column, sort=True, observed=True):
            if len(group) >= 30:
                errors.append(abs(float(group["covered"].mean()) - 0.90))
    return float(np.median(errors)), len(errors)


def _baseline_metrics(test: pd.DataFrame, empirical: pd.DataFrame) -> dict[str, float]:
    global_row = empirical[
        (empirical["population"] == "POSITIVE_UPLIFT_EVENT_PROXY_P1_P2_P3")
        & (empirical["group_dimension"] == "overall")
    ].iloc[0]
    q90 = float(global_row["q90"])
    q95 = float(global_row["q95"])
    q90_coverage = float(np.mean(test["uplift"].to_numpy() <= q90))
    q95_coverage = float(np.mean(test["uplift"].to_numpy() <= q95))
    median_group, groups = _group_median_q90_error(test, np.full(len(test), q90))
    return {
        "q90_coverage": q90_coverage,
        "q90_absolute_error": abs(q90_coverage - 0.90),
        "q95_coverage": q95_coverage,
        "q95_absolute_error": abs(q95_coverage - 0.95),
        "median_group_q90_absolute_error": median_group,
        "eligible_groups": groups,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args()
    output = ROOT / "artifacts" / "risk_impact_calibration_stage3"
    gate_path = output / "ml_necessity_gate.json"
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    if not conditional_ml_is_authorized(gate["classification"]):
        raise SystemExit("Stage 3B not executed: frozen ML necessity gate did not trigger")
    audit_path = output / "execution_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    thresholds = audit["promotion_intensity_train_tertiles"]
    frame = _prepare(
        args.data_dir / "favorita_store_family_day.csv.gz",
        audit["selected_baseline"],
        (float(thresholds["lower"]), float(thresholds["upper"])),
    )
    train = frame[frame["split"] == "TRAIN"].copy()
    validation = frame[frame["split"] == "VALIDATION"].copy()
    test = frame[frame["split"] == "TEST"].copy()
    if min(len(train), len(validation), len(test)) < 30:
        raise SystemExit("STOP: insufficient Stage-3B split sample")

    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.linear_model import QuantileRegressor
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

    numeric = [
        "log_baseline", "log_lag_1", "log_lag_4", "log_promotion_intensity",
        "log_holiday_event_count", "week_sin", "week_cos", "month_sin", "month_cos",
    ]
    linear_categorical = ["event_proxy", "promotion_stratum", "holiday_status"]
    tree_categorical = ["store_id", "family", "state", *linear_categorical]
    target = "uplift"
    y_train = train[target].to_numpy()
    y_validation = validation[target].to_numpy()
    y_test = test[target].to_numpy()
    predictions: dict[str, dict[float, np.ndarray]] = {"LINEAR_QUANTILE": {}, "GRADIENT_BOOSTING_QUANTILE": {}}
    validation_losses: dict[str, dict[float, float]] = {key: {} for key in predictions}
    selected_parameters: dict[str, dict[float, dict]] = {key: {} for key in predictions}

    for quantile in QUANTILES:
        linear = Pipeline([
            ("features", ColumnTransformer([
                ("numeric", StandardScaler(), numeric),
                ("categorical", OneHotEncoder(handle_unknown="ignore"), linear_categorical),
            ])),
            ("model", QuantileRegressor(quantile=quantile, alpha=0.01, solver="highs")),
        ])
        linear.fit(train[numeric + linear_categorical], y_train)
        linear_validation = linear.predict(validation[numeric + linear_categorical])
        validation_losses["LINEAR_QUANTILE"][quantile] = _pinball(y_validation, linear_validation, quantile)
        predictions["LINEAR_QUANTILE"][quantile] = linear.predict(test[numeric + linear_categorical])
        selected_parameters["LINEAR_QUANTILE"][quantile] = {
            "alpha": 0.01, "solver": "highs",
        }

        candidates = (
            {"n_estimators": 50, "learning_rate": 0.05, "max_depth": 2},
            {"n_estimators": 100, "learning_rate": 0.05, "max_depth": 2},
        )
        best = None
        for parameters in candidates:
            tree = Pipeline([
                ("features", ColumnTransformer([
                    ("numeric", "passthrough", numeric),
                    ("categorical", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1), tree_categorical),
                ])),
                ("model", GradientBoostingRegressor(
                    loss="quantile", alpha=quantile, random_state=RANDOM_SEED,
                    **parameters,
                )),
            ])
            tree.fit(train[numeric + tree_categorical], y_train)
            validation_prediction = tree.predict(validation[numeric + tree_categorical])
            loss = _pinball(y_validation, validation_prediction, quantile)
            candidate = (loss, parameters["n_estimators"], parameters, tree)
            if best is None or candidate[:2] < best[:2]:
                best = candidate
        loss, _, parameters, tree = best
        validation_losses["GRADIENT_BOOSTING_QUANTILE"][quantile] = loss
        predictions["GRADIENT_BOOSTING_QUANTILE"][quantile] = tree.predict(test[numeric + tree_categorical])
        selected_parameters["GRADIENT_BOOSTING_QUANTILE"][quantile] = parameters

    selected_family = min(
        predictions,
        key=lambda family: (
            np.mean(list(validation_losses[family].values())), family
        ),
    )
    result_rows = []
    family_summaries = {}
    for family in sorted(predictions):
        crossing = (
            (predictions[family][0.75] > predictions[family][0.90])
            | (predictions[family][0.90] > predictions[family][0.95])
        )
        for quantile in QUANTILES:
            prediction = predictions[family][quantile]
            coverage = float(np.mean(y_test <= prediction))
            median_group, eligible_groups = _group_median_q90_error(test, prediction) if quantile == 0.90 else (np.nan, 0)
            result_rows.append({
                "model": family, "quantile_level": quantile,
                "features": "|".join(numeric + (linear_categorical if family == "LINEAR_QUANTILE" else tree_categorical)),
                "feature_timing": "lags/rolling=past_only; calendar/holiday=known calendar; promotion=scheduled-known assumption; IDs=known",
                "parameters": json.dumps(selected_parameters[family][quantile], sort_keys=True),
                "train_rows": len(train), "validation_rows": len(validation), "test_rows": len(test),
                "validation_pinball_loss": validation_losses[family][quantile],
                "test_pinball_loss": _pinball(y_test, prediction, quantile),
                "test_empirical_coverage": coverage,
                "test_coverage_error": coverage - quantile,
                "median_group_absolute_q90_coverage_error": median_group,
                "eligible_q90_groups": eligible_groups,
                "quantile_crossing_count": int(crossing.sum()),
                "quantile_crossing_rate": float(crossing.mean()),
                "selected_model_family_on_validation": family == selected_family,
                "postprocessing_applied": False,
            })
        q90_prediction = predictions[family][0.90]
        q95_prediction = predictions[family][0.95]
        q90_coverage = float(np.mean(y_test <= q90_prediction))
        q95_coverage = float(np.mean(y_test <= q95_prediction))
        median_group, groups = _group_median_q90_error(test, q90_prediction)
        family_summaries[family] = {
            "q90_coverage": q90_coverage,
            "q90_absolute_error": abs(q90_coverage - 0.90),
            "q95_coverage": q95_coverage,
            "q95_absolute_error": abs(q95_coverage - 0.95),
            "median_group_q90_absolute_error": median_group,
            "eligible_groups": groups,
        }
    results = pd.DataFrame(result_rows)
    results.to_csv(output / "conditional_ml_results.csv", index=False, lineterminator="\n")

    empirical = pd.read_csv(output / "empirical_quantiles.csv")
    baseline = _baseline_metrics(test, empirical)
    selected = family_summaries[selected_family]
    overall_improvement = baseline["q90_absolute_error"] - selected["q90_absolute_error"]
    group_improvement = baseline["median_group_q90_absolute_error"] - selected["median_group_q90_absolute_error"]
    q95_worsening = selected["q95_absolute_error"] - baseline["q95_absolute_error"]
    conditions = {
        "overall_q90_improvement": overall_improvement >= ML_VALUE_THRESHOLDS["overall_q90_absolute_error_improvement"],
        "median_group_q90_improvement": group_improvement >= ML_VALUE_THRESHOLDS["median_group_q90_absolute_error_improvement"],
        "no_material_q95_degradation": q95_worsening <= ML_VALUE_THRESHOLDS["maximum_q95_absolute_error_worsening"],
    }
    value_supported = all(conditions.values())
    final_classification = (
        "STAGE_3_CONDITIONAL_ML_SUPPORTED"
        if value_supported else "STAGE_3_SIMPLE_CALIBRATION_PREFERRED"
    )
    gate.update({
        "stage3b_executed": True,
        "conditional_ml_results_file": "conditional_ml_results.csv",
        "selected_model_family_on_validation": selected_family,
        "validation_average_pinball_loss": {
            family: float(np.mean(list(losses.values())))
            for family, losses in validation_losses.items()
        },
        "ml_value_gate": {
            "classification": final_classification,
            "supported": value_supported,
            "conditions": conditions,
            "simple_global_quantile_test_metrics": baseline,
            "selected_conditional_model_test_metrics": selected,
            "overall_q90_absolute_error_improvement": overall_improvement,
            "median_group_q90_absolute_error_improvement": group_improvement,
            "q95_absolute_error_worsening": q95_worsening,
            "thresholds": ML_VALUE_THRESHOLDS,
        },
    })
    gate_path.write_text(json.dumps(gate, indent=2) + "\n", encoding="utf-8")
    audit["stage3b_executed"] = True
    audit["stage3b_train_rows"] = len(train)
    audit["stage3b_validation_rows"] = len(validation)
    audit["stage3b_test_rows"] = len(test)
    audit["selected_conditional_model"] = selected_family
    audit["final_demand_calibration_classification"] = final_classification
    audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "selected_model_family": selected_family,
        "classification": final_classification,
        "ml_value_gate": gate["ml_value_gate"],
    }, indent=2))


if __name__ == "__main__":
    main()
