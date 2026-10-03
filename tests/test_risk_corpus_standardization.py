from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

from ai_risk_trigger_inventory.risk_corpus.standardization import (
    COMMON_COLUMNS,
    GDELT_58_COLUMNS,
    THIN_INDEX_COLUMNS,
    clean_text,
    coordinate,
    country_iso3,
    parse_date,
    standardize_gdelt,
)


def _gdelt_row(record_id: str, date: str = "20200102", country: str = "EC") -> list[str]:
    row = [""] * 58
    values = {
        "GlobalEventID": record_id,
        "SQLDATE": date,
        "EventCode": "190",
        "EventBaseCode": "190",
        "EventRootCode": "19",
        "ActionGeo_FullName": "Quito, Pichincha, Ecuador",
        "ActionGeo_CountryCode": country,
        "ActionGeo_ADM1Code": "EC18",
        "ActionGeo_Lat": "-0.22985",
        "ActionGeo_Long": "-78.5249",
        "SOURCEURL": "https://example.org/event",
    }
    for key, value in values.items():
        row[GDELT_58_COLUMNS.index(key)] = value
    return row


def _write_gdelt(path: Path, rows: list[list[str]]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerows(rows)


def test_deterministic_common_cleaning_rules() -> None:
    assert clean_text("  Pe\u0301ru  ") == "Péru"
    assert country_iso3("Ecuador") == "ECU"
    assert country_iso3("Colombia") == "COL"
    assert country_iso3("Perú") == "PER"
    assert country_iso3("unknown") is None
    assert parse_date("20240229") == "2024-02-29"
    assert parse_date("20240230") is None
    assert parse_date(0) is None
    assert coordinate(90, -90, 90) == (90.0, True)
    assert coordinate(91, -90, 90) == (None, False)


def test_gdelt_two_file_duplicates_are_canonical_and_traceable(tmp_path: Path) -> None:
    first = tmp_path / "gdelt_a.tsv.gz"
    second = tmp_path / "gdelt_b.tsv.gz"
    duplicate = _gdelt_row("100")
    _write_gdelt(first, [duplicate, _gdelt_row("200", country="CO")])
    _write_gdelt(second, [duplicate, _gdelt_row("300", country="PE")])

    summary, output = standardize_gdelt([first, second], tmp_path)

    assert summary["raw_rows"] == 4
    assert summary["standardized_rows"] == 3
    assert summary["exact_duplicate_count"] == 1
    with gzip.open(output, "rt", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert [row["source_record_id"] for row in rows] == ["100", "200", "300"]
    assert {row["country_iso3"] for row in rows} == {"ECU", "COL", "PER"}
    duplicate_rows = list(csv.DictReader((tmp_path / "gdelt_event_duplicates.csv").open(encoding="utf-8")))
    assert duplicate_rows[0]["duplicate_type"] == "EXACT_DUPLICATE"
    assert duplicate_rows[0]["canonical_source_file"] == str(first.resolve())
    assert duplicate_rows[0]["duplicate_source_file"] == str(second.resolve())


def test_common_index_contract_has_no_cross_source_canonical_id() -> None:
    assert "canonical_event_id" not in THIN_INDEX_COLUMNS
    assert "canonical_event_id" not in COMMON_COLUMNS
    assert THIN_INDEX_COLUMNS == [
        "source", "source_record_id", "event_date", "event_end_date",
        "country_iso3", "admin1_raw", "latitude", "longitude",
        "event_type_raw", "title_raw", "source_url", "quality_flag",
    ]


def test_generated_outputs_and_raw_integrity_if_present() -> None:
    root = Path(__file__).resolve().parents[1]
    output_root = root / "data/local/processed_risk_corpus"
    integrity_path = output_root / "audit/raw_integrity_audit.json"
    if not integrity_path.exists():
        return
    integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
    assert integrity["all_unchanged"] is True
    assert integrity["changed_files"] == []
    acquisition = json.loads((output_root / "audit/gdelt_acquisition_comparison.json").read_text(encoding="utf-8"))
    assert acquisition == {
        "combined_max_export_date": "20260831",
        "combined_min_export_date": "20150219",
        "combined_rows_from_logs": 2_744_104,
        "logical_acquisition_count": 2,
        "overlapping_successful_export_dates": 0,
        "physical_event_table_count": 1,
    }
    processed = output_root / "processed"
    expected = [
        "gdelt_event_standardized.tsv.gz", "usgs_standardized.tsv.gz",
        "gdacs_standardized.tsv.gz", "desinventar_standardized.tsv.gz",
        "copernicus_ems_standardized.tsv.gz", "common_event_index.tsv.gz",
    ]
    source_outputs = expected[:-1]
    for name in expected:
        path = processed / name
        assert path.exists()
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            assert reader.fieldnames
            assert next(reader, None) is not None
    for name in source_outputs:
        path = processed / name
        quality_path = processed / name.replace("_standardized.tsv.gz", "_quality_summary.json")
        if name == "gdelt_event_standardized.tsv.gz":
            quality_path = processed / "gdelt_event_quality_summary.json"
        elif name == "copernicus_ems_standardized.tsv.gz":
            quality_path = processed / "copernicus_ems_quality_summary.json"
        expected_rows = json.loads(quality_path.read_text(encoding="utf-8"))["standardized_rows"]
        count = 0
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                count += 1
                assert row["country_iso3"] in {"", "ECU", "COL", "PER"}
                if row["latitude"]:
                    assert -90 <= float(row["latitude"]) <= 90
                if row["longitude"]:
                    assert -180 <= float(row["longitude"]) <= 180
                assert row["event_date"] or "INVALID_DATE" in row["quality_issues"]
                if row["source"] == "GDELT":
                    assert row["source_record_id"] == row["GlobalEventID"]
                elif row["source"] == "USGS":
                    assert row["source_record_id"] == row["native_id"]
                elif row["source"] == "GDACS":
                    assert row["source_record_id"] == ":".join((row["native_eventtype"], row["native_eventid"], row["native_episodeid"]))
                elif row["source"] == "DesInventar":
                    assert row["source_record_id"].endswith(":" + row["native_clave"])
                elif row["source"] == "Copernicus EMS":
                    assert row["source_record_id"] == row["native_code"]
        assert count == expected_rows
    with gzip.open(processed / "common_event_index.tsv.gz", "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert reader.fieldnames == THIN_INDEX_COLUMNS
        for _, row in zip(range(10_000), reader):
            assert row["country_iso3"] in {"", "ECU", "COL", "PER"}
            if row["latitude"]:
                assert -90 <= float(row["latitude"]) <= 90
            if row["longitude"]:
                assert -180 <= float(row["longitude"]) <= 180
            if row["event_date"]:
                assert parse_date(row["event_date"]) is not None
    duplicates = list(csv.DictReader((processed / "gdelt_event_duplicates.csv").open(encoding="utf-8")))
    assert duplicates == []


def test_no_forbidden_processing_outputs_if_present() -> None:
    root = Path(__file__).resolve().parents[1]
    processed = root / "data/local/processed_risk_corpus"
    if not processed.exists():
        return
    forbidden = ("genai", "cluster", "optimization", "canonical_event")
    assert not [path for path in processed.rglob("*") if any(token in path.name.lower() for token in forbidden)]
