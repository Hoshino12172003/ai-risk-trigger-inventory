"""Audit local M5 inputs and stop before calibration when critical data are missing."""

from __future__ import annotations

import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
STAGE3D_COMMIT = "0104de30da0686ee4babf7700738a3bbbec70d30"
PAPER2_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
FILES = (
    ("sales_train_validation.csv", True),
    ("sales_train_evaluation.csv", False),
    ("calendar.csv", True),
    ("sell_prices.csv", True),
)


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _directory_hashes(path: Path) -> dict[str, str]:
    return {
        str(file.relative_to(ROOT)).replace("\\", "/"): _digest(file)
        for file in sorted(path.rglob("*")) if file.is_file()
    }


def _header(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        return next(csv.reader(handle))


def _paper2_git(checkout: Path, *arguments: str) -> str:
    safe_path = str(checkout.resolve()).replace("\\", "/")
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={safe_path}", *arguments],
        cwd=checkout,
        text=True,
    ).strip()


def build_audit(data_dir: Path, paper2_checkout: Path) -> tuple[dict, list[dict]]:
    paper2_head = _paper2_git(paper2_checkout, "rev-parse", "HEAD")
    paper2_clean = not bool(_paper2_git(paper2_checkout, "status", "--porcelain"))
    if paper2_head != PAPER2_SHA or not paper2_clean:
        raise RuntimeError("STOP: frozen Paper-2 checkout changed")

    prior_dirs = {
        "stage1": ROOT / "artifacts" / "compound_risk_stage1",
        "stage2": ROOT / "artifacts" / "compound_risk_stage2",
        "stage3a": ROOT / "artifacts" / "risk_impact_calibration_stage3",
        "stage3c": ROOT / "artifacts" / "risk_impact_mapping_stage3c",
        "stage3d": ROOT / "artifacts" / "route_risk_calibration_stage3d",
    }
    prior_before = {name: _directory_hashes(path) for name, path in prior_dirs.items()}
    schema_rows = []
    file_records = []
    for filename, required in FILES:
        path = data_dir / filename
        present = path.is_file()
        columns = _header(path) if present else []
        record = {
            "filename": filename,
            "required_for_calibration": required,
            "present": present,
            "file_path": str(path.resolve()),
            "file_size_bytes": path.stat().st_size if present else None,
            "row_count": None,
            "columns": columns,
            "date_coverage_start": None,
            "date_coverage_end": None,
            "item_count": None,
            "store_count": None,
            "state_count": None,
            "category_count": None,
            "department_count": None,
            "calendar_event_fields": [],
            "snap_fields": [],
            "price_fields": [],
            "duplicate_key_check": "NOT_AUDITED_MISSING_FILE" if not present else "DEFERRED_UNTIL_ALL_CRITICAL_FILES_PRESENT",
            "missingness_summary": None,
            "status": "MISSING" if not present else "PRESENT_SCHEMA_ONLY",
        }
        file_records.append(record)
        schema_rows.append({
            **{key: value for key, value in record.items() if key not in {
                "columns", "calendar_event_fields", "snap_fields", "price_fields",
                "missingness_summary",
            }},
            "columns_json": json.dumps(record["columns"]),
            "calendar_event_fields_json": json.dumps(record["calendar_event_fields"]),
            "snap_fields_json": json.dumps(record["snap_fields"]),
            "price_fields_json": json.dumps(record["price_fields"]),
            "missingness_summary_json": json.dumps(record["missingness_summary"]),
        })

    missing_required = [row["filename"] for row in file_records if row["required_for_calibration"] and not row["present"]]
    missing_optional = [row["filename"] for row in file_records if not row["required_for_calibration"] and not row["present"]]
    prior_after = {name: _directory_hashes(path) for name, path in prior_dirs.items()}
    unchanged = {name: prior_before[name] == prior_after[name] for name in prior_dirs}
    if not all(unchanged.values()):
        raise RuntimeError("STOP: prior artifacts changed during M5 availability audit")

    status = "STAGE_4_BLOCKED_MISSING_M5_DATA" if missing_required else "M5_DATA_AVAILABLE_FOR_STAGE4"
    audit = {
        "status": status,
        "stage3d_base_commit": STAGE3D_COMMIT,
        "input_directory": str(data_dir.resolve()),
        "input_directory_present": data_dir.is_dir(),
        "required_files": [filename for filename, required in FILES if required],
        "optional_files": [filename for filename, required in FILES if not required],
        "missing_required_files": missing_required,
        "missing_optional_files": missing_optional,
        "files": file_records,
        "calibration_permitted": not missing_required,
        "calibration_started": False,
        "temporal_split_created": False,
        "test_coverage_read": False,
        "optimizer_dispatches": 0,
        "genai_dispatches": 0,
        "ml_training_dispatches": 0,
        "fabricated_or_downloaded_data": False,
        "paper2_frozen_sha_before": paper2_head,
        "paper2_frozen_sha_after": paper2_head,
        "paper2_clean_before": paper2_clean,
        "paper2_clean_after": paper2_clean,
        "prior_artifacts_unchanged": unchanged,
        "prior_artifact_hashes_before": prior_before,
        "prior_artifact_hashes_after": prior_after,
        "stop_reason": "One or more critical local M5 files are missing; Stage-4 calibration was not started." if missing_required else None,
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
    print(json.dumps({
        "status": audit["status"],
        "missing_required_files": audit["missing_required_files"],
        "missing_optional_files": audit["missing_optional_files"],
        "calibration_started": audit["calibration_started"],
    }, indent=2))


if __name__ == "__main__":
    main()
