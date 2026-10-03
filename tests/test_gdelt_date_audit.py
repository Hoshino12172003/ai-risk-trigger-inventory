from __future__ import annotations

import csv
import gzip
from pathlib import Path

from ai_risk_trigger_inventory.risk_corpus.gdelt_date_audit import (
    GEO_FIELDS,
    audit_gdelt_dates,
    classify_pre2015_date,
)
from ai_risk_trigger_inventory.risk_corpus.standardization import GDELT_58_COLUMNS


def _row(**overrides: str) -> dict[str, str]:
    row = {field: "" for field in GDELT_58_COLUMNS}
    row.update({
        "GlobalEventID": "896048079",
        "SQLDATE": "19200101",
        "MonthYear": "192001",
        "Year": "1920",
        "FractionDate": "1920.0027",
        "DATEADDED": "20200101",
        "SOURCEURL": "https://example.org/historical",
        "EventCode": "010",
        "EventBaseCode": "010",
        "EventRootCode": "01",
    })
    row.update({
        "source_record_id": row["GlobalEventID"],
        "event_date": "1920-01-01",
        "source_file": "raw.tsv.gz",
        "source_row_number": "1",
        "source_url": row["SOURCEURL"],
    })
    row.update(overrides)
    return row


def test_date_classification_distinguishes_consistent_conflicting_and_invalid() -> None:
    assert classify_pre2015_date(_row())[0] == "SOURCE_NATIVE_HISTORICAL_DATE"
    assert classify_pre2015_date(_row(Year="2019"))[0] == "SUSPICIOUS_DATE_ENCODING"
    assert classify_pre2015_date(_row(SQLDATE="19201340"))[0] == "INVALID_DATE"
    assert classify_pre2015_date(_row(DATEADDED=""))[0] == "UNKNOWN"


def test_read_only_audit_outputs_sample_summary_and_geo_check(tmp_path: Path) -> None:
    input_path = tmp_path / "gdelt_event_standardized.tsv.gz"
    fieldnames = list(dict.fromkeys([
        "source_record_id", "event_date", "source_file", "source_row_number", "source_url",
        *GDELT_58_COLUMNS,
    ]))
    rows = [_row(source_row_number=str(index), GlobalEventID=str(896048000 + index)) for index in range(1, 121)]
    for row in rows:
        row["source_record_id"] = row["GlobalEventID"]
    with gzip.open(input_path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    summary = audit_gdelt_dates(input_path, tmp_path / "audit")

    assert summary["pre2015_row_count"] == 120
    assert summary["pre1979_row_count"] == 120
    assert summary["pre1900_row_count"] == 0
    assert summary["related_1920_row_count"] == 120
    assert summary["classification_counts"] == {
        "SOURCE_NATIVE_HISTORICAL_DATE": 120,
        "SUSPICIOUS_DATE_ENCODING": 0,
        "INVALID_DATE": 0,
        "UNKNOWN": 0,
    }
    assert summary["sample"]["row_count"] == 100
    assert summary["geo_field_audit"] == {
        "required_count": 18,
        "present_count": 18,
        "complete": True,
        "missing_fields": [],
        "required_fields": GEO_FIELDS,
    }
    assert summary["input_unchanged"] is True
    assert (tmp_path / "audit/gdelt_pre2015_date_audit.csv").exists()
    assert (tmp_path / "audit/gdelt_pre2015_date_summary.json").exists()
    assert (tmp_path / "audit/gdelt_pre2015_date_report.md").exists()
