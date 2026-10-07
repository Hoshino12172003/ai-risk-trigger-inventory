from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "data_retention_audit"


def read_csv(name: str) -> list[dict[str, str]]:
    with (ARTIFACTS / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_retention_class_counts_cover_inventory() -> None:
    rows = read_csv("retention_class_counts.csv")
    counts = {row["retention_class"]: int(row["file_count"]) for row in rows}

    assert sum(counts.values()) == 833
    assert counts["KEEP_RAW"] == 166
    assert counts["DISCARD_AFTER_APPROVAL"] == 534
    assert counts["MANUAL_REVIEW"] == 2


def test_screening_summary_accounting_is_consistent() -> None:
    rows = read_csv("screening_summary.csv")

    assert rows
    for row in rows:
        input_count = int(row["input_count"])
        accepted_count = int(row["accepted_count"])
        rejected_count = int(row["rejected_count"])
        assert accepted_count + rejected_count <= input_count

    gkg = next(row for row in rows if row["source_database"] == "GDELT GKG")
    assert int(gkg["input_count"]) == 6
    assert "six URLs matched reliably" in gkg["notes"]
