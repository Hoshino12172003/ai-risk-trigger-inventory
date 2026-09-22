"""Build deterministic Favorita decision states without calling an optimizer."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from ai_risk_trigger_inventory.data.decision_sensitivity import (
    CAPACITY_BUFFER,
    FORBIDDEN_INPUT_FIELDS,
    INCUMBENT_COVERAGE,
    MIN_BASELINE_DEMAND,
    PRIORITY_FAMILIES,
    TARGET_STATES,
    assert_no_post_solve_leakage,
    select_families,
    select_store_contexts,
)
from ai_risk_trigger_inventory.oracle.provenance import OptimizationOracleProvenance


REQUIRED_DAILY_COLUMNS = {
    "date",
    "state",
    "store_nbr",
    "family",
    "sales",
    "onpromotion",
    "holiday_flag",
    "transactions",
}
REQUIRED_CONTEXT_COLUMNS = {
    "state",
    "network_id",
    "transport_cost_cv",
    "transport_substitutability_proxy",
}


def _paths(args: argparse.Namespace) -> tuple[Path, Path]:
    daily = args.daily_data or args.data_dir / "favorita_store_family_day.csv.gz"
    contexts = args.network_contexts or args.data_dir / "state_network_contexts.csv"
    missing = [path for path in (daily, contexts) if not path.exists()]
    if missing:
        names = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(
            f"BLOCKED: cleaned local input missing: {names}. Kaggle download is prohibited."
        )
    return daily, contexts


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{label} is missing required columns: {sorted(missing)}")


def _truthy(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin({"1", "true", "yes", "y"})


def _select_diverse_states(candidates: pd.DataFrame, limit: int) -> pd.DataFrame:
    """Deterministically cover event and shift strata before filling by time."""

    promotion_cutoff = float(candidates["promotion_intensity"].quantile(0.75))
    strata = (
        candidates["relative_demand_shift"].abs() <= 0.05,
        candidates["promotion_intensity"] >= promotion_cutoff,
        candidates["holiday_flag"].astype(bool),
        candidates["positive_demand_shift"] >= 0.30,
        candidates["positive_demand_shift"].between(0.0, 0.10, inclusive="right"),
        candidates["positive_demand_shift"].between(0.10, 0.30, inclusive="neither"),
    )
    selected: list[int] = []
    state_counts = {state: 0 for state in TARGET_STATES}
    for mask in strata:
        options = candidates.loc[mask & ~candidates.index.isin(selected)].copy()
        if options.empty:
            continue
        options["state_count"] = options["state"].map(state_counts)
        options = options.sort_values(["state_count", "week_start", "state"])
        choice = int(options.index[len(options) // 2])
        selected.append(choice)
        state_counts[str(candidates.loc[choice, "state"])] += 1
        if len(selected) == limit:
            break

    remaining = candidates.loc[~candidates.index.isin(selected)].sort_values(
        ["week_start", "state"]
    )
    needed = limit - len(selected)
    if needed > 0 and not remaining.empty:
        if needed >= len(remaining):
            selected.extend(int(index) for index in remaining.index)
        else:
            positions = [round(i * (len(remaining) - 1) / (needed - 1)) for i in range(needed)] if needed > 1 else [len(remaining) // 2]
            selected.extend(int(remaining.index[position]) for position in positions)
    return candidates.loc[selected[:limit]].sort_values(["week_start", "state"])


def build_states(daily: pd.DataFrame, contexts: pd.DataFrame, limit: int) -> pd.DataFrame:
    _require_columns(daily, REQUIRED_DAILY_COLUMNS, "daily input")
    _require_columns(contexts, REQUIRED_CONTEXT_COLUMNS, "network contexts")
    if not 5 <= limit <= 50:
        raise ValueError("state limit must be between 5 and 50")

    frame = daily.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame["state"] = frame["state"].astype(str).str.title()
    frame["family"] = frame["family"].astype(str).str.upper()
    frame["store_nbr"] = frame["store_nbr"].astype(str)
    frame["week_start"] = frame["date"] - pd.to_timedelta(frame["date"].dt.weekday, unit="D")
    frame["holiday_flag"] = _truthy(frame["holiday_flag"])
    frame = frame[
        frame["state"].isin(TARGET_STATES) & frame["family"].isin(PRIORITY_FAMILIES)
    ].copy()
    if frame.empty:
        raise ValueError("no Pichincha/Guayas priority-family observations were found")

    context_frame = contexts.copy()
    context_frame["state"] = context_frame["state"].astype(str).str.title()
    context_frame["network_id"] = context_frame["network_id"].astype(str)
    context_frame = context_frame[context_frame["state"].isin(TARGET_STATES)].copy()
    if context_frame.duplicated(["state", "network_id"]).any():
        raise ValueError("state/network_id pairs must be unique")

    selections: dict[tuple[str, str], tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for state in TARGET_STATES:
        state_frame = frame[frame["state"] == state]
        state_contexts = context_frame[context_frame["state"] == state].sort_values("network_id")
        if state_contexts.empty:
            raise ValueError(f"network contexts are missing for {state}")
        store_contexts = select_store_contexts(
            state_frame.groupby("store_nbr")["sales"].sum().to_dict(),
            len(state_contexts),
        )
        families = select_families(state_frame["family"].unique())
        for (_, context), stores in zip(
            state_contexts.iterrows(), store_contexts, strict=True
        ):
            selections[(state, str(context["network_id"]))] = stores, families

    selected_parts = []
    for (state, network_id), (stores, families) in selections.items():
        part = frame[
                (frame["state"] == state)
                & frame["store_nbr"].isin(stores)
                & frame["family"].isin(families)
            ].copy()
        part["network_id"] = network_id
        selected_parts.append(part)
    selected_daily = pd.concat(selected_parts, ignore_index=True)

    transactions = (
        selected_daily.groupby(
            ["date", "week_start", "state", "network_id", "store_nbr"],
            as_index=False,
        )["transactions"]
        .max()
        .groupby(["state", "network_id", "week_start"], as_index=False)["transactions"]
        .sum()
        .sort_values(["state", "network_id", "week_start"])
    )
    transactions["transactions_baseline"] = transactions.groupby(
        ["state", "network_id"]
    )["transactions"].transform(lambda values: values.shift(1).rolling(4, min_periods=4).mean())
    transactions["transactions_change"] = (
        transactions["transactions"] - transactions["transactions_baseline"]
    ) / transactions["transactions_baseline"].where(transactions["transactions_baseline"] >= 1.0)

    weekly = (
        selected_daily.groupby(
            ["state", "network_id", "week_start", "store_nbr", "family"],
            as_index=False,
        )
        .agg(
            current_demand=("sales", "sum"),
            promoted_items=("onpromotion", "sum"),
            holiday_flag=("holiday_flag", "max"),
        )
        .sort_values(["state", "network_id", "store_nbr", "family", "week_start"])
    )
    group = weekly.groupby(
        ["state", "network_id", "store_nbr", "family"]
    )["current_demand"]
    weekly["baseline_demand"] = group.transform(
        lambda values: values.shift(1).rolling(4, min_periods=4).mean()
    )
    weekly["history_peak_8"] = group.transform(
        lambda values: values.shift(1).rolling(8, min_periods=4).max()
    )
    weekly["incumbent_inventory"] = INCUMBENT_COVERAGE * weekly["baseline_demand"]
    weekly["capacity"] = pd.concat(
        [
            weekly["incumbent_inventory"],
            CAPACITY_BUFFER * weekly["history_peak_8"],
        ],
        axis=1,
    ).max(axis=1)

    context_lookup = context_frame.set_index(["state", "network_id"])

    transaction_lookup = transactions.set_index(["state", "network_id", "week_start"])
    records: list[dict[str, object]] = []
    for (state, network_id, week_start), cells in weekly.groupby(
        ["state", "network_id", "week_start"]
    ):
        stores, families = selections[(str(state), str(network_id))]
        expected_cells = len(stores) * len(families)
        if len(cells) != expected_cells:
            continue
        if cells["baseline_demand"].isna().any() or (
            cells["baseline_demand"] < MIN_BASELINE_DEMAND
        ).any():
            continue
        if (state, network_id, week_start) not in transaction_lookup.index:
            continue
        transaction_row = transaction_lookup.loc[(state, network_id, week_start)]
        if pd.isna(transaction_row["transactions_change"]):
            continue
        baseline = float(cells["baseline_demand"].sum())
        current = float(cells["current_demand"].sum())
        relative = (current - baseline) / baseline
        incumbent = cells["incumbent_inventory"].astype(float)
        capacity = cells["capacity"].astype(float)
        context = context_lookup.loc[(state, network_id)]
        safe_network_id = str(network_id).lower().replace(" ", "-")
        records.append(
            {
                "state_id": f"{state.lower()}-{safe_network_id}-{pd.Timestamp(week_start):%Y%m%d}",
                "state": state,
                "week_start": pd.Timestamp(week_start).date().isoformat(),
                "network_id": str(network_id),
                "selected_stores": "|".join(stores),
                "selected_families": "|".join(families),
                "baseline_demand": baseline,
                "current_demand": current,
                "absolute_demand_shift": current - baseline,
                "relative_demand_shift": relative,
                "positive_demand_shift": max(relative, 0.0),
                "negative_demand_shift": min(relative, 0.0),
                "promotion_intensity": float(cells["promoted_items"].sum()) / max(current, 1.0),
                "holiday_flag": bool(cells["holiday_flag"].max()),
                "transactions_change": float(transaction_row["transactions_change"]),
                "inventory_to_demand_ratio": float(incumbent.sum()) / max(current, 1.0),
                "capacity_slack_ratio": float(capacity.sum() - current) / float(capacity.sum()),
                "demand_concentration": float(((cells["current_demand"] / max(current, 1.0)) ** 2).sum()),
                "incumbent_inventory_concentration": float(((incumbent / incumbent.sum()) ** 2).sum()),
                "transport_cost_cv": float(context["transport_cost_cv"]),
                "transport_substitutability_proxy": float(context["transport_substitutability_proxy"]),
                "incumbent_inventory_source": "CALIBRATED",
                "oracle_repository": OptimizationOracleProvenance().repository,
                "oracle_commit": OptimizationOracleProvenance().commit_sha,
            }
        )

    candidates = pd.DataFrame(records)
    if len(candidates) < limit:
        raise ValueError(f"BLOCKED: only {len(candidates)} valid states; {limit} required")
    assert_no_post_solve_leakage(candidates.columns)
    if FORBIDDEN_INPUT_FIELDS & set(candidates.columns):
        raise AssertionError("post-solve leakage detected")
    return _select_diverse_states(candidates, limit).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--daily-data", type=Path)
    parser.add_argument("--network-contexts", type=Path)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/decision_sensitivity_pilot/decision_states.csv"),
    )
    args = parser.parse_args()
    try:
        daily_path, context_path = _paths(args)
        states = build_states(pd.read_csv(daily_path), pd.read_csv(context_path), args.limit)
    except (FileNotFoundError, ValueError) as error:
        print(str(error))
        raise SystemExit(2) from error
    args.output.parent.mkdir(parents=True, exist_ok=True)
    states.to_csv(args.output, index=False)
    print(f"PASS: wrote {len(states)} solver-free decision states to {args.output}")


if __name__ == "__main__":
    main()
