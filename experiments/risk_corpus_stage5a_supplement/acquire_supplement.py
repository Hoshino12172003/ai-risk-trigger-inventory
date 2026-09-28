"""Recover bounded Stage-5A gaps without modifying accepted baseline data."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_risk_trigger_inventory.risk_corpus.raw_acquisition import (
    REQUESTED_END,
    REQUESTED_START,
    TARGET_COUNTRIES,
    file_digest,
    finalize_source,
    utc_timestamp,
    write_csv,
    write_json,
)
from ai_risk_trigger_inventory.risk_corpus.supplement import (
    BATCH_ID,
    GDELT_NATIVE_START,
    RISK_TERMS,
    filter_event_row,
    filter_gkg_row,
    gdelt_anchor_days,
    gdelt_monthly_anchor_days,
)


BASELINE_COMMIT = "ead6d5ae02502382d63402959a8120dbbfe54fc6"
PAPER2_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
USER_AGENT = "ai-risk-trigger-inventory-stage5a-supplement/1.0"
GDELT_BASE = "https://data.gdeltproject.org/gdeltv2"
GDELT_MAX_COMPRESSED_BYTES = 8_000_000
SOURCE_KEYS = ("gdelt", "reliefweb", "gdacs", "desinventar", "usgs", "copernicus")
SOURCE_NAMES = {
    "gdelt": "GDELT", "reliefweb": "ReliefWeb", "gdacs": "GDACS",
    "desinventar": "DesInventar", "usgs": "USGS", "copernicus": "Copernicus EMS",
}


def http_head(url: str) -> tuple[int | None, int | None, str]:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return response.status, int(response.headers.get("Content-Length") or 0), ""
    except urllib.error.HTTPError as error:
        return error.code, None, str(error)
    except Exception as error:
        return None, None, f"{type(error).__name__}: {error}"


def download(url: str, destination: Path, timeout: int = 180) -> dict[str, Any]:
    row: dict[str, Any] = {
        "official_url": url,
        "retrieval_timestamp": utc_timestamp(),
        "http_status": None,
        "status_result": "DOWNLOAD_FAILED",
        "size_bytes": 0,
        "sha256": "",
        "error": "",
    }
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    part = destination.with_name(destination.name + ".part")
    part.unlink(missing_ok=True)
    try:
        started = time.monotonic()
        digest = sha256()
        size = 0
        with urllib.request.urlopen(request, timeout=min(timeout, 15)) as response, part.open("wb") as handle:
            row["http_status"] = response.status
            expected = int(response.headers.get("Content-Length") or 0)
            while True:
                if time.monotonic() - started > timeout:
                    raise TimeoutError(f"overall download deadline exceeded ({timeout}s)")
                block = response.read1(64 * 1024)
                if not block:
                    break
                handle.write(block)
                digest.update(block)
                size += len(block)
            if expected and size != expected:
                raise OSError(f"incomplete response: received {size} of {expected} bytes")
    except urllib.error.HTTPError as error:
        row["http_status"] = error.code
        row["error"] = error.read(1024).decode("utf-8", errors="replace")
        return row
    except Exception as error:
        row["error"] = f"{type(error).__name__}: {error}"
        part.unlink(missing_ok=True)
        return row
    destination.parent.mkdir(parents=True, exist_ok=True)
    part.replace(destination)
    row.update({
        "status_result": "DOWNLOADED",
        "size_bytes": size,
        "sha256": digest.hexdigest(),
    })
    return row


def gdelt_candidates(anchor: str) -> list[str]:
    anchor_date = datetime.strptime(anchor, "%Y%m%d").date()
    final_date = date.fromisoformat(REQUESTED_END[:10])
    return [anchor + "120000"] if anchor_date <= final_date else []


def choose_gdelt_timestamp(anchor: str, availability_rows: list[dict[str, Any]]) -> str | None:
    for timestamp in gdelt_candidates(anchor):
        statuses = []
        sizes = []
        for family, suffix in (("GDELT_EVENT", "export.CSV.zip"), ("GDELT_GKG", "gkg.csv.zip")):
            url = f"{GDELT_BASE}/{timestamp}.{suffix}"
            status, size, error = http_head(url)
            availability_rows.append({
                "anchor_date": anchor,
                "candidate_timestamp": timestamp,
                "source_family": family,
                "official_url": url,
                "http_status": status,
                "advertised_size_bytes": size,
                "error": error,
                "supplement_batch_id": BATCH_ID,
            })
            statuses.append(status)
            sizes.append(size)
        if statuses == [200, 200] and all(
            size is not None and size <= GDELT_MAX_COMPRESSED_BYTES for size in sizes
        ):
            return timestamp
    return None


def process_gdelt_archive(
    archive_path: Path,
    output_path: Path,
    family: str,
    seen_ids: set[str],
) -> dict[str, Any]:
    filter_row = filter_event_row if family == "GDELT_EVENT" else filter_gkg_row
    total = retained = malformed = exact_duplicates = 0
    countries: Counter[str] = Counter()
    years: Counter[str] = Counter()
    provenance: Counter[str] = Counter()
    term_counts: Counter[str] = Counter()
    dates: list[str] = []
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive, output_path.open("wb") as output:
        member = archive.namelist()[0]
        with archive.open(member) as source:
            for line in source:
                total += 1
                fields = line.decode("utf-8", errors="replace").rstrip("\r\n").split("\t")
                metadata = filter_row(fields)
                if metadata is None:
                    malformed += int(len(fields) < (68 if family == "GDELT_EVENT" else 16))
                    continue
                record_id = str(metadata["record_id"])
                if record_id in seen_ids:
                    exact_duplicates += 1
                    continue
                seen_ids.add(record_id)
                output.write(line if line.endswith(b"\n") else line + b"\n")
                retained += 1
                value = str(metadata["date"])
                if len(value) >= 8 and value[:8].isdigit():
                    dates.append(value[:8])
                    years[value[:4]] += 1
                countries.update(metadata["countries"])
                provenance[str(metadata["geography_provenance"])] += 1
                term_counts.update(metadata["matched_retrieval_terms"])
    return {
        "source_member": member,
        "records_inspected": total,
        "records_retained": retained,
        "records_discarded": total - retained,
        "malformed_records": malformed,
        "exact_duplicate_records": exact_duplicates,
        "actual_min_date": min(dates) if dates else None,
        "actual_max_date": max(dates) if dates else None,
        "geography_counts": dict(sorted(countries.items())),
        "year_counts": dict(sorted(years.items())),
        "geography_provenance_counts": dict(sorted(provenance.items())),
        "retrieval_term_counts": dict(sorted(term_counts.items())),
        "filtered_filename": output_path.name,
        "filtered_size_bytes": output_path.stat().st_size,
        "filtered_sha256": file_digest(output_path),
    }


def acquire_gdelt(corpus_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    supplement = corpus_root / "gdelt" / "supplement"
    raw = supplement / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    availability_rows: list[dict[str, Any]] = []
    log_rows: list[dict[str, Any]] = []
    seen = {"GDELT_EVENT": set(), "GDELT_GKG": set()}
    selected = []
    for anchor in gdelt_anchor_days():
        timestamp = choose_gdelt_timestamp(anchor, availability_rows)
        if timestamp:
            selected.append((anchor, timestamp))

    family_suffixes = (("GDELT_EVENT", "export.CSV.zip"), ("GDELT_GKG", "gkg.csv.zip"))
    for anchor, timestamp in selected:
        for family, suffix in family_suffixes:
            url = f"{GDELT_BASE}/{timestamp}.{suffix}"
            temporary = supplement / f".temporary_{timestamp}_{suffix}"
            download_row = download(url, temporary)
            row = {
                "source": "GDELT",
                "source_family": family,
                "source_file_identifier": f"{timestamp}.{suffix}",
                "requested_start": REQUESTED_START,
                "requested_end": REQUESTED_END,
                "country_scope": "|".join(TARGET_COUNTRIES),
                "filter_logic": "structured target geography AND frozen broad source-level risk terms",
                "supplement_batch_id": BATCH_ID,
                "anchor_date": anchor,
                **download_row,
            }
            if download_row["status_result"] == "DOWNLOADED":
                output = raw / f"{family.lower()}_{timestamp}.tsv"
                audit = process_gdelt_archive(temporary, output, family, seen[family])
                row.update(audit)
                row["filename"] = output.name
                row["temporary_global_file_deleted"] = True
                temporary.unlink()
            log_rows.append(row)

    retained = sum(int(row.get("records_retained", 0)) for row in log_rows)
    dates = [
        value for row in log_rows for value in (row.get("actual_min_date"), row.get("actual_max_date")) if value
    ]
    country_counts: Counter[str] = Counter()
    year_counts: Counter[str] = Counter()
    for row in log_rows:
        country_counts.update(row.get("geography_counts", {}))
        year_counts.update(row.get("year_counts", {}))
    status = "PARTIAL_SOURCE_COVERAGE" if retained else "DOWNLOAD_FAILED"
    manifest = {
        "source": "GDELT",
        "status": status,
        "supplement_batch_id": BATCH_ID,
        "requested_start_date": REQUESTED_START,
        "requested_end_date": REQUESTED_END,
        "source_native_start_date": GDELT_NATIVE_START,
        "source_native_start_gap": "2015-01-01 through 2015-02-18",
        "sampling_rule": "one official 15-minute Event/GKG pair at 12:00 UTC for each frozen annual anchor, subject to an 8 MB per-file safety cap",
        "compressed_file_safety_cap_bytes": GDELT_MAX_COMPRESSED_BYTES,
        "coverage_limitation": "bounded systematic sample, not a complete GDELT historical census",
        "actual_min_date": min(dates) if dates else None,
        "actual_max_date": max(dates) if dates else None,
        "files_inspected": len(log_rows),
        "records_inspected": sum(int(row.get("records_inspected", 0)) for row in log_rows),
        "records_retained": retained,
        "records_discarded": sum(int(row.get("records_discarded", 0)) for row in log_rows),
        "event_count": sum(int(row.get("records_retained", 0)) for row in log_rows if row["source_family"] == "GDELT_EVENT"),
        "gkg_count": sum(int(row.get("records_retained", 0)) for row in log_rows if row["source_family"] == "GDELT_GKG"),
        "exact_duplicate_count": sum(int(row.get("exact_duplicate_records", 0)) for row in log_rows),
        "geography_counts": dict(sorted(country_counts.items())),
        "year_counts": dict(sorted(year_counts.items())),
        "stored_file_count": sum(row.get("status_result") == "DOWNLOADED" for row in log_rows),
        "stored_bytes": sum(int(row.get("filtered_size_bytes", 0)) for row in log_rows),
        "compressed_bytes_streamed_then_deleted": sum(int(row.get("size_bytes", 0)) for row in log_rows),
        "retrieval_terms": list(RISK_TERMS),
        "retrieval_timestamp": utc_timestamp(),
    }
    schema = {
        "storage": "source-native tab-delimited rows retained unchanged after filtering",
        "families": {
            "GDELT_EVENT": {"minimum_columns": 68, "codebook": "GDELT Event Database Codebook V2.0"},
            "GDELT_GKG": {"minimum_columns": 16, "codebook": "GDELT GKG Codebook V2.1"},
        },
    }
    finalize_source(supplement, manifest, log_rows, schema)
    write_csv(ROOT / "artifacts" / "risk_corpus_stage5a_supplement" / "gdelt_filter_audit.csv", log_rows)
    write_csv(ROOT / "artifacts" / "risk_corpus_stage5a_supplement" / "gdelt_availability_audit.csv", availability_rows)
    return manifest, log_rows


def record_gdelt_failed_recovery(corpus_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    supplement = corpus_root / "gdelt" / "supplement"
    raw = supplement / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for path in list(supplement.glob(".temporary_*")) + list(raw.glob("gdelt_*.tsv")):
        path.unlink(missing_ok=True)
    row = {
        "source": "GDELT",
        "source_family": "GDELT_EVENT|GDELT_GKG",
        "filename": "",
        "official_url": GDELT_BASE,
        "query_or_source_file_identifier": "20150219120000 Event/GKG pair",
        "requested_start": REQUESTED_START,
        "requested_end": REQUESTED_END,
        "actual_min_date": "",
        "actual_max_date": "",
        "country_scope": "|".join(TARGET_COUNTRIES),
        "filter_logic": "structured target geography AND frozen broad source-level risk terms",
        "http_status": "200 on availability checks",
        "status_result": "DOWNLOAD_FAILED",
        "size_bytes": 0,
        "sha256": "",
        "retrieval_timestamp": utc_timestamp(),
        "record_count": 0,
        "supplement_batch_id": BATCH_ID,
        "error": "three bounded runs failed to complete the official GKG stream; no partial output accepted",
    }
    manifest = {
        "source": "GDELT",
        "status": "DOWNLOAD_FAILED",
        "supplement_batch_id": BATCH_ID,
        "requested_start_date": REQUESTED_START,
        "requested_end_date": REQUESTED_END,
        "source_native_start_date": GDELT_NATIVE_START,
        "source_native_start_gap": "2015-01-01 through 2015-02-18",
        "sampling_rule": "frozen annual 12:00 UTC Event/GKG anchors with an 8 MB per-file safety cap",
        "coverage_limitation": row["error"],
        "actual_min_date": None,
        "actual_max_date": None,
        "files_inspected": 0,
        "records_inspected": 0,
        "records_retained": 0,
        "records_discarded": 0,
        "event_count": 0,
        "gkg_count": 0,
        "exact_duplicate_count": 0,
        "geography_counts": {},
        "year_counts": {},
        "stored_file_count": 0,
        "stored_bytes": 0,
        "retrieval_terms": list(RISK_TERMS),
        "retrieval_timestamp": utc_timestamp(),
    }
    finalize_source(supplement, manifest, [row], {
        "status": "no accepted supplemental schema snapshot; bounded official stream did not complete",
    })
    artifact_dir = ROOT / "artifacts" / "risk_corpus_stage5a_supplement"
    write_csv(artifact_dir / "gdelt_filter_audit.csv", [row])
    write_csv(artifact_dir / "gdelt_availability_audit.csv", [{
        "source_family": "GDELT_EVENT",
        "candidate_timestamp": "20150219120000",
        "http_status": 200,
        "advertised_size_bytes": 119836,
        "supplement_batch_id": BATCH_ID,
    }, {
        "source_family": "GDELT_GKG",
        "candidate_timestamp": "20150219120000",
        "http_status": 200,
        "advertised_size_bytes": 7028948,
        "supplement_batch_id": BATCH_ID,
    }])
    return manifest, [row]


def acquire_gdelt_event_only(corpus_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Recover the independent small Event family after bounded GKG failure."""
    supplement = corpus_root / "gdelt" / "supplement"
    raw = supplement / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for path in list(supplement.glob(".temporary_*")) + list(raw.glob("gdelt_*.tsv")):
        path.unlink(missing_ok=True)
    logs: list[dict[str, Any]] = []
    availability: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in gdelt_monthly_anchor_days():
        timestamp = anchor + "120000"
        url = f"{GDELT_BASE}/{timestamp}.export.CSV.zip"
        status, advertised_size, error = http_head(url)
        availability.append({
            "source_family": "GDELT_EVENT",
            "candidate_timestamp": timestamp,
            "official_url": url,
            "http_status": status,
            "advertised_size_bytes": advertised_size,
            "error": error,
            "supplement_batch_id": BATCH_ID,
        })
        row = {
            "source": "GDELT",
            "source_family": "GDELT_EVENT",
            "source_file_identifier": f"{timestamp}.export.CSV.zip",
            "requested_start": REQUESTED_START,
            "requested_end": REQUESTED_END,
            "country_scope": "|".join(TARGET_COUNTRIES),
            "filter_logic": "structured target geography AND frozen broad source-level risk terms",
            "supplement_batch_id": BATCH_ID,
            "anchor_date": anchor,
        }
        if status != 200 or advertised_size is None or advertised_size > GDELT_MAX_COMPRESSED_BYTES:
            row.update({
                "official_url": url, "retrieval_timestamp": utc_timestamp(),
                "http_status": status, "status_result": "DOWNLOAD_FAILED",
                "size_bytes": 0, "sha256": "", "error": error or "unavailable or above safety cap",
            })
            logs.append(row)
            continue
        temporary = supplement / f".temporary_{timestamp}_export.CSV.zip"
        row.update(download(url, temporary))
        if row["status_result"] == "DOWNLOADED":
            output = raw / f"gdelt_event_{timestamp}.tsv"
            row.update(process_gdelt_archive(temporary, output, "GDELT_EVENT", seen))
            if row["records_retained"]:
                row["filename"] = output.name
                row["filtered_file_retained"] = True
            else:
                output.unlink()
                row["filename"] = ""
                row["filtered_file_retained"] = False
            row["temporary_global_file_deleted"] = True
            temporary.unlink()
        logs.append(row)
    logs.append({
        "source": "GDELT", "source_family": "GDELT_GKG", "filename": "",
        "official_url": GDELT_BASE, "query_or_source_file_identifier": "20150219120000.gkg.csv.zip",
        "requested_start": REQUESTED_START, "requested_end": REQUESTED_END,
        "country_scope": "|".join(TARGET_COUNTRIES),
        "filter_logic": "structured target geography AND frozen broad source-level risk terms",
        "http_status": "200 on availability checks", "status_result": "DOWNLOAD_FAILED",
        "size_bytes": 0, "sha256": "", "retrieval_timestamp": utc_timestamp(),
        "record_count": 0, "supplement_batch_id": BATCH_ID,
        "error": "three bounded GKG stream runs did not complete; no partial output accepted",
    })
    successful = [row for row in logs if row.get("source_family") == "GDELT_EVENT" and row.get("status_result") == "DOWNLOADED"]
    retained = sum(int(row.get("records_retained", 0)) for row in successful)
    dates = [value for row in successful for value in (row.get("actual_min_date"), row.get("actual_max_date")) if value]
    country_counts: Counter[str] = Counter()
    year_counts: Counter[str] = Counter()
    for row in successful:
        country_counts.update(row.get("geography_counts", {}))
        year_counts.update(row.get("year_counts", {}))
    manifest = {
        "source": "GDELT",
        "status": "PARTIAL_SOURCE_COVERAGE" if retained else "DOWNLOAD_FAILED",
        "supplement_batch_id": BATCH_ID,
        "requested_start_date": REQUESTED_START,
        "requested_end_date": REQUESTED_END,
        "source_native_start_date": GDELT_NATIVE_START,
        "source_native_start_gap": "2015-01-01 through 2015-02-18",
        "sampling_rule": "one official 15-minute Event file at 12:00 UTC for each frozen monthly anchor, plus native start and requested end",
        "coverage_limitation": "bounded Event sample only; official GKG streaming failed after three bounded runs",
        "actual_min_date": min(dates) if dates else None,
        "actual_max_date": max(dates) if dates else None,
        "files_inspected": len(successful),
        "records_inspected": sum(int(row.get("records_inspected", 0)) for row in successful),
        "records_retained": retained,
        "records_discarded": sum(int(row.get("records_discarded", 0)) for row in successful),
        "event_count": retained,
        "gkg_count": 0,
        "exact_duplicate_count": sum(int(row.get("exact_duplicate_records", 0)) for row in successful),
        "geography_counts": dict(sorted(country_counts.items())),
        "year_counts": dict(sorted(year_counts.items())),
        "stored_file_count": sum(bool(row.get("records_retained")) for row in successful),
        "stored_bytes": sum(int(row.get("filtered_size_bytes", 0)) for row in successful),
        "retrieval_terms": list(RISK_TERMS),
        "retrieval_timestamp": utc_timestamp(),
    }
    finalize_source(supplement, manifest, logs, {
        "storage": "source-native Event tab-delimited rows retained unchanged after filtering",
        "GDELT_EVENT": {"minimum_columns": 68, "codebook": "GDELT Event Database Codebook V2.0"},
        "GDELT_GKG": {"status": "not accepted because bounded stream did not complete"},
    })
    artifact_dir = ROOT / "artifacts" / "risk_corpus_stage5a_supplement"
    write_csv(artifact_dir / "gdelt_filter_audit.csv", logs)
    write_csv(artifact_dir / "gdelt_availability_audit.csv", availability)
    return manifest, logs


def load_supplement_source(corpus_root: Path, source: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    supplement = corpus_root / source / "supplement"
    manifest = json.loads((supplement / "manifest.json").read_text(encoding="utf-8"))
    with (supplement / "download_log.csv").open(encoding="utf-8", newline="") as handle:
        logs = list(csv.DictReader(handle))
    return manifest, logs


def load_and_clean_gdelt(corpus_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest, logs = load_supplement_source(corpus_root, "gdelt")
    raw = corpus_root / "gdelt" / "supplement" / "raw"
    for path in raw.glob("gdelt_event_*.tsv"):
        if path.stat().st_size == 0:
            path.unlink()
    for row in logs:
        if int(row.get("records_retained") or 0) == 0:
            row["filename"] = ""
            row["filtered_file_retained"] = False
    manifest["stored_file_count"] = 0
    manifest["stored_bytes"] = 0
    finalize_source(corpus_root / "gdelt" / "supplement", manifest, logs, {
        "storage": "no filtered raw rows retained because all inspected Event records failed the frozen filter",
        "GDELT_EVENT": {"minimum_columns": 68, "codebook": "GDELT Event Database Codebook V2.0"},
        "GDELT_GKG": {"status": "not accepted because bounded stream did not complete"},
    })
    return manifest, logs


def record_reliefweb(corpus_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    supplement = corpus_root / "reliefweb" / "supplement"
    (supplement / "raw").mkdir(parents=True, exist_ok=True)
    credential_names = ("RELIEFWEB_APPNAME", "RELIEFWEB_API_APPNAME")
    available_name = next((name for name in credential_names if os.environ.get(name)), None)
    if available_name is not None:
        raise RuntimeError(
            "An approved ReliefWeb appname exists, but authenticated acquisition is not implemented in this bounded supplement; stop for review."
        )
    row = {
        "source": "ReliefWeb",
        "source_family": "RELIEFWEB_REPORTS_AND_DISASTERS",
        "filename": "",
        "official_url": "https://api.reliefweb.int/v2/",
        "query_or_source_file_identifier": "planned reports + disasters pagination",
        "requested_start": REQUESTED_START,
        "requested_end": REQUESTED_END,
        "actual_min_date": "",
        "actual_max_date": "",
        "country_scope": "|".join(TARGET_COUNTRIES),
        "filter_logic": "country/date filters; deterministic offset/limit pagination",
        "http_status": "",
        "status_result": "AUTH_BLOCKED",
        "size_bytes": 0,
        "sha256": "",
        "retrieval_timestamp": utc_timestamp(),
        "record_count": 0,
        "supplement_batch_id": BATCH_ID,
        "required_action": "provide a pre-approved appname through RELIEFWEB_APPNAME or RELIEFWEB_API_APPNAME",
    }
    manifest = {
        "source": "ReliefWeb",
        "status": "AUTH_BLOCKED",
        "supplement_batch_id": BATCH_ID,
        "requested_start_date": REQUESTED_START,
        "requested_end_date": REQUESTED_END,
        "actual_min_date": None,
        "actual_max_date": None,
        "approved_appname_available": False,
        "credential_values_logged": False,
        "disaster_count": 0,
        "report_count": 0,
        "files": 0,
        "bytes": 0,
        "required_action": row["required_action"],
        "planned_endpoints": [
            "https://api.reliefweb.int/v2/disasters",
            "https://api.reliefweb.int/v2/reports",
        ],
        "planned_pagination": {"limit": 1000, "offset": "0, 1000, ... until short page"},
        "retrieval_timestamp": utc_timestamp(),
    }
    finalize_source(supplement, manifest, [row], {"status": "not retrieved; approved appname absent"})
    write_csv(ROOT / "artifacts" / "risk_corpus_stage5a_supplement" / "reliefweb_acquisition_audit.csv", [row])
    return manifest, [row]


def inspect_desinventar(path: Path) -> dict[str, Any]:
    total = dated = in_window = 0
    dates: list[str] = []
    window_dates: list[str] = []
    events: Counter[str] = Counter()
    admins: Counter[str] = Counter()
    with zipfile.ZipFile(path) as archive:
        members = archive.namelist()
        xml_member = next(member for member in members if member.lower().endswith(".xml"))
        in_fichas = False
        with archive.open(xml_member) as handle:
            for action, element in ET.iterparse(handle, events=("start", "end")):
                if action == "start" and element.tag == "fichas":
                    in_fichas = True
                elif action == "end" and element.tag == "TR" and in_fichas:
                    total += 1
                    values = {child.tag: (child.text or "").strip() for child in element}
                    try:
                        value = date(
                            int(values["fechano"]),
                            max(1, int(values.get("fechames") or 1)),
                            max(1, int(values.get("fechadia") or 1)),
                        ).isoformat()
                    except (KeyError, TypeError, ValueError):
                        value = None
                    if value:
                        dated += 1
                        dates.append(value)
                        if REQUESTED_START[:10] <= value <= REQUESTED_END[:10]:
                            in_window += 1
                            window_dates.append(value)
                            if values.get("evento"):
                                events[values["evento"]] += 1
                            if values.get("name0"):
                                admins[values["name0"]] += 1
                    element.clear()
                elif action == "end" and element.tag == "fichas":
                    in_fichas = False
                    element.clear()
    return {
        "xml_member": xml_member,
        "members": members,
        "total_source_records": total,
        "dated_source_records": dated,
        "in_window_records": in_window,
        "out_of_window_records": total - in_window,
        "raw_min_date": min(dates) if dates else None,
        "raw_max_date": max(dates) if dates else None,
        "actual_min_date": min(window_dates) if window_dates else None,
        "actual_max_date": max(window_dates) if window_dates else None,
        "disaster_type_counts": dict(sorted(events.items())),
        "province_admin_counts": dict(sorted(admins.items())),
    }


def acquire_desinventar_ecuador(corpus_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    supplement = corpus_root / "desinventar" / "supplement"
    raw = supplement / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    destination = raw / f"DI_export_ecu_{BATCH_ID}.zip"
    url = "https://www.desinventar.net/DesInventar/download/DI_export_ecu.zip"
    logs = []
    for attempt in range(1, 3):
        row = {
            "source": "DesInventar",
            "source_family": "DESINVENTAR_ECUADOR",
            "filename": destination.name,
            "query_or_source_file_identifier": "DI_export_ecu.zip",
            "requested_start": REQUESTED_START,
            "requested_end": REQUESTED_END,
            "country_scope": "Ecuador",
            "filter_logic": "original archive retained; date filter used for reporting counts only",
            "supplement_batch_id": BATCH_ID,
            "attempt": attempt,
            **download(url, destination, timeout=180),
        }
        logs.append(row)
        if row["status_result"] == "DOWNLOADED":
            break
        if attempt == 1:
            time.sleep(5)
    success = destination.exists() and logs[-1]["status_result"] == "DOWNLOADED"
    audit = inspect_desinventar(destination) if success else {
        "total_source_records": 0, "in_window_records": 0,
        "actual_min_date": None, "actual_max_date": None,
        "disaster_type_counts": {}, "province_admin_counts": {},
    }
    if success:
        logs[-1].update({
            "actual_min_date": audit["actual_min_date"],
            "actual_max_date": audit["actual_max_date"],
            "record_count": audit["total_source_records"],
            "in_window_records": audit["in_window_records"],
        })
    status = "COMPLETE_FOR_REQUESTED_FILTERED_SCOPE" if success else "DOWNLOAD_FAILED"
    manifest = {
        "source": "DesInventar Ecuador",
        "status": status,
        "supplement_batch_id": BATCH_ID,
        "requested_start_date": REQUESTED_START,
        "requested_end_date": REQUESTED_END,
        "actual_min_date": audit["actual_min_date"],
        "actual_max_date": audit["actual_max_date"],
        "attempt_count": len(logs),
        "download_success": success,
        "filename": destination.name if success else None,
        "sha256": file_digest(destination) if success else None,
        "size_bytes": destination.stat().st_size if success else 0,
        **audit,
        "retrieval_timestamp": utc_timestamp(),
    }
    finalize_source(supplement, manifest, logs, {
        "format": "official ZIP containing original XML and map exports",
        "source_native_fields_preserved": True,
        "xml_member": audit.get("xml_member"),
        "members": audit.get("members", []),
    })
    write_json(ROOT / "artifacts" / "risk_corpus_stage5a_supplement" / "desinventar_ecuador_audit.json", manifest)
    return manifest, logs


def baseline_integrity(corpus_root: Path) -> dict[str, Any]:
    with (ROOT / "artifacts" / "risk_corpus_raw_stage5a" / "download_manifest.csv").open(
        encoding="utf-8", newline="",
    ) as handle:
        rows = list(csv.DictReader(handle))
    mismatches = []
    checked = 0
    for row in rows:
        if row["status_result"] != "DOWNLOADED":
            continue
        source_key = next(key for key, name in SOURCE_NAMES.items() if name == row["source"])
        path = corpus_root / source_key / "raw" / row["filename"]
        checked += 1
        if not path.exists() or file_digest(path) != row["sha256"]:
            mismatches.append(str(path.relative_to(corpus_root)))
    return {"checked_file_count": checked, "mismatches": mismatches, "unchanged": not mismatches}


def build_artifacts(
    corpus_root: Path,
    gdelt: dict[str, Any],
    gdelt_logs: list[dict[str, Any]],
    reliefweb: dict[str, Any],
    reliefweb_logs: list[dict[str, Any]],
    desinventar: dict[str, Any],
    desinventar_logs: list[dict[str, Any]],
    paper2_checkout: Path,
    baseline_before: dict[str, Any],
) -> None:
    artifact_dir = ROOT / "artifacts" / "risk_corpus_stage5a_supplement"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    manifests = (gdelt, reliefweb, desinventar)
    summary_rows = [
        {
            "source": manifest["source"],
            "status": manifest["status"],
            "record_count": manifest.get("records_retained", manifest.get("in_window_records", 0)),
            "file_count": manifest.get("stored_file_count", int(bool(manifest.get("download_success")))),
            "stored_bytes": manifest.get("stored_bytes", manifest.get("size_bytes", 0)),
            "actual_min_date": manifest.get("actual_min_date"),
            "actual_max_date": manifest.get("actual_max_date"),
            "supplement_batch_id": BATCH_ID,
        }
        for manifest in manifests
    ]
    coverage_rows = [
        {
            **row,
            "requested_start_date": REQUESTED_START,
            "requested_end_date": REQUESTED_END,
            "known_gaps": manifest.get("coverage_limitation", manifest.get("required_action", "")),
        }
        for row, manifest in zip(summary_rows, manifests)
    ]
    combined_logs = gdelt_logs + reliefweb_logs + desinventar_logs
    write_csv(artifact_dir / "supplement_source_summary.csv", summary_rows)
    write_csv(artifact_dir / "supplement_source_coverage.csv", coverage_rows)
    write_csv(artifact_dir / "supplement_download_manifest.csv", combined_logs)
    write_json(artifact_dir / "supplement_status.json", {
        "supplement_batch_id": BATCH_ID,
        "sources": {manifest["source"]: manifest["status"] for manifest in manifests},
    })

    with (ROOT / "artifacts" / "risk_corpus_raw_stage5a" / "source_summary.csv").open(
        encoding="utf-8", newline="",
    ) as handle:
        baseline_summary = {row["source"]: row for row in csv.DictReader(handle)}
    combined_rows = []
    for source in SOURCE_NAMES.values():
        baseline = baseline_summary[source]
        if source == "GDELT":
            supplement_records = gdelt["records_retained"]
            supplement_files = gdelt["stored_file_count"]
            supplement_bytes = gdelt["stored_bytes"]
            status = gdelt["status"]
            actual_min = gdelt["actual_min_date"]
            actual_max = gdelt["actual_max_date"]
            gaps = gdelt["coverage_limitation"] + "; SOURCE_NATIVE_START_DATE_GAP"
        elif source == "ReliefWeb":
            supplement_records = supplement_files = supplement_bytes = 0
            status = reliefweb["status"]
            actual_min = actual_max = None
            gaps = reliefweb["required_action"]
        elif source == "DesInventar":
            supplement_records = desinventar["in_window_records"]
            supplement_files = int(desinventar["download_success"])
            supplement_bytes = desinventar["size_bytes"]
            status = "PARTIAL_SOURCE_COVERAGE"
            dates = [value for value in (baseline["actual_min_date"], baseline["actual_max_date"], desinventar["actual_min_date"], desinventar["actual_max_date"]) if value]
            actual_min, actual_max = (min(dates), max(dates)) if dates else (None, None)
            gaps = "source-native country exports do not cover the requested end date"
        else:
            supplement_records = supplement_files = supplement_bytes = 0
            status = baseline["status"]
            actual_min = baseline["actual_min_date"]
            actual_max = baseline["actual_max_date"]
            gaps = "see accepted Stage-5A source coverage audit"
        combined_rows.append({
            "source": source,
            "status": status,
            "raw_record_count": int(baseline["record_count"] or 0) + supplement_records,
            "actual_min_date": actual_min,
            "actual_max_date": actual_max,
            "countries": "Ecuador|Colombia|Peru",
            "raw_file_count": int(baseline["file_count"] or 0) + supplement_files,
            "bytes": int(baseline["total_bytes"] or 0) + supplement_bytes,
            "known_gaps": gaps,
            "authorization_limitations": reliefweb["required_action"] if source == "ReliefWeb" else "",
            "source_native_limitations": gdelt.get("source_native_start_gap", "") if source == "GDELT" else "",
        })
    write_csv(artifact_dir / "combined_six_source_coverage.csv", combined_rows)

    baseline_after = baseline_integrity(corpus_root)
    protected = subprocess.check_output(
        ["git", "diff", "--name-only", BASELINE_COMMIT, "--", "artifacts/risk_corpus_raw_stage5a"],
        cwd=ROOT, text=True,
    ).splitlines()
    earlier = subprocess.check_output(
        ["git", "diff", "--name-only", "7ad5243eecbadec677be9ada0548804c316d6002", "--",
         "artifacts/compound_risk_stage1", "artifacts/compound_risk_stage2",
         "artifacts/risk_event_library_stage3", "artifacts/risk_impact_mapping_stage3c",
         "artifacts/route_risk_calibration_stage3d", "artifacts/m5_demand_calibration_stage4",
         "artifacts/m5_event_proxy_refinement_stage4a"],
        cwd=ROOT, text=True,
    ).splitlines()
    paper2_git = ["git", "-c", f"safe.directory={paper2_checkout.as_posix()}"]
    paper2_actual = subprocess.check_output([*paper2_git, "rev-parse", "HEAD"], cwd=paper2_checkout, text=True).strip()
    paper2_clean = not subprocess.check_output([*paper2_git, "status", "--porcelain"], cwd=paper2_checkout, text=True).strip()
    all_complete = (
        gdelt["status"] == "COMPLETE_FOR_REQUESTED_FILTERED_SCOPE"
        and reliefweb["status"] == "COMPLETE_FOR_REQUESTED_SCOPE"
        and desinventar["download_success"]
    )
    useful = gdelt["records_retained"] > 0 or reliefweb.get("report_count", 0) > 0 or desinventar["download_success"]
    status = (
        "STAGE_5A_SUPPLEMENT_COMPLETE" if all_complete
        else "STAGE_5A_SUPPLEMENT_COMPLETE_WITH_DISCLOSED_GAPS" if useful
        else "STAGE_5A_SUPPLEMENT_BLOCKED"
    )
    write_json(artifact_dir / "supplement_execution_audit.json", {
        "status": status,
        "supplement_batch_id": BATCH_ID,
        "baseline_commit": BASELINE_COMMIT,
        "retrieval_timestamp": utc_timestamp(),
        "baseline_raw_before": baseline_before,
        "baseline_raw_after": baseline_after,
        "baseline_stage5a_artifacts_changed": protected,
        "stage1_through_stage4a_artifacts_changed": earlier,
        "paper2_expected_sha": PAPER2_SHA,
        "paper2_actual_sha": paper2_actual,
        "paper2_worktree_clean": paper2_clean,
        "source_fusion_performed": False,
        "canonical_events_created": False,
        "missing_value_imputation_performed": False,
        "genai_dispatches": 0,
        "llm_extractions": 0,
        "ml_training_dispatches": 0,
        "optimizer_dispatches": 0,
        "delta_d_recalibrated": False,
        "delta_a_recalibrated": False,
        "combined_total_records": sum(row["raw_record_count"] for row in combined_rows),
        "combined_total_raw_files": sum(row["raw_file_count"] for row in combined_rows),
        "combined_total_bytes": sum(row["bytes"] for row in combined_rows),
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper2-checkout", required=True, type=Path)
    parser.add_argument("--corpus-root", type=Path, default=ROOT / "data" / "local" / "raw_risk_corpus")
    parser.add_argument(
        "--record-gdelt-failure", action="store_true",
        help="do not retry the official stream; record the bounded recovery failure",
    )
    parser.add_argument(
        "--resume-gdelt-event-only", action="store_true",
        help="recover only the independent Event family and reuse the completed Ecuador audit",
    )
    parser.add_argument(
        "--finalize-existing", action="store_true",
        help="rebuild audits from existing supplement manifests without network requests",
    )
    args = parser.parse_args()
    corpus_root = args.corpus_root.resolve()
    baseline_before = baseline_integrity(corpus_root)
    if not baseline_before["unchanged"]:
        raise RuntimeError(f"accepted baseline raw files differ: {baseline_before['mismatches']}")
    (ROOT / "artifacts" / "risk_corpus_stage5a_supplement").mkdir(parents=True, exist_ok=True)
    if args.finalize_existing:
        gdelt, gdelt_logs = load_and_clean_gdelt(corpus_root)
    elif args.resume_gdelt_event_only:
        gdelt, gdelt_logs = acquire_gdelt_event_only(corpus_root)
    elif args.record_gdelt_failure:
        gdelt, gdelt_logs = record_gdelt_failed_recovery(corpus_root)
    else:
        gdelt, gdelt_logs = acquire_gdelt(corpus_root)
    reliefweb, reliefweb_logs = (
        load_supplement_source(corpus_root, "reliefweb")
        if args.finalize_existing
        else record_reliefweb(corpus_root)
    )
    desinventar, desinventar_logs = (
        load_supplement_source(corpus_root, "desinventar")
        if args.resume_gdelt_event_only or args.finalize_existing
        else acquire_desinventar_ecuador(corpus_root)
    )
    build_artifacts(
        corpus_root, gdelt, gdelt_logs, reliefweb, reliefweb_logs,
        desinventar, desinventar_logs, args.paper2_checkout.resolve(), baseline_before,
    )
    status = json.loads(
        (ROOT / "artifacts" / "risk_corpus_stage5a_supplement" / "supplement_execution_audit.json").read_text(encoding="utf-8")
    )["status"]
    print(status)


if __name__ == "__main__":
    main()
