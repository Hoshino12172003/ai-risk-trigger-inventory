import pandas as pd

from experiments.decision_sensitivity_pilot.audit_local_inputs import (
    canonical_json,
    validate_weekly_baselines,
)


def test_local_manifest_serialization_is_deterministic() -> None:
    first = {"files": [{"filename": "a"}], "source": "Favorita", "version": 1}
    second = {"version": 1, "source": "Favorita", "files": [{"filename": "a"}]}

    assert canonical_json(first) == canonical_json(second)
    assert canonical_json(first).endswith("\n")


def test_weekly_baseline_uses_prior_weeks_only_and_has_both_states() -> None:
    rows = []
    for state in ("Pichincha", "Guayas"):
        sales = [10.0, 20.0, 30.0, 40.0, 50.0]
        for index, current in enumerate(sales):
            history = sales[max(0, index - 4) : index]
            rows.append(
                {
                    "state": state,
                    "family": "GROCERY I",
                    "week_start": f"2020-01-{1 + 7 * index:02d}",
                    "sales": current,
                    "sales_mean_4w": sum(history) / len(history) if history else None,
                    "sales_mean_8w": sum(sales[:index]) / index if index else None,
                    "sales_mean_13w": sum(sales[:index]) / index if index else None,
                }
            )

    result = validate_weekly_baselines(pd.DataFrame(rows))

    assert result["future_leakage_detected"] is False
    assert result["pilot_states_available"] == ["Guayas", "Pichincha"]
