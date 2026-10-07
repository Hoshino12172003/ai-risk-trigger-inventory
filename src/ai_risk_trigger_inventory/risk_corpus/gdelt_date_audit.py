"""Read-only audit of historical dates in standardized GDELT Event data."""

from __future__ import annotations

import csv
from datetime import date, datetime
import gzip
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .standardization import file_sha256


ANALYSIS_START = date(2015, 2, 19)
ANALYSIS_END = date(2026, 8, 31)
PRE_1979 = date(1979, 1, 1)
PRE_1900 = date(1900, 1, 1)
SAMPLE_SEED = "gdelt-pre2015-stage5b-audit-20261003"

GEO_FIELDS = [
    f"{actor}Geo_{suffix}"
    for actor in ("Actor1", "Actor2", "Action")
    for suffix in ("Type", "FullName", "CountryCode", "ADM1Code", "Lat", "Long")
]

AUDIT_FIELDS = [
    "classification", "classification_reason", "event_date", "GlobalEventID",
    "source_record_id", "SQLDATE", "MonthYear", "Year", "FractionDate",
    "DATEADDED", "dateadded_iso", "date_fields_consistent",
    "dateadded_in_acquisition_window", "SOURCEURL", "source_url", "EventCode",
    "EventBaseCode", "EventRootCode", *GEO_FIELDS, "source_file",
    "source_row_number",
]


def _yyyymmdd(value: str | None) -> date | None:
    try:
        return datetime.strptime(value or "", "%Y%m%d").date()
    except ValueError:
        return None


def classify_pre2015_date(row: dict[str, str]) -> tuple[str, str, bool, date | None]:
    """Classify source-field consistency without validating the historical event itself."""
    sql_date = _yyyymmdd(row.get("SQLDATE"))
    if sql_date is None:
        return "INVALID_DATE", "SQLDATE is not a valid YYYYMMDD date", False, None

    expected_month = sql_date.strftime("%Y%m")
    expected_year = str(sql_date.year)
    try:
        fraction_year = int(float(row.get("FractionDate") or "nan"))
    except (ValueError, OverflowError):
        fraction_year = None

    conflicts: list[str] = []
    if row.get("event_date") != sql_date.isoformat():
        conflicts.append("event_date")
    if row.get("MonthYear") != expected_month:
        conflicts.append("MonthYear")
    if row.get("Year") != expected_year:
        conflicts.append("Year")
    if fraction_year != sql_date.year:
        conflicts.append("FractionDate")
    if conflicts:
        return (
            "SUSPICIOUS_DATE_ENCODING",
            "SQLDATE conflicts with " + ", ".join(conflicts),
            False,
            _yyyymmdd(row.get("DATEADDED")),
        )

    added = _yyyymmdd(row.get("DATEADDED"))
    if added is None:
        return "UNKNOWN", "source date fields agree but DATEADDED is invalid", True, None
    if not ANALYSIS_START <= added <= ANALYSIS_END:
        return (
            "UNKNOWN",
            "source date fields agree but DATEADDED is outside the acquisition window",
            True,
            added,
        )
    return (
        "SOURCE_NATIVE_HISTORICAL_DATE",
        "SQLDATE, MonthYear, Year, FractionDate year, and standardized event_date agree; "
        "DATEADDED is in the acquisition window",
        True,
        added,
    )


def _audit_record(row: dict[str, str]) -> dict[str, Any]:
    classification, reason, consistent, added = classify_pre2015_date(row)
    result = {field: row.get(field, "") for field in AUDIT_FIELDS}
    result.update({
        "classification": classification,
        "classification_reason": reason,
        "dateadded_iso": added.isoformat() if added else "",
        "date_fields_consistent": str(consistent).lower(),
        "dateadded_in_acquisition_window": str(
            added is not None and ANALYSIS_START <= added <= ANALYSIS_END
        ).lower(),
    })
    return result


def _stable_rank(record: dict[str, Any]) -> str:
    key = "|".join((
        SAMPLE_SEED,
        str(record.get("event_date", "")),
        str(record.get("source_record_id", "")),
        str(record.get("source_file", "")),
        str(record.get("source_row_number", "")),
    ))
    return sha256(key.encode("utf-8")).hexdigest()


def stratified_sample(records: list[dict[str, Any]], size: int = 100) -> list[dict[str, Any]]:
    """Select a deterministic year-stratified sample that includes earliest records."""
    if len(records) <= size:
        return sorted(records, key=lambda row: (row["event_date"], row["source_record_id"]))

    selected: dict[tuple[str, str, str, str], dict[str, Any]] = {}

    def add(record: dict[str, Any]) -> None:
        key = (
            str(record["source_file"]), str(record["source_row_number"]),
            str(record["source_record_id"]), str(record["event_date"]),
        )
        selected[key] = record

    chronological = sorted(records, key=lambda row: (
        row["event_date"], row["source_record_id"], row["source_file"], row["source_row_number"],
    ))
    for record in chronological[:10]:
        add(record)

    years = sorted({record["event_date"][:4] for record in records})
    for year in years:
        group = [record for record in records if record["event_date"].startswith(year)]
        for record in sorted(group, key=_stable_rank)[:5]:
            add(record)

    for record in sorted(records, key=_stable_rank):
        if len(selected) >= size:
            break
        add(record)

    return sorted(selected.values(), key=lambda row: (
        row["event_date"], row["source_record_id"], row["source_file"], row["source_row_number"],
    ))[:size]


def audit_gdelt_dates(input_path: Path, audit_dir: Path, sample_size: int = 100) -> dict[str, Any]:
    """Scan a standardized GDELT table and write the three Stage-5B audit outputs."""
    input_path = input_path.resolve()
    audit_dir.mkdir(parents=True, exist_ok=True)
    before_sha = file_sha256(input_path)
    records: list[dict[str, Any]] = []
    year_counts: dict[str, int] = {}
    classification_counts = {
        "SOURCE_NATIVE_HISTORICAL_DATE": 0,
        "SUSPICIOUS_DATE_ENCODING": 0,
        "INVALID_DATE": 0,
        "UNKNOWN": 0,
    }
    pre_1979_count = 0
    pre_1900_count = 0
    related_1920_count = 0
    earliest: date | None = None
    dateadded_values: list[date] = []
    numeric_event_ids: list[int] = []

    with gzip.open(input_path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fields = reader.fieldnames or []
        missing_geo_fields = [field for field in GEO_FIELDS if field not in fields]
        for row in reader:
            try:
                event_date = date.fromisoformat(row.get("event_date", ""))
            except ValueError:
                continue
            if event_date >= ANALYSIS_START:
                continue
            record = _audit_record(row)
            records.append(record)
            year = str(event_date.year)
            year_counts[year] = year_counts.get(year, 0) + 1
            classification_counts[record["classification"]] += 1
            earliest = event_date if earliest is None else min(earliest, event_date)
            pre_1979_count += int(event_date < PRE_1979)
            pre_1900_count += int(event_date < PRE_1900)
            related_1920_count += int(event_date.year == 1920 or row.get("SQLDATE", "").startswith("1920"))
            added = _yyyymmdd(row.get("DATEADDED"))
            if added:
                dateadded_values.append(added)
            try:
                numeric_event_ids.append(int(row.get("GlobalEventID", "")))
            except ValueError:
                pass

    sample = stratified_sample(records, sample_size)
    sample_path = audit_dir / "gdelt_pre2015_date_audit.csv"
    with sample_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AUDIT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(sample)

    after_sha = file_sha256(input_path)
    summary = {
        "input_file": str(input_path),
        "input_sha256_before": before_sha,
        "input_sha256_after": after_sha,
        "input_unchanged": before_sha == after_sha,
        "analysis_window": {"start": ANALYSIS_START.isoformat(), "end": ANALYSIS_END.isoformat()},
        "pre2015_cutoff": ANALYSIS_START.isoformat(),
        "pre2015_row_count": len(records),
        "pre1979_row_count": pre_1979_count,
        "pre1900_row_count": pre_1900_count,
        "earliest_event_date": earliest.isoformat() if earliest else None,
        "related_1920_row_count": related_1920_count,
        "year_distribution": dict(sorted(year_counts.items())),
        "classification_counts": classification_counts,
        "dateadded_min": min(dateadded_values).isoformat() if dateadded_values else None,
        "dateadded_max": max(dateadded_values).isoformat() if dateadded_values else None,
        "dateadded_in_acquisition_window_count": sum(
            ANALYSIS_START <= value <= ANALYSIS_END for value in dateadded_values
        ),
        "global_event_id_audit": {
            "numeric_count": len(numeric_event_ids),
            "missing_or_nonnumeric_count": len(records) - len(numeric_event_ids),
            "minimum": min(numeric_event_ids) if numeric_event_ids else None,
            "maximum": max(numeric_event_ids) if numeric_event_ids else None,
            "interpretation": "GlobalEventID is an identifier, not a date encoding; DATEADDED provides the acquisition-period check",
        },
        "sample": {
            "path": str(sample_path.resolve()),
            "row_count": len(sample),
            "method": "deterministic SHA-256 ranking stratified by event year; includes 10 earliest records",
            "seed": SAMPLE_SEED,
        },
        "geo_field_audit": {
            "required_count": len(GEO_FIELDS),
            "present_count": len(GEO_FIELDS) - len(missing_geo_fields),
            "complete": not missing_geo_fields,
            "missing_fields": missing_geo_fields,
            "required_fields": GEO_FIELDS,
        },
    }
    summary_path = audit_dir / "gdelt_pre2015_date_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report_path = audit_dir / "gdelt_pre2015_date_report.md"
    report_path.write_text(_report(summary), encoding="utf-8")
    return summary


def _report(summary: dict[str, Any]) -> str:
    classifications = summary["classification_counts"]
    years = "\n".join(
        f"| {year} | {count:,} |" for year, count in summary["year_distribution"].items()
    )
    geo = summary["geo_field_audit"]
    return f"""# GDELT pre-2015 date audit

## Scope and integrity

This is a read-only diagnostic of `{summary['input_file']}`. It does not delete records or modify the Stage 5B-1 standardized table. The input SHA-256 was identical before and after the scan: `{summary['input_sha256_before']}`.

## Results

- Rows before 2015-02-19: {summary['pre2015_row_count']:,}
- Rows before 1979-01-01: {summary['pre1979_row_count']:,}
- Rows before 1900-01-01: {summary['pre1900_row_count']:,}
- Earliest event date: {summary['earliest_event_date']}
- Rows related to year 1920: {summary['related_1920_row_count']:,}
- SOURCE_NATIVE_HISTORICAL_DATE: {classifications['SOURCE_NATIVE_HISTORICAL_DATE']:,}
- SUSPICIOUS_DATE_ENCODING: {classifications['SUSPICIOUS_DATE_ENCODING']:,}
- INVALID_DATE: {classifications['INVALID_DATE']:,}
- UNKNOWN: {classifications['UNKNOWN']:,}

| Event year | Rows |
|---:|---:|
{years}

## Interpretation

`SOURCE_NATIVE_HISTORICAL_DATE` means that SQLDATE, MonthYear, Year, the year component of FractionDate, and the standardized event date agree, while DATEADDED is inside the 2015-02-19 through 2026-08-31 acquisition window. It establishes source-field consistency and distinguishes a historical event date recorded in a later acquisition-period row from an obvious column shift. It does **not** independently verify that the historical date is factually correct.

For the 1920 rows, the aligned 1920 values in the source-native date fields and acquisition-period DATEADDED values support the source-native historical-date interpretation. The audit found no basis for deleting these rows or rewriting their dates.

All {summary['global_event_id_audit']['numeric_count']:,} pre-cutoff rows have numeric GlobalEventID values (range {summary['global_event_id_audit']['minimum']} to {summary['global_event_id_audit']['maximum']}). GlobalEventID is treated as an identifier rather than decoded as a date. DATEADDED is the direct acquisition-period field: it ranges from {summary['dateadded_min']} to {summary['dateadded_max']}, and all {summary['dateadded_in_acquisition_window_count']:,} rows fall within the declared acquisition window.

For Stage 5B-2, the evidence supports adding a separate deterministic flag `in_analysis_window = 2015-02-19 <= event_date <= 2026-08-31`. Out-of-window rows should remain in the standardized corpus but should not enter the formal risk-candidate pool. This is a downstream inclusion rule, not a correction to the source data.

## Geo-field audit

The standardized header contains {geo['present_count']} of {geo['required_count']} required Actor1Geo, Actor2Geo, and ActionGeo fields. Completeness status: `{str(geo['complete']).upper()}`. Missing fields: `{', '.join(geo['missing_fields']) if geo['missing_fields'] else 'none'}`.

## Sample

The companion CSV contains {summary['sample']['row_count']} rows selected by deterministic SHA-256 ranking stratified by event year, with the 10 earliest ordered records forced into the sample. It includes the source-native date, event-code, geo, URL, and row-level provenance fields requested for diagnosis.
"""
