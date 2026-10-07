import csv
import gzip
from hashlib import sha256
import json
from pathlib import Path

from ai_risk_trigger_inventory.risk_corpus.candidate_screening import (
    _candidate,
    gdelt_geo,
    lexical_signals,
    run_candidate_screening,
)


def test_lexicons_preserve_high_recall_boundaries() -> None:
    assert lexical_signals("panic buying caused a demand surge")["flash"]
    assert lexical_signals("compras de pánico por alta demanda")["flash"]
    assert not lexical_signals("holiday promotion discount campaign")["flash"]
    assert not lexical_signals("festival shopping season")["flash"]
    assert not lexical_signals("retail shortage")["flash"]
    assert lexical_signals("major flood emergency")["regional"]
    assert lexical_signals("inundación y estado de emergencia")["regional"]
    assert not lexical_signals("road freight corridor")["transport"]
    assert not lexical_signals("access was closed and blocked")["transport"]
    assert lexical_signals("bridge closed after the storm")["transport"]


def test_geo_and_roles_are_not_overstated() -> None:
    strong = {"ActionGeo_CountryCode": "CO", "Actor1Geo_CountryCode": "", "Actor2Geo_CountryCode": ""}
    actor = {"ActionGeo_CountryCode": "", "Actor1Geo_CountryCode": "PE", "Actor2Geo_CountryCode": ""}
    assert gdelt_geo(strong) == ("ACTION_GEO", "STRONG", "COL")
    assert gdelt_geo(actor) == ("ACTOR_GEO", "MEDIUM", "PER")
    actor_candidate = _candidate({"source_record_id": "g"}, source="GDELT", record_type="EVENT",
        role="EVENT_RECORD_CANDIDATE", text="flood emergency", geo_basis="ACTOR_GEO", geo_strength="MEDIUM")
    assert actor_candidate["screening_priority"] == "C"
    report = _candidate({"source_record_id": "r"}, source="RELIEFWEB", record_type="REPORT",
        role="DOCUMENT_EVIDENCE", text="flood emergency")
    disaster = _candidate({"source_record_id": "d"}, source="RELIEFWEB", record_type="DISASTER",
        role="EVENT_ANCHOR", text="flood emergency", structured=["SOURCE_NATIVE_TYPE:Flood"])
    assert report["evidence_role"] == "DOCUMENT_EVIDENCE"
    assert disaster["evidence_role"] == "EVENT_ANCHOR"


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row}) or ["source_record_id"]
    with gzip.open(path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def _read(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def test_end_to_end_screening_is_deterministic_and_read_only(tmp_path: Path) -> None:
    root = tmp_path / "corpus"; p = root / "processed"
    _write(p / "gdelt_event_standardized.tsv.gz", [
        {"source_record_id": "g1", "event_date": "2020-01-01", "ActionGeo_CountryCode": "CO",
         "ActionGeo_FullName": "Colombia", "SOURCEURL": "https://x/demand-surge", "EventCode": "144",
         "EventBaseCode": "144", "EventRootCode": "14", "country_iso3": "COL"},
        {"source_record_id": "g0", "event_date": "2010-01-01", "ActionGeo_CountryCode": "CO",
         "SOURCEURL": "https://x/flood", "country_iso3": "COL"},
        {"source_record_id": "g2", "event_date": "2020-01-01", "ActionGeo_CountryCode": "CO",
         "SOURCEURL": "https://x/no-family-signal", "EventCode": "144", "EventBaseCode": "144",
         "EventRootCode": "14", "country_iso3": "COL"},
    ])
    _write(p / "reliefweb_reports_standardized.tsv.gz", [
        {"source_record_id": "r1", "date_original": "2020-01-02", "title": "Puente cerrado",
         "body_text": "corte de vía y puente cerrado", "disaster_ids": "[\"d1\"]",
         "target_country_iso3_list": "[\"COL\"]", "target_geo_match_basis": "PRIMARY_COUNTRY",
         "primary_country_iso3": "COL", "reliefweb_url": "https://x/r1", "quality_flags": ""},
        {"source_record_id": "r2", "date_original": "2020-01-03", "title": "Shortage",
         "body_text": "retail shortage", "disaster_ids": "[]", "target_country_iso3_list": "[\"PER\"]",
         "primary_country_iso3": "PER", "reliefweb_url": "https://x/r2", "quality_flags": ""},
    ])
    _write(p / "reliefweb_disasters_standardized.tsv.gz", [{"source_record_id": "d1", "event_date": "2020-01-01",
        "event_name": "Flood", "primary_disaster_type_name": "Flood", "primary_country_iso3": "COL",
        "target_country_match": "true", "target_country_iso3_list": "[\"COL\"]", "reliefweb_url": "https://x/d1"}])
    _write(p / "usgs_standardized.tsv.gz", [{"source_record_id": "u1", "event_date": "2020-01-01", "country_iso3": "PER",
        "event_type_raw": "earthquake", "title_raw": "Earthquake Peru", "source_url": "https://x/u1"}])
    _write(p / "gdacs_standardized.tsv.gz", [{"source_record_id": "a1", "event_date": "2020-01-01", "country_iso3": "ECU",
        "event_type_raw": "FL", "title_raw": "Flood Ecuador", "source_url": "https://x/a1"}])
    _write(p / "desinventar_standardized.tsv.gz", [{"source_record_id": "i1", "event_date": "2020-01-01", "country_iso3": "COL",
        "event_type_raw": "Inundación", "source_url": "https://x/i1", "native_transporte": "0"}])
    _write(p / "copernicus_ems_standardized.tsv.gz", [{"source_record_id": "c1", "event_date": "2020-01-01", "country_iso3": "PER",
        "event_type_raw": "Other", "title_raw": "Preparedness activation", "source_url": "https://x/c1"}])
    for name in ("reliefweb_report_disaster_links.tsv.gz", "reliefweb_report_index.tsv.gz", "common_event_index.tsv.gz", "common_event_index_reliefweb_addition.tsv.gz"):
        _write(p / name, [])
    input_hashes = {path: _digest(path) for path in p.glob("*.gz")}
    first = run_candidate_screening(root)
    first_hashes = dict(first["output_sha256"])
    second = run_candidate_screening(root)
    assert first_hashes == second["output_sha256"]
    assert input_hashes == {path: _digest(path) for path in p.glob("*.gz")}
    gdelt = _read(root / "candidates" / "gdelt_risk_candidates.tsv.gz")
    reports = _read(root / "candidates" / "reliefweb_report_risk_candidates.tsv.gz")
    anchors = _read(root / "candidates" / "structured_event_anchors.tsv.gz")
    assert [row["source_record_id"] for row in gdelt] == ["g1"]
    assert reports[0]["source_record_id"] == "r1" and reports[0]["evidence_role"] == "DOCUMENT_EVIDENCE"
    assert all(row["source_record_id"] != "r2" for row in reports)
    assert {row["source_record_id"] for row in anchors} >= {"d1", "u1", "a1", "i1", "c1"}
    assert next(row for row in anchors if row["source_record_id"] == "u1")["candidate_regional_emergency"] == "true"
