import csv
import gzip
from hashlib import sha256
import json
from pathlib import Path

from ai_risk_trigger_inventory.risk_corpus.reliefweb_standardization import (
    clean_body,
    run_reliefweb_standardization,
)


COUNTRIES = {"ecuador": "ECU", "colombia": "COL", "peru": "PER"}


def _report(record_id: int, country: str, *, disaster_id: int | None = None) -> dict:
    fields = {
        "uuid": f"report-{record_id}",
        "title": f"Informe {record_id}",
        "body": "  Información en español: Perú, acción.\r\n\r\n\r\nFin.  ",
        "body-html": "<p>not retained</p>",
        "origin": "https://example.org/original",
        "url": f"https://reliefweb.int/node/{record_id}",
        "url_alias": f"https://reliefweb.int/report/{record_id}",
        "date": {
            "original": "2016-02-03T00:00:00+00:00",
            "created": "2016-02-04T00:00:00+00:00",
            "changed": "2016-02-05T00:00:00+00:00",
        },
        "primary_country": {"name": country.title(), "iso3": COUNTRIES[country]},
        "country": [{"name": country.title(), "iso3": COUNTRIES[country]}],
        "source": [{"name": "Test Source", "shortname": "TS", "type": {"name": "NGO"}}],
        "language": [{"name": "Spanish", "code": "es"}],
        "format": [{"name": "Assessment"}],
        "file": [{"url": "https://example.org/a.pdf", "filename": "a.pdf", "mimetype": "application/pdf"}],
    }
    if disaster_id is not None:
        fields["disaster"] = [{"id": disaster_id, "name": "Native event", "glide": "EQ-TEST"}]
    return {"id": record_id, "href": f"https://api.reliefweb.int/v1/reports/{record_id}", "fields": fields}


def _disaster(record_id: int, country: str, *, event_date: str | None) -> dict:
    dates = {"created": "2016-01-01T00:00:00+00:00", "changed": "2016-01-02T00:00:00+00:00"}
    if event_date is not None:
        dates["event"] = event_date
    return {
        "id": record_id,
        "href": f"https://api.reliefweb.int/v1/disasters/{record_id}",
        "fields": {
            "uuid": f"disaster-{record_id}", "name": "Native event", "description": "Descripción",
            "status": "ongoing", "glide": "EQ-TEST", "date": dates,
            "primary_country": {"name": country.title(), "iso3": COUNTRIES[country]},
            "country": [{"name": country.title(), "iso3": COUNTRIES[country]}],
            "primary_type": {"name": "Earthquake", "code": "EQ"},
            "type": [{"name": "Earthquake", "code": "EQ"}],
            "url": f"https://reliefweb.int/disaster/{record_id}",
            "url_alias": f"https://reliefweb.int/disaster/test-{record_id}",
        },
    }


def _write_page(root: Path, record_type: str, country: str, records: list[dict]) -> None:
    folder = root / record_type / country
    folder.mkdir(parents=True)
    payload = {
        "href": f"https://api.reliefweb.int/v1/{record_type}?country={country}",
        "totalCount": len(records), "count": len(records), "data": records,
    }
    (folder / "page_0000.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def test_reliefweb_standardization_preserves_semantics_and_is_deterministic(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    shared_report = _report(1, "ecuador", disaster_id=10)
    shared_disaster = _disaster(10, "ecuador", event_date="2014-12-31T00:00:00+00:00")
    missing_body = _report(3, "peru")
    missing_body["fields"].pop("body")
    identifier_only = _report(4, "peru")
    for field in ("body", "title", "origin", "file"):
        identifier_only["fields"].pop(field)
    _write_page(raw, "reports", "ecuador", [shared_report, _report(2, "ecuador")])
    _write_page(raw, "reports", "colombia", [shared_report])
    _write_page(raw, "reports", "peru", [missing_body, identifier_only])
    _write_page(raw, "disasters", "ecuador", [shared_disaster])
    _write_page(raw, "disasters", "colombia", [shared_disaster])
    _write_page(raw, "disasters", "peru", [_disaster(11, "peru", event_date=None)])
    expected = {
        ("reports", "ecuador"): 2, ("reports", "colombia"): 1, ("reports", "peru"): 2,
        ("disasters", "ecuador"): 1, ("disasters", "colombia"): 1, ("disasters", "peru"): 1,
    }
    before = {path: _sha(path) for path in raw.rglob("*.json")}
    output = tmp_path / "processed_risk_corpus"
    common = output / "processed" / "common_event_index.tsv.gz"
    common.parent.mkdir(parents=True)
    common.write_bytes(b"existing-index-sentinel")
    common_before = _sha(common)

    first = run_reliefweb_standardization(raw, output, expected)
    first_hashes = dict(first["output_sha256"])
    second = run_reliefweb_standardization(raw, output, expected)

    assert first_hashes == second["output_sha256"]
    assert before == {path: _sha(path) for path in raw.rglob("*.json")}
    assert _sha(common) == common_before
    reports = _read_tsv(output / "processed" / "reliefweb_reports_standardized.tsv.gz")
    disasters = _read_tsv(output / "processed" / "reliefweb_disasters_standardized.tsv.gz")
    links = _read_tsv(output / "processed" / "reliefweb_report_disaster_links.tsv.gz")
    additions = _read_tsv(output / "processed" / "common_event_index_reliefweb_addition.tsv.gz")

    assert len(reports) == len({row["source_record_id"] for row in reports}) == 4
    assert len(disasters) == len({row["source_record_id"] for row in disasters}) == 2
    assert reports[0]["body_text"] == clean_body(shared_report["fields"]["body"])
    assert "Información en español: Perú, acción." in reports[0]["body_text"]
    assert reports[0]["date_original"] == "2016-02-03T00:00:00Z"
    assert json.loads(reports[0]["download_country_membership"]) == ["col", "ecu"]
    assert json.loads(reports[0]["target_country_iso3_list"]) == ["ECU"]
    assert len(links) == 1
    assert links[0]["report_id"] == "1"
    assert links[0]["disaster_id"] == "10"
    assert links[0]["link_source"] == "RELIEFWEB_NATIVE"
    assert links[0]["source_file"] in json.loads(reports[0]["source_files"])
    assert {row["report_id"] for row in links} <= {row["source_record_id"] for row in reports}
    assert disasters[0]["event_date"] == "2014-12-31T00:00:00Z"
    assert "EVENT_DATE_OUTSIDE_DOWNLOAD_WINDOW" in disasters[0]["quality_flags"]
    assert disasters[1]["event_date"] == ""
    assert "MISSING_EVENT_DATE" in disasters[1]["quality_flags"]
    assert additions and {row["source_record_type"] for row in additions} == {"DISASTER"}
    assert {row["source_record_id"] for row in additions} == {row["source_record_id"] for row in disasters}
    assert first["report_duplicate_copies"] == 1
    assert first["disaster_duplicate_copies"] == 1
    assert first["payload_conflict_ids"] == 0
    missing_audit = list(csv.DictReader(
        (output / "audit" / "reliefweb_missing_body_audit.csv").open(encoding="utf-8", newline="")
    ))
    missing_summary = json.loads(
        (output / "audit" / "reliefweb_missing_body_summary.json").read_text(encoding="utf-8")
    )
    assert {row["source_record_id"] for row in missing_audit} == {"3", "4"}
    assert missing_summary["missing_body_reports"] == 2
    assert missing_summary["usable_non_body_evidence_present"] == 1
    assert missing_summary["identifier_only_no_usable_content"] == 1
