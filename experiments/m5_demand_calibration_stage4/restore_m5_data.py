"""Copy user-supplied M5 files into ignored local storage with provenance."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[2]
FILENAMES = (
    "calendar.csv",
    "sell_prices.csv",
    "sales_train_validation.csv",
    "sales_train_evaluation.csv",
    "sample_submission.csv",
)


def digest(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--destination-dir", required=True, type=Path)
    args = parser.parse_args()
    args.destination_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for filename in FILENAMES:
        source = args.source_dir / filename
        destination = args.destination_dir / filename
        if not source.is_file():
            raise SystemExit(f"STOP: supplied M5 source file is missing: {source}")
        source_size = source.stat().st_size
        source_hash = digest(source)
        shutil.copy2(source, destination)
        destination_hash = digest(destination)
        if destination.stat().st_size != source_size or destination_hash != source_hash:
            raise SystemExit(f"STOP: copied M5 file failed size/hash identity: {filename}")
        rows.append({
            "filename": filename,
            "source_path": str(source.resolve()),
            "destination_path": str(destination.resolve()),
            "size_bytes": source_size,
            "sha256": source_hash,
            "copy_status": "COPIED_AND_VERIFIED",
        })

    audit = {
        "status": "M5_DATA_RESTORATION_PASS",
        "previous_status": "DATA_RESTORATION_BLOCKED_SOURCE_NOT_FOUND",
        "stage4_branch": "feat/m5-demand-calibration-validation-stage4",
        "stage4_initial_blocked_commit": "329e28fab0b018e7e503809fdde100590988c948",
        "failed_restoration_audit_commit": "84c8f923eb22b68e51495fb04656bd49813f3a6e",
        "copy_started": True,
        "calibration_started": False,
        "raw_data_committed": False,
        "files": rows,
    }
    output = ROOT / "artifacts" / "m5_demand_calibration_stage4" / "data_restoration_audit.json"
    output.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": audit["status"], "files": len(rows)}, indent=2))


if __name__ == "__main__":
    main()
