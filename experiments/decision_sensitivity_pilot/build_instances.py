"""Build five deterministic Favorita dry-run states without calling a solver."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd

from ai_risk_trigger_inventory.data.decision_sensitivity import (
    FORBIDDEN_INPUT_FIELDS,
    INCUMBENT_COVERAGE,
    MIN_BASELINE_DEMAND,
    PRIORITY_FAMILIES,
    TARGET_STATES,
    assert_no_post_solve_leakage,
    select_families,
    select_store_contexts,
)
from ai_risk_trigger_inventory.oracle.budget_inventory_adapter import (
    OraclePayload,
    assert_comparable_payloads,
    make_comparable_payloads,
)
from ai_risk_trigger_inventory.oracle.provenance import OptimizationOracleProvenance


REQUIRED_DAILY_COLUMNS = {
    "date",
    "state",
    "store_nbr",
    "family",
    "sales",
    "onpromotion",
    "holiday_any",
    "transactions_clean",
    "city",
    "cluster",
}
CONTEXTS_PER_STATE = 2
FAMILIES_PER_INSTANCE = 3
CAPACITY_BUFFER = 1.20
DEMAND_DEVIATION_FLOOR = 0.10
SERVICE_LEVEL = 0.95
LAMBDA_R = 0.05
BUDGET_MULTIPLIER = 2.0


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{label} is missing required columns: {sorted(missing)}")


def _load_daily(path: Path) -> pd.DataFrame:
    parts = []
    for chunk in pd.read_csv(path, usecols=sorted(REQUIRED_DAILY_COLUMNS), chunksize=100_000):
        chunk["state"] = chunk["state"].astype(str).str.title()
        chunk["family"] = chunk["family"].astype(str).str.upper()
        filtered = chunk[
            chunk["state"].isin(TARGET_STATES)
            & chunk["family"].isin(PRIORITY_FAMILIES)
        ]
        if not filtered.empty:
            parts.append(filtered)
    if not parts:
        raise ValueError("no pilot-state priority-family observations found")
    frame = pd.concat(parts, ignore_index=True)
    _require_columns(frame, REQUIRED_DAILY_COLUMNS, "daily input")
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame["store_nbr"] = frame["store_nbr"].astype(str)
    frame["week_start"] = frame["date"] - pd.to_timedelta(
        frame["date"].dt.weekday, unit="D"
    )
    return frame


def _select_diverse_states(candidates: pd.DataFrame, limit: int) -> pd.DataFrame:
    if limit == 20:
        selected: list[int] = []
        per_state = limit // len(TARGET_STATES)
        for state in TARGET_STATES:
            pool = candidates[candidates["state"] == state].copy()
            promotion_high = float(pool["promotion_intensity"].quantile(0.75))
            promotion_normal = float(pool["promotion_intensity"].quantile(0.50))
            strata = (
                pool["relative_demand_shift"] < -0.05,
                pool["relative_demand_shift"].abs() <= 0.05,
                pool["relative_demand_shift"] > 0.05,
                pool["promotion_intensity"] >= promotion_high,
                pool["holiday_flag"].astype(bool),
                (~pool["holiday_flag"].astype(bool))
                & (pool["promotion_intensity"] <= promotion_normal),
            )
            state_selected: list[int] = []
            for mask in strata:
                options = pool.loc[mask & ~pool.index.isin(state_selected)].sort_values(
                    ["week_start", "network_id"]
                )
                if options.empty:
                    continue
                state_selected.append(int(options.index[len(options) // 2]))
            remaining = pool.loc[~pool.index.isin(state_selected)].sort_values(
                ["week_start", "network_id"]
            )
            needed = per_state - len(state_selected)
            if needed > 0:
                positions = [
                    round(i * (len(remaining) - 1) / (needed - 1))
                    for i in range(needed)
                ] if needed > 1 else [len(remaining) // 2]
                state_selected.extend(
                    int(remaining.index[position]) for position in positions
                )
            selected.extend(state_selected[:per_state])
        result = candidates.loc[selected].sort_values(
            ["week_start", "state", "network_id"]
        )
        if len(result) != limit or result["state"].value_counts().min() != per_state:
            raise ValueError("unable to select a balanced deterministic 20-state pilot")
        coverage = {
            "positive": (result["relative_demand_shift"] > 0.05).any(),
            "negative": (result["relative_demand_shift"] < -0.05).any(),
            "near_zero": (result["relative_demand_shift"].abs() <= 0.05).any(),
            "holiday": result["holiday_flag"].astype(bool).any(),
            "normal": (
                ~result["holiday_flag"].astype(bool)
                & (result["promotion_intensity"] <= result["promotion_intensity"].median())
            ).any(),
            "promotion_heavy": (
                result["promotion_intensity"]
                >= candidates["promotion_intensity"].quantile(0.75)
            ).any(),
            "multiple_years": pd.to_datetime(result["week_start"]).dt.year.nunique() >= 4,
            "two_family_sets": result["selected_families"].nunique() >= 2,
        }
        if not all(coverage.values()):
            missing = [name for name, passed in coverage.items() if not passed]
            raise ValueError(f"20-state coverage gate failed: {missing}")
        return result

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
        options = options.sort_values(["state_count", "week_start", "state", "network_id"])
        choice = int(options.index[len(options) // 2])
        selected.append(choice)
        state_counts[str(candidates.loc[choice, "state"])] += 1
        if len(selected) == limit:
            break
    remaining = candidates.loc[~candidates.index.isin(selected)].sort_values(
        ["week_start", "state", "network_id"]
    )
    needed = limit - len(selected)
    if needed > 0:
        positions = (
            [round(i * (len(remaining) - 1) / (needed - 1)) for i in range(needed)]
            if needed > 1
            else [len(remaining) // 2]
        )
        selected.extend(int(remaining.index[position]) for position in positions)
    return candidates.loc[selected[:limit]].sort_values(
        ["week_start", "state", "network_id"]
    )


def _transport_costs(
    store_metadata: pd.DataFrame,
    stores: tuple[str, ...],
    families: tuple[str, ...],
) -> tuple[list[str], list[list[list[float]]]]:
    metadata = store_metadata.set_index("store_nbr")
    anchors = (stores[0], stores[-1])
    inventory_nodes = [f"calibrated_inventory_node_{store}" for store in anchors]
    costs = []
    for anchor in anchors:
        anchor_city = str(metadata.loc[anchor, "city"])
        anchor_cluster = float(metadata.loc[anchor, "cluster"])
        node_costs = []
        for store in stores:
            city_penalty = 0.25 if str(metadata.loc[store, "city"]) != anchor_city else 0.0
            cluster_penalty = 0.05 * abs(
                float(metadata.loc[store, "cluster"]) - anchor_cluster
            )
            base_cost = 1.0 + city_penalty + cluster_penalty
            node_costs.append([base_cost] * len(families))
        costs.append(node_costs)
    return inventory_nodes, costs


def _payload_for_cells(
    state: str,
    network_id: str,
    week_start: pd.Timestamp,
    stores: tuple[str, ...],
    families: tuple[str, ...],
    cells: pd.DataFrame,
    metadata: pd.DataFrame,
) -> tuple[OraclePayload, dict[str, float]]:
    indexed = cells.set_index(["store_nbr", "family"])
    base_demand = [
        [float(indexed.loc[(store, family), "current_demand"]) for family in families]
        for store in stores
    ]
    baseline = [
        [float(indexed.loc[(store, family), "baseline_demand"]) for family in families]
        for store in stores
    ]
    deviations = [
        [
            max(
                abs(base_demand[r][j] - baseline[r][j]),
                DEMAND_DEVIATION_FLOOR * baseline[r][j],
            )
            for j in range(len(families))
        ]
        for r in range(len(stores))
    ]
    inventory_nodes, transport_cost = _transport_costs(metadata, stores, families)
    assignments = [
        min(
            range(len(inventory_nodes)),
            key=lambda i: (transport_cost[i][r][0], i),
        )
        for r in range(len(stores))
    ]
    x0 = [[0.0] * len(families) for _ in inventory_nodes]
    peak_inventory = [[0.0] * len(families) for _ in inventory_nodes]
    for r in range(len(stores)):
        node = assignments[r]
        for j, family in enumerate(families):
            x0[node][j] += INCUMBENT_COVERAGE * baseline[r][j]
            peak_inventory[node][j] += float(
                indexed.loc[(stores[r], family), "history_peak_8"]
            )
    capacity = [
        max(sum(x0[i]), CAPACITY_BUFFER * sum(peak_inventory[i]))
        for i in range(len(inventory_nodes))
    ]
    inventory_upper_bound = [
        [capacity[i]] * len(families) for i in range(len(inventory_nodes))
    ]
    total_current = sum(map(sum, base_demand))
    fixed_depot_cost = [0.05 * total_current / len(inventory_nodes)] * len(
        inventory_nodes
    )
    inventory_cost = [[1.0] * len(families) for _ in inventory_nodes]
    shortage_penalty = [
        [
            10.0
            * max(
                transport_cost[i][r][j] for i in range(len(inventory_nodes))
            )
            for j in range(len(families))
        ]
        for r in range(len(stores))
    ]
    service_penalty = [
        20.0 * sum(base_demand[r][j] for r in range(len(stores)))
        for j in range(len(families))
    ]
    y0 = [1] * len(inventory_nodes)
    keep_first_stage = sum(fixed_depot_cost) + sum(map(sum, x0))
    budget = BUDGET_MULTIPLIER * keep_first_stage
    instance = {
        "name": f"favorita-{state.lower()}-{network_id}-{week_start:%Y%m%d}",
        "depot_ids": inventory_nodes,
        "region_ids": [f"demand_region_store_{store}" for store in stores],
        "product_ids": list(families),
        "base_demand": base_demand,
        "demand_deviation": deviations,
        "transport_cost": transport_cost,
        "shortage_penalty": shortage_penalty,
        "service_level": [SERVICE_LEVEL] * len(families),
        "service_penalty": service_penalty,
        "capacity": capacity,
        "inventory_upper_bound": inventory_upper_bound,
        "fixed_depot_cost": fixed_depot_cost,
        "inventory_cost": inventory_cost,
        "product_volume": [1.0] * len(families),
        "initial_inventory": x0,
        "reconfiguration_cost_multiplier": None,
        "provenance": {
            "data_source": "Favorita / Kaggle Store Sales, cleaned externally",
            "inventory_nodes": "CALIBRATED_NOT_OBSERVED",
            "state": state,
            "week_start": week_start.date().isoformat(),
        },
    }
    payload = OraclePayload(
        decision_mode="KEEP",
        instance=instance,
        x0=x0,
        y0=y0,
        budget=budget,
        gamma=min(2, len(stores)),
        lambda_r=LAMBDA_R,
        active_network_id=network_id,
        evaluation_horizon=(
            f"{week_start.date().isoformat()}/"
            f"{(week_start + pd.Timedelta(days=6)).date().isoformat()}"
        ),
    )
    flat_costs = np.array(transport_cost, dtype=float).ravel()
    best_costs = []
    second_costs = []
    for r in range(len(stores)):
        ordered = sorted(
            transport_cost[i][r][0] for i in range(len(inventory_nodes))
        )
        best_costs.append(ordered[0])
        second_costs.append(ordered[1])
    metrics = {
        "inventory_to_demand_ratio": sum(map(sum, x0)) / max(total_current, 1.0),
        "capacity_slack_ratio": (sum(capacity) - total_current) / sum(capacity),
        "transport_cost_cv": float(flat_costs.std() / flat_costs.mean()),
        "transport_substitutability_proxy": float(
            np.mean(np.array(second_costs) / np.array(best_costs) <= 1.25)
        ),
    }
    return payload, metrics


def build_states(
    daily: pd.DataFrame,
    contexts: pd.DataFrame,
    candidates: pd.DataFrame,
    limit: int,
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    if limit not in (5, 20):
        raise ValueError("authorized construction size must be exactly 5 or 20 states")
    for label, frame in (("network contexts", contexts), ("oracle candidates", candidates)):
        if "state" not in frame or not set(TARGET_STATES) <= set(
            frame["state"].astype(str)
        ):
            raise ValueError(f"{label} does not cover both pilot states")

    selections: dict[tuple[str, str], tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for state in TARGET_STATES:
        state_frame = daily[daily["state"] == state]
        candidate_families = candidates.loc[
            candidates["state"].astype(str) == state, "family"
        ]
        primary_families = select_families(
            candidate_families, count=FAMILIES_PER_INSTANCE
        )
        available = {str(family).upper() for family in candidate_families}
        secondary_families = tuple(
            family
            for family in PRIORITY_FAMILIES
            if family in available and family not in primary_families
        )[:FAMILIES_PER_INSTANCE]
        if limit == 20 and len(secondary_families) < 2:
            raise ValueError("20-state pilot requires two priority-family sets")
        store_contexts = select_store_contexts(
            state_frame.groupby("store_nbr")["sales"].sum().to_dict(),
            CONTEXTS_PER_STATE,
        )
        for index, stores in enumerate(store_contexts, start=1):
            use_secondary = limit == 20 and (
                (state == TARGET_STATES[0] and index == 2)
                or (state == TARGET_STATES[1] and index == 1)
            )
            families = secondary_families if use_secondary else primary_families
            selections[(state, f"{state.lower()}-context-{index}")] = stores, families

    selected_parts = []
    for (state, network_id), (stores, families) in selections.items():
        part = daily[
            (daily["state"] == state)
            & daily["store_nbr"].isin(stores)
            & daily["family"].isin(families)
        ].copy()
        part["network_id"] = network_id
        selected_parts.append(part)
    selected_daily = pd.concat(selected_parts, ignore_index=True)
    metadata = (
        selected_daily[["store_nbr", "city", "cluster"]]
        .drop_duplicates("store_nbr")
        .copy()
    )

    transactions = (
        selected_daily.groupby(
            ["date", "week_start", "state", "network_id", "store_nbr"],
            as_index=False,
        )["transactions_clean"]
        .max()
        .groupby(["state", "network_id", "week_start"], as_index=False)[
            "transactions_clean"
        ]
        .sum()
        .sort_values(["state", "network_id", "week_start"])
    )
    transactions["transactions_baseline"] = transactions.groupby(
        ["state", "network_id"]
    )["transactions_clean"].transform(
        lambda values: values.shift(1).rolling(4, min_periods=4).mean()
    )
    transactions["transactions_change"] = (
        transactions["transactions_clean"] - transactions["transactions_baseline"]
    ) / transactions["transactions_baseline"].where(
        transactions["transactions_baseline"] >= 1.0
    )

    weekly = (
        selected_daily.groupby(
            ["state", "network_id", "week_start", "store_nbr", "family"],
            as_index=False,
        )
        .agg(
            current_demand=("sales", "sum"),
            promoted_items=("onpromotion", "sum"),
            holiday_flag=("holiday_any", "max"),
        )
        .sort_values(
            ["state", "network_id", "store_nbr", "family", "week_start"]
        )
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
    weekly["week_gap_days"] = weekly.groupby(
        ["state", "network_id", "store_nbr", "family"]
    )["week_start"].diff().dt.days
    weekly["history_weeks_contiguous"] = weekly.groupby(
        ["state", "network_id", "store_nbr", "family"]
    )["week_gap_days"].transform(
        lambda values: values.rolling(4, min_periods=4).apply(
            lambda gaps: float((gaps == 7).all()), raw=False
        )
    )

    transaction_lookup = transactions.set_index(
        ["state", "network_id", "week_start"]
    )
    provenance = OptimizationOracleProvenance()
    records: list[dict[str, object]] = []
    payload_by_state: dict[str, list[dict[str, object]]] = {}
    for (state, network_id, week_start), cells in weekly.groupby(
        ["state", "network_id", "week_start"]
    ):
        stores, families = selections[(str(state), str(network_id))]
        if len(cells) != len(stores) * len(families):
            continue
        if cells["baseline_demand"].isna().any() or (
            cells["baseline_demand"] < MIN_BASELINE_DEMAND
        ).any() or not cells["history_weeks_contiguous"].eq(1.0).all():
            continue
        lookup_key = (state, network_id, week_start)
        if lookup_key not in transaction_lookup.index:
            continue
        transaction_row = transaction_lookup.loc[lookup_key]
        if pd.isna(transaction_row["transactions_change"]):
            continue
        payload, metrics = _payload_for_cells(
            str(state),
            str(network_id),
            pd.Timestamp(week_start),
            stores,
            families,
            cells,
            metadata,
        )
        keep, reoptimize = make_comparable_payloads(payload)
        assert_comparable_payloads(keep, reoptimize)
        state_id = f"{state.lower()}-{network_id}-{pd.Timestamp(week_start):%Y%m%d}"
        payload_by_state[state_id] = [asdict(keep), asdict(reoptimize)]
        baseline = float(cells["baseline_demand"].sum())
        current = float(cells["current_demand"].sum())
        relative = (current - baseline) / baseline
        current_values = cells["current_demand"].astype(float)
        x0_values = np.array(payload.x0, dtype=float).ravel()
        records.append(
            {
                "state_id": state_id,
                "state": state,
                "week_start": pd.Timestamp(week_start).date().isoformat(),
                "network_id": network_id,
                "selected_stores": "|".join(stores),
                "selected_families": "|".join(families),
                "baseline_demand": baseline,
                "current_demand": current,
                "absolute_demand_shift": current - baseline,
                "relative_demand_shift": relative,
                "positive_demand_shift": max(relative, 0.0),
                "negative_demand_shift": min(relative, 0.0),
                "promotion_intensity": float(cells["promoted_items"].sum())
                / max(current, 1.0),
                "holiday_flag": bool(cells["holiday_flag"].max()),
                "transactions_change": float(transaction_row["transactions_change"]),
                "inventory_to_demand_ratio": metrics["inventory_to_demand_ratio"],
                "capacity_slack_ratio": metrics["capacity_slack_ratio"],
                "demand_concentration": float(
                    ((current_values / current_values.sum()) ** 2).sum()
                ),
                "incumbent_inventory_concentration": float(
                    ((x0_values / x0_values.sum()) ** 2).sum()
                ),
                "transport_cost_cv": metrics["transport_cost_cv"],
                "transport_substitutability_proxy": metrics[
                    "transport_substitutability_proxy"
                ],
                "incumbent_inventory_source": "CALIBRATED",
                "oracle_repository": provenance.repository,
                "oracle_commit": provenance.commit_sha,
            }
        )
    candidates_frame = pd.DataFrame(records)
    if len(candidates_frame) < limit:
        raise ValueError(
            f"BLOCKED: only {len(candidates_frame)} valid states; {limit} required"
        )
    assert_no_post_solve_leakage(candidates_frame.columns)
    if FORBIDDEN_INPUT_FIELDS & set(candidates_frame.columns):
        raise AssertionError("post-solve leakage detected")
    selected = _select_diverse_states(candidates_frame, limit).reset_index(drop=True)
    payloads = [
        {"state_id": state_id, "payloads": payload_by_state[state_id]}
        for state_id in selected["state_id"]
    ]
    return selected, payloads


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/local/favorita")
    )
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/decision_sensitivity_pilot/decision_states.csv"),
    )
    parser.add_argument(
        "--payload-output",
        type=Path,
        default=Path(
            "artifacts/decision_sensitivity_pilot/dry_run_oracle_payloads.json"
        ),
    )
    args = parser.parse_args()
    required = {
        "daily": args.data_dir / "favorita_store_family_day.csv.gz",
        "contexts": args.data_dir / "state_network_contexts.csv",
        "candidates": args.data_dir / "oracle_pilot_candidates.csv",
    }
    missing = [str(path) for path in required.values() if not path.is_file()]
    if missing:
        print(f"BLOCKED_MISSING_LOCAL_INPUT: {missing}")
        raise SystemExit(2)
    try:
        states, payloads = build_states(
            _load_daily(required["daily"]),
            pd.read_csv(required["contexts"]),
            pd.read_csv(required["candidates"]),
            args.limit,
        )
    except ValueError as error:
        print(f"BLOCKED: {error}")
        raise SystemExit(2) from error
    args.output.parent.mkdir(parents=True, exist_ok=True)
    states.to_csv(args.output, index=False)
    args.payload_output.write_text(
        json.dumps(payloads, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"PASS: wrote {len(states)} solver-free decision states; solver_calls=0"
    )


if __name__ == "__main__":
    main()
