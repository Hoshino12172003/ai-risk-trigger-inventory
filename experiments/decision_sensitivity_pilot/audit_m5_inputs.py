"""Audit local M5 inputs without downloading data or invoking an optimizer."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


FILES = (
    "sales_train_validation.csv",
    "sales_train_evaluation.csv",
    "calendar.csv",
    "sell_prices.csv",
)
SOURCE = "M5 Forecasting Accuracy, cleaned or original local input"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _basic_entry(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "filename": path.name,
            "present": False,
            "sha256": None,
            "file_size": None,
            "row_count": None,
            "columns": [],
            "source": SOURCE,
            "status": "OBSERVED_LOCAL_INPUT_MISSING",
        }
    columns = pd.read_csv(path, nrows=0).columns.tolist()
    row_count = sum(
        len(chunk)
        for chunk in pd.read_csv(path, usecols=[columns[0]], chunksize=100_000)
    )
    return {
        "filename": path.name,
        "present": True,
        "sha256": _sha256(path),
        "file_size": path.stat().st_size,
        "row_count": row_count,
        "columns": columns,
        "source": SOURCE,
        "status": "OBSERVED_LOCAL_INPUT",
    }


def _sales_audit(path: Path) -> dict[str, Any]:
    columns = pd.read_csv(path, nrows=0).columns.tolist()
    day_columns = [column for column in columns if column.startswith("d_")]
    items: set[str] = set()
    stores: set[str] = set()
    states: set[str] = set()
    ids: set[str] = set()
    duplicate_ids = 0
    missing_cells = 0
    zeros = 0
    demand_cells = 0
    for frame in pd.read_csv(path, chunksize=1_000):
        items.update(frame["item_id"].astype(str))
        stores.update(frame["store_id"].astype(str))
        states.update(frame["state_id"].astype(str))
        current_ids = frame["id"].astype(str).tolist()
        current_unique = set(current_ids)
        duplicate_ids += len(current_ids) - len(current_unique)
        duplicate_ids += sum(identifier in ids for identifier in current_unique)
        ids.update(current_ids)
        values = frame[day_columns]
        missing_cells += int(frame.isna().sum().sum())
        zeros += int((values == 0).to_numpy().sum())
        demand_cells += int(values.size)
    return {
        "date_coverage": {
            "first_day_key": day_columns[0] if day_columns else None,
            "last_day_key": day_columns[-1] if day_columns else None,
            "day_count": len(day_columns),
        },
        "number_of_items": len(items),
        "number_of_stores": len(stores),
        "number_of_states": len(states),
        "missing_cells": missing_cells,
        "duplicate_id_rows": duplicate_ids,
        "zero_sales_prevalence": zeros / demand_cells if demand_cells else None,
    }


def _calendar_audit(path: Path) -> dict[str, Any]:
    frame = pd.read_csv(path)
    dates = pd.to_datetime(frame["date"], errors="raise")
    event_fields = [column for column in frame if column.startswith("event_")]
    snap_fields = [column for column in frame if column.startswith("snap_")]
    return {
        "date_coverage": {
            "start": dates.min().date().isoformat(),
            "end": dates.max().date().isoformat(),
        },
        "missingness": {
            column: int(frame[column].isna().sum()) for column in frame.columns
        },
        "duplicate_date_rows": int(frame.duplicated(["date"]).sum()),
        "calendar_event_fields": event_fields,
        "snap_fields": snap_fields,
    }


def _price_audit(path: Path) -> dict[str, Any]:
    items: set[str] = set()
    stores: set[str] = set()
    keys: set[tuple[str, str, str]] = set()
    missing = 0
    duplicates = 0
    for frame in pd.read_csv(path, chunksize=100_000):
        items.update(frame["item_id"].astype(str))
        stores.update(frame["store_id"].astype(str))
        missing += int(frame["sell_price"].isna().sum())
        current = list(
            zip(
                frame["store_id"].astype(str),
                frame["item_id"].astype(str),
                frame["wm_yr_wk"].astype(str),
            )
        )
        current_unique = set(current)
        duplicates += len(current) - len(current_unique)
        duplicates += sum(key in keys for key in current_unique)
        keys.update(current)
    return {
        "number_of_items": len(items),
        "number_of_stores": len(stores),
        "price_missingness": missing,
        "duplicate_price_keys": duplicates,
    }


def build_manifest(data_dir: Path) -> dict[str, Any]:
    entries = [_basic_entry(data_dir / filename) for filename in FILES]
    by_name = {entry["filename"]: entry for entry in entries}
    for filename in FILES:
        path = data_dir / filename
        if not path.is_file():
            continue
        if filename.startswith("sales_train_"):
            by_name[filename]["audit"] = _sales_audit(path)
        elif filename == "calendar.csv":
            by_name[filename]["audit"] = _calendar_audit(path)
        elif filename == "sell_prices.csv":
            by_name[filename]["audit"] = _price_audit(path)
    missing = [entry["filename"] for entry in entries if not entry["present"]]
    return {
        "status": "PASS" if not missing else "BLOCKED_MISSING_LOCAL_INPUT",
        "data_directory": "data/local/m5",
        "required_files": list(FILES),
        "missing_files": missing,
        "files": entries,
        "field_provenance": {
            "OBSERVED": [
                "demand/sales", "item", "department", "category", "store",
                "state", "price", "calendar/event/SNAP fields",
            ],
            "DERIVED": [
                "lagged demand", "rolling mean", "rolling volatility",
                "promotion/price-change proxies", "seasonal features",
                "store/item concentration", "demand shifts",
            ],
            "CALIBRATED": [
                "warehouse nodes", "inventory", "capacity", "transport cost",
                "shortage penalty", "reconfiguration cost",
            ],
        },
        "optimizer_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/local/m5")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/manifests/m5_local_manifest.json"),
    )
    args = parser.parse_args()
    manifest = build_manifest(args.data_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(manifest["status"])


if __name__ == "__main__":
    main()
