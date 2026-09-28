"""Deterministic helpers for the Stage-4A M5 proxy-semantics audit."""

from __future__ import annotations

import numpy as np
import pandas as pd


PRICE_REFERENCE_WEEKS = 12
PRIMARY_ITEM_DISCOUNT = 0.10
PRIMARY_DEPARTMENT_SHARE = 0.10
MIN_VALID_PRICED_ITEMS = 10
SENSITIVITY_ITEM_DISCOUNTS = (0.05, 0.10, 0.15)
SENSITIVITY_DEPARTMENT_SHARES = (0.05, 0.10, 0.20)
SATURATION_CONDITIONAL_THRESHOLD = 0.95
NEAR_EQUAL_COUNT_TOLERANCE = 0.01
MIN_SATURATION_REDUCTION = 0.10
PAIRWISE_DISTINGUISHABILITY_JACCARD = 0.95


def add_causal_price_reference(prices: pd.DataFrame) -> pd.DataFrame:
    """Add the median of the preceding 12 available store-item price weeks."""
    frame = prices.sort_values(["store_id", "item_id", "wm_yr_wk"]).copy()
    grouped = frame.groupby(["store_id", "item_id"], sort=False, observed=True)[
        "sell_price"
    ]
    frame["price_reference_12"] = grouped.transform(
        lambda values: values.shift(1).rolling(
            PRICE_REFERENCE_WEEKS, min_periods=PRICE_REFERENCE_WEEKS
        ).median()
    )
    valid = frame["price_reference_12"].notna() & (frame["price_reference_12"] > 0)
    frame["valid_price_reference"] = valid
    frame["discount"] = np.nan
    frame.loc[valid, "discount"] = (
        frame.loc[valid, "price_reference_12"] - frame.loc[valid, "sell_price"]
    ) / frame.loc[valid, "price_reference_12"]
    return frame


def aggregate_price_promotion(
    prices: pd.DataFrame,
    *,
    item_discount: float,
    department_share: float,
    minimum_valid_items: int = MIN_VALID_PRICED_ITEMS,
) -> pd.DataFrame:
    """Aggregate a frozen item-price rule to store-department-week."""
    frame = prices.copy()
    frame["promoted_item"] = (
        frame["valid_price_reference"] & (frame["discount"] >= item_discount)
    )
    valid = frame[frame["valid_price_reference"]]
    result = valid.groupby(
        ["store_id", "dept_id", "wm_yr_wk"], sort=True, observed=True
    ).agg(
        valid_priced_item_count=("item_id", "size"),
        promoted_priced_item_count=("promoted_item", "sum"),
    ).reset_index()
    result["promotion_share"] = (
        result["promoted_priced_item_count"] / result["valid_priced_item_count"]
    )
    result["price_promotion_event"] = (
        (result["valid_priced_item_count"] >= minimum_valid_items)
        & (result["promotion_share"] >= department_share)
    )
    return result


def overlap_tables(
    frame: pd.DataFrame,
    proxy_columns: dict[str, str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return pairwise overlap, ordered conditional overlap, and exclusivity."""
    masks = {name: frame[column].astype(bool) for name, column in proxy_columns.items()}
    pairwise = []
    conditional = []
    for left_name, left in masks.items():
        for right_name, right in masks.items():
            intersection = int((left & right).sum())
            union = int((left | right).sum())
            pairwise.append({
                "proxy_a": left_name,
                "proxy_b": right_name,
                "count_a": int(left.sum()),
                "count_b": int(right.sum()),
                "intersection_count": intersection,
                "union_count": union,
                "jaccard_similarity": intersection / union if union else np.nan,
            })
            conditional.append({
                "event": left_name,
                "given": right_name,
                "intersection_count": intersection,
                "given_count": int(right.sum()),
                "conditional_probability": intersection / int(right.sum())
                if right.any() else np.nan,
            })
    atomic_names = [name for name in masks if "GENERIC" not in name and "OVERLAP" not in name]
    exclusive = []
    for name, mask in masks.items():
        other = pd.Series(False, index=frame.index)
        for other_name, other_mask in masks.items():
            if other_name != name:
                other |= other_mask
        atomic_other = pd.Series(False, index=frame.index)
        for other_name in atomic_names:
            if other_name != name:
                atomic_other |= masks[other_name]
        exclusive.append({
            "proxy_name": name,
            "sample_count": int(mask.sum()),
            "exclusive_against_all_listed": int((mask & ~other).sum()),
            "atomic_exclusive_count": int((mask & ~atomic_other).sum())
            if name in atomic_names else np.nan,
        })
    return pd.DataFrame(pairwise), pd.DataFrame(conditional), pd.DataFrame(exclusive)


def conditional_value(
    conditional: pd.DataFrame, event: str, given: str
) -> float:
    row = conditional[(conditional["event"] == event) & (conditional["given"] == given)]
    if len(row) != 1:
        raise ValueError(f"missing conditional overlap P({event}|{given})")
    return float(row.iloc[0]["conditional_probability"])
