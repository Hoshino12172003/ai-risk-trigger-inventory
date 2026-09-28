"""Audit real local M5 inputs before Stage-4 calibration is allowed."""

from __future__ import annotations

import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
STAGE3D_COMMIT = "0104de30da0686ee4babf7700738a3bbbec70d30"
PAPER2_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
FILES = (
    ("sales_train_validation.csv", True),
    ("sales_train_evaluation.csv", True),
    ("calendar.csv", True),
    ("sell_prices.csv", True),
    ("sample_submission.csv", False),
)
EXPECTED = {
    "calendar.csv": {
        "date", "wm_yr_wk", "weekday", "wday", "month", "year", "d",
        "event_name_1", "event_type_1", "event_name_2", "event_type_2",
        "snap_CA", "snap_TX", "snap_WI",
    },
    "sell_prices.csv": {"store_id", "item_id", "wm_yr_wk", "sell_price"},
    "sales_train_validation.csv": {"item_id", "dept_id", "cat_id", "store_id", "state_id"},
    "sales_train_evaluation.csv": {"item_id", "dept_id", "cat_id", "store_id", "state_id"},
}


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def directory_hashes(path: Path) -> dict[str, str]:
    return {
        str(file.relative_to(ROOT)).replace("\\", "/"): digest(file)
        for file in sorted(path.rglob("*")) if file.is_file()
    }


def paper2_git(checkout: Path, *arguments: str) -> str:
    safe_path = str(checkout.resolve()).replace("\\", "/")
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={safe_path}", *arguments],
        cwd=checkout, text=True,
    ).strip()


def header(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        return next(csv.reader(handle))


def audit_sales(path: Path, calendar: pd.DataFrame) -> dict[str, object]:
    columns = header(path)
    demand_columns = [column for column in columns if column.startswith("d_")]
    id_columns = [column for column in columns if not column.startswith("d_")]
    unique = {name: set() for name in ("item_id", "store_id", "state_id", "cat_id", "dept_id")}
    row_count = missing_cells = duplicate_ids = 0
    columns_with_missing: set[str] = set()
    seen_ids: set[str] = set()
    for chunk in pd.read_csv(path, chunksize=1000):
        row_count += len(chunk)
        missing = chunk.isna().sum()
        missing_cells += int(missing.sum())
        columns_with_missing.update(missing[missing > 0].index)
        ids = chunk["id"].astype(str)
        duplicate_ids += int(ids.isin(seen_ids).sum() + ids.duplicated().sum())
        seen_ids.update(ids)
        for name in unique:
            unique[name].update(chunk[name].dropna().astype(str).unique())
    dates = calendar.set_index("d").reindex(demand_columns)["date"]
    if dates.isna().any():
        raise RuntimeError(f"sales demand columns do not map to calendar: {path.name}")
    return {
        "row_count": row_count, "column_count": len(columns), "columns": columns,
        "date_coverage_start": str(dates.min().date()), "date_coverage_end": str(dates.max().date()),
        "item_count": len(unique["item_id"]), "store_count": len(unique["store_id"]),
        "state_count": len(unique["state_id"]), "category_count": len(unique["cat_id"]),
        "department_count": len(unique["dept_id"]),
        "calendar_event_fields": [], "snap_fields": [], "price_fields": [],
        "duplicate_key_check": {"key": id_columns, "duplicate_id_rows": duplicate_ids},
        "missingness_summary": {
            "missing_cells": missing_cells,
            "columns_with_missing_count": len(columns_with_missing),
            "columns_with_missing": sorted(columns_with_missing),
        },
        "demand_column_count": len(demand_columns),
    }


def audit_prices(path: Path) -> dict[str, object]:
    row_count = missing_cells = adjacent_duplicates = 0
    columns_with_missing: set[str] = set()
    items: set[str] = set()
    stores: set[str] = set()
    weeks: set[int] = set()
    prior_key: tuple[str, str, int] | None = None
    sorted_keys = True
    for chunk in pd.read_csv(path, chunksize=250000):
        row_count += len(chunk)
        missing = chunk.isna().sum()
        missing_cells += int(missing.sum())
        columns_with_missing.update(missing[missing > 0].index)
        items.update(chunk["item_id"].astype(str).unique())
        stores.update(chunk["store_id"].astype(str).unique())
        weeks.update(map(int, chunk["wm_yr_wk"].unique()))
        keys = list(zip(chunk["store_id"].astype(str), chunk["item_id"].astype(str), chunk["wm_yr_wk"].astype(int)))
        sequence = ([prior_key] if prior_key is not None else []) + keys
        adjacent_duplicates += sum(left == right for left, right in zip(sequence, sequence[1:]))
        sorted_keys &= all(left <= right for left, right in zip(sequence, sequence[1:]))
        prior_key = keys[-1]
    item_codes = {value: index for index, value in enumerate(sorted(items))}
    store_codes = {value: index for index, value in enumerate(sorted(stores))}
    week_min, week_max = min(weeks), max(weeks)
    week_span = week_max - week_min + 1
    seen = np.zeros(len(stores) * len(items) * week_span, dtype=bool)
    exact_duplicates = 0
    for chunk in pd.read_csv(path, usecols=["store_id", "item_id", "wm_yr_wk"], chunksize=250000):
        encoded = (
            chunk["store_id"].astype(str).map(store_codes).to_numpy(dtype=np.int64)
            * len(items) * week_span
            + chunk["item_id"].astype(str).map(item_codes).to_numpy(dtype=np.int64) * week_span
            + chunk["wm_yr_wk"].to_numpy(dtype=np.int64) - week_min
        )
        unique_encoded, counts = np.unique(encoded, return_counts=True)
        exact_duplicates += int((counts - 1).sum() + seen[unique_encoded].sum())
        seen[unique_encoded] = True
    return {
        "row_count": row_count, "column_count": 4,
        "columns": ["store_id", "item_id", "wm_yr_wk", "sell_price"],
        "date_coverage_start": None, "date_coverage_end": None,
        "item_count": len(items), "store_count": len(stores), "state_count": None,
        "category_count": None, "department_count": None,
        "calendar_event_fields": [], "snap_fields": [], "price_fields": ["sell_price"],
        "duplicate_key_check": {
            "key": ["store_id", "item_id", "wm_yr_wk"],
            "keys_lexically_sorted": sorted_keys,
            "adjacent_duplicate_rows": adjacent_duplicates,
            "exact_duplicate_rows": exact_duplicates,
        },
        "missingness_summary": {
            "missing_cells": missing_cells,
            "columns_with_missing": sorted(columns_with_missing),
        },
        "week_count": len(weeks), "week_min": week_min, "week_max": week_max,
    }


def audit_calendar(path: Path) -> tuple[dict[str, object], pd.DataFrame]:
    frame = pd.read_csv(path, parse_dates=["date"])
    event_fields = [name for name in frame if name.startswith("event_")]
    snap_fields = [name for name in frame if name.startswith("snap_")]
    return ({
        "row_count": len(frame), "column_count": len(frame.columns), "columns": list(frame.columns),
        "date_coverage_start": str(frame["date"].min().date()),
        "date_coverage_end": str(frame["date"].max().date()),
        "item_count": None, "store_count": None, "state_count": 3,
        "category_count": None, "department_count": None,
        "calendar_event_fields": event_fields, "snap_fields": snap_fields, "price_fields": [],
        "duplicate_key_check": {
            "d_duplicates": int(frame.duplicated("d").sum()),
            "date_duplicates": int(frame.duplicated("date").sum()),
        },
        "missingness_summary": {name: int(frame[name].isna().sum()) for name in frame.columns},
    }, frame)


def build_audit(data_dir: Path, paper2_checkout: Path) -> tuple[dict, list[dict]]:
    paper2_head = paper2_git(paper2_checkout, "rev-parse", "HEAD")
    paper2_clean = not bool(paper2_git(paper2_checkout, "status", "--porcelain"))
    if paper2_head != PAPER2_SHA or not paper2_clean:
        raise RuntimeError("STOP: frozen Paper-2 checkout changed")
    prior_dirs = {
        "stage1": ROOT / "artifacts" / "compound_risk_stage1",
        "stage2": ROOT / "artifacts" / "compound_risk_stage2",
        "stage3a": ROOT / "artifacts" / "risk_impact_calibration_stage3",
        "stage3c": ROOT / "artifacts" / "risk_impact_mapping_stage3c",
        "stage3d": ROOT / "artifacts" / "route_risk_calibration_stage3d",
    }
    prior_before = {name: directory_hashes(path) for name, path in prior_dirs.items()}
    paths = {filename: data_dir / filename for filename, _ in FILES}
    missing_required = [filename for filename, required in FILES if required and not paths[filename].is_file()]
    if missing_required:
        raise RuntimeError(f"STOP: required M5 files are missing: {missing_required}")
    calendar_metrics, calendar = audit_calendar(paths["calendar.csv"])
    metrics = {
        "calendar.csv": calendar_metrics,
        "sell_prices.csv": audit_prices(paths["sell_prices.csv"]),
        "sales_train_validation.csv": audit_sales(paths["sales_train_validation.csv"], calendar),
        "sales_train_evaluation.csv": audit_sales(paths["sales_train_evaluation.csv"], calendar),
    }
    schema_rows = []
    file_records = []
    for filename, required in FILES:
        path = paths[filename]
        columns = header(path)
        missing_columns = sorted(EXPECTED.get(filename, set()) - set(columns))
        if missing_columns:
            raise RuntimeError(f"STOP: {filename} missing required columns: {missing_columns}")
        values = metrics.get(filename, {
            "row_count": sum(1 for _ in path.open(encoding="utf-8")) - 1,
            "column_count": len(columns), "columns": columns,
            "date_coverage_start": None, "date_coverage_end": None,
            "item_count": None, "store_count": None, "state_count": None,
            "category_count": None, "department_count": None,
            "calendar_event_fields": [], "snap_fields": [], "price_fields": [],
            "duplicate_key_check": "PROVENANCE_ONLY_NOT_USED_FOR_CALIBRATION",
            "missingness_summary": None,
        })
        record = {
            "filename": filename, "required_for_calibration": required, "present": True,
            "file_path": str(path.resolve()), "file_size_bytes": path.stat().st_size,
            "sha256": digest(path), **values, "status": "PRESENT_AND_AUDITED",
        }
        file_records.append(record)
        schema_rows.append({
            "filename": filename, "required_for_calibration": required, "present": True,
            "file_path": str(path.resolve()), "file_size_bytes": record["file_size_bytes"],
            "sha256": record["sha256"], "row_count": record["row_count"],
            "column_count": record["column_count"],
            "date_coverage_start": record["date_coverage_start"],
            "date_coverage_end": record["date_coverage_end"],
            "item_count": record["item_count"], "store_count": record["store_count"],
            "state_count": record["state_count"], "category_count": record["category_count"],
            "department_count": record["department_count"],
            "duplicate_key_check_json": json.dumps(record["duplicate_key_check"], sort_keys=True),
            "missingness_summary_json": json.dumps(record["missingness_summary"], sort_keys=True),
            "columns_json": json.dumps(record["columns"]),
            "calendar_event_fields_json": json.dumps(record["calendar_event_fields"]),
            "snap_fields_json": json.dumps(record["snap_fields"]),
            "price_fields_json": json.dumps(record["price_fields"]),
            "status": record["status"],
        })
    prior_after = {name: directory_hashes(path) for name, path in prior_dirs.items()}
    unchanged = {name: prior_before[name] == prior_after[name] for name in prior_dirs}
    if not all(unchanged.values()):
        raise RuntimeError("STOP: prior artifacts changed during M5 availability audit")
    audit = {
        "status": "M5_DATA_AVAILABILITY_PASS",
        "previous_status": "STAGE_4_BLOCKED_MISSING_M5_DATA",
        "stage3d_base_commit": STAGE3D_COMMIT,
        "input_directory": str(data_dir.resolve()), "input_directory_present": True,
        "required_files": [filename for filename, required in FILES if required],
        "optional_files": [filename for filename, required in FILES if not required],
        "missing_required_files": [], "missing_optional_files": [], "files": file_records,
        "calibration_permitted": True, "calibration_started": False,
        "optimizer_dispatches": 0, "genai_dispatches": 0, "ml_training_dispatches": 0,
        "fabricated_or_downloaded_data": False,
        "paper2_frozen_sha_before": paper2_head, "paper2_frozen_sha_after": paper2_head,
        "paper2_clean_before": paper2_clean, "paper2_clean_after": paper2_clean,
        "prior_artifacts_unchanged": unchanged,
        "prior_artifact_hashes_before": prior_before,
        "prior_artifact_hashes_after": prior_after,
        "stop_reason": None,
    }
    return audit, schema_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--paper2-checkout", required=True, type=Path)
    args = parser.parse_args()
    audit, schema_rows = build_audit(args.data_dir, args.paper2_checkout)
    output = ROOT / "artifacts" / "m5_demand_calibration_stage4"
    output.mkdir(parents=True, exist_ok=True)
    (output / "data_availability_audit.json").write_text(
        json.dumps(audit, indent=2) + "\n", encoding="utf-8",
    )
    with (output / "m5_schema_audit.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(schema_rows[0]))
        writer.writeheader()
        writer.writerows(schema_rows)
    print(json.dumps({"status": audit["status"], "files": len(schema_rows)}, indent=2))


if __name__ == "__main__":
    main()
