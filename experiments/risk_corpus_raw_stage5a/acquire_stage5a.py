"""Acquire a source-separated six-source historical risk corpus."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
import zipfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ai_risk_trigger_inventory.risk_corpus.raw_acquisition import (
    REQUESTED_END,
    REQUESTED_START,
    TARGET_BBOX,
    TARGET_COUNTRIES,
    download_bytes,
    finalize_source,
    json_record_count,
    schema_snapshot,
    utc_timestamp,
    write_csv,
    write_json,
)


SOURCES = ("gdelt", "reliefweb", "gdacs", "desinventar", "usgs", "copernicus")
STAGE4A_COMMIT = "7ad5243eecbadec677be9ada0548804c316d6002"
PAPER2_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
GDACS_TYPES = ("EQ", "FL", "TC", "DR", "WF", "VO")
RISK_QUERY_FAMILIES = {
    "natural_hazards": "(flood OR landslide OR earthquake OR severe weather OR emergency)",
    "transport_supply": '("road closure" OR "highway closure" OR "transport disruption" OR "logistics disruption" OR "supply disruption" OR shortage)',
    "demand_surge": '("demand surge" OR "panic buying" OR promotion OR sale OR "shopping surge" OR "major event")',
}


def parse_json(payload: bytes) -> Any:
    return json.loads(payload.decode("utf-8-sig"))


def iso_from_epoch_millis(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def event_date(properties: dict[str, Any]) -> str | None:
    for key in ("fromdate", "eventtime", "datetime", "todate", "date"):
        value = properties.get(key)
        if value:
            return str(value)
    return None


def point_in_target(geometry: Any) -> bool:
    if not isinstance(geometry, dict):
        return False
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "Point" and isinstance(coordinates, list) and len(coordinates) >= 2:
        longitude, latitude = coordinates[:2]
        return (
            TARGET_BBOX["minlongitude"] <= longitude <= TARGET_BBOX["maxlongitude"]
            and TARGET_BBOX["minlatitude"] <= latitude <= TARGET_BBOX["maxlatitude"]
        )
    return False


def gdacs_target(feature: dict[str, Any]) -> bool:
    properties = feature.get("properties", {})
    searchable = " ".join(
        str(properties.get(key, ""))
        for key in ("country", "countryname", "affectedcountries", "name", "description")
    ).lower()
    return any(country.lower() in searchable for country in TARGET_COUNTRIES) or point_in_target(
        feature.get("geometry")
    )


def copernicus_target(record: dict[str, Any]) -> bool:
    countries = record.get("countries", [])
    names = []
    for country in countries:
        names.append(country.get("short_name", "") if isinstance(country, dict) else str(country))
    return any(name in TARGET_COUNTRIES for name in names)


def collect_usgs(root: Path) -> dict[str, Any]:
    source_dir = root / "usgs"
    raw = source_dir / "raw"
    logs: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    schema: dict[str, Any] = {}
    for year in range(2015, 2027):
        end = f"{year}-12-31T23:59:59" if year < 2026 else "2026-08-31T23:59:59"
        parameters = {
            "format": "geojson", "starttime": f"{year}-01-01T00:00:00", "endtime": end,
            **TARGET_BBOX, "orderby": "time-asc",
        }
        log, payload = download_bytes(
            source="USGS", endpoint="https://earthquake.usgs.gov/fdsnws/event/1/query",
            parameters=parameters, destination=raw / f"usgs_{year}.geojson",
        )
        if payload is not None:
            value = parse_json(payload)
            log["record_count"] = json_record_count(value)
            events.extend(value.get("features", []))
            if not schema:
                schema = schema_snapshot(value)
        logs.append(log)
    dates = sorted(filter(None, (iso_from_epoch_millis(e.get("properties", {}).get("time")) for e in events)))
    magnitudes = [e.get("properties", {}).get("mag") for e in events]
    magnitudes = [float(value) for value in magnitudes if value is not None]
    success = [row for row in logs if row["status_result"] == "DOWNLOADED"]
    status = "COMPLETE_FOR_REQUESTED_SCOPE" if len(success) == 12 else (
        "PARTIAL_SOURCE_COVERAGE" if success else "DOWNLOAD_FAILED"
    )
    manifest = {
        "source": "USGS", "status": status,
        "requested_start_date": REQUESTED_START, "requested_end_date": REQUESTED_END,
        "actual_min_date": dates[0] if dates else None, "actual_max_date": dates[-1] if dates else None,
        "coverage_gaps": [] if status == "COMPLETE_FOR_REQUESTED_SCOPE" else ["one or more annual queries failed"],
        "retrieval_timestamp": utc_timestamp(), "spatial_filter": TARGET_BBOX,
        "event_count": len(events), "magnitude_min": min(magnitudes) if magnitudes else None,
        "magnitude_max": max(magnitudes) if magnitudes else None,
        "files": len(success), "bytes": sum(row["size_bytes"] for row in success),
        "inference_exclusions": ["road impact", "transport disruption", "model severity class"],
    }
    finalize_source(source_dir, manifest, logs, schema)
    return manifest


def collect_gdacs(root: Path) -> dict[str, Any]:
    source_dir = root / "gdacs"
    raw = source_dir / "raw"
    logs: list[dict[str, Any]] = []
    all_features: list[dict[str, Any]] = []
    schema: dict[str, Any] = {}
    endpoint = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
    for country in TARGET_COUNTRIES:
        country_slug = country.lower()
        for year in range(2015, 2027):
            end = f"{year}-12-31" if year < 2026 else "2026-08-31"
            page = 1
            while True:
                parameters = {
                    "eventlist": ";".join(GDACS_TYPES),
                    "fromdate": f"{year}-01-01", "todate": end,
                    "country": country, "alertlevel": "green;orange;red",
                    "pagesize": 100, "pagenumber": page,
                }
                log, payload = download_bytes(
                    source="GDACS", endpoint=endpoint, parameters=parameters,
                    destination=raw / f"gdacs_{country_slug}_{year}_p{page:03d}.json",
                )
                logs.append(log)
                if payload is None:
                    break
                if not payload.strip():
                    # GDACS uses HTTP 204/empty bodies for event-type windows
                    # with no records. Preserve the zero-byte response as-is.
                    log["record_count"] = 0
                    break
                try:
                    value = parse_json(payload)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    log["status_result"] = "INVALID_NON_JSON_RESPONSE"
                    log["error"] = f"{type(error).__name__}: {error}"
                    break
                features = value.get("features", [])
                log["record_count"] = len(features)
                all_features.extend(features)
                if not schema:
                    schema = schema_snapshot(value)
                if len(features) < 100:
                    break
                page += 1
                time.sleep(0.25)
            time.sleep(0.10)
    target = [feature for feature in all_features if gdacs_target(feature)]
    keys = [
        (
            feature.get("properties", {}).get("eventtype"),
            feature.get("properties", {}).get("eventid"),
            feature.get("properties", {}).get("episodeid"),
        )
        for feature in target
    ]
    unique: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for key, feature in zip(keys, target):
        unique.setdefault(key, feature)
    duplicate_count = len(target) - len(unique)
    enrichment = {
        key: feature for key, feature in unique.items()
        if str(feature.get("properties", {}).get("alertlevel", "")).lower()
        in {"orange", "red"}
    }
    detail_success = 0
    for event_type, event_id, episode_id in sorted(enrichment, key=lambda key: tuple(str(v) for v in key)):
        detail_log, detail_payload = download_bytes(
            source="GDACS",
            endpoint="https://www.gdacs.org/gdacsapi/api/events/geteventdata",
            parameters={"eventtype": event_type, "eventid": event_id},
            destination=raw / f"detail_{event_type}_{event_id}.json",
        )
        logs.append(detail_log)
        detail_success += int(detail_payload is not None)
        polygon_log, _ = download_bytes(
            source="GDACS",
            endpoint="https://www.gdacs.org/gdacsapi/api/polygons/getgeometry",
            parameters={"eventtype": event_type, "eventid": event_id, "episodeid": episode_id},
            destination=raw / f"geometry_{event_type}_{event_id}_{episode_id}.json",
        )
        logs.append(polygon_log)
        time.sleep(0.10)
    dates = sorted(filter(None, (event_date(feature.get("properties", {})) for feature in unique.values())))
    list_success = [row for row in logs if row["filename"].startswith("gdacs_") and row["status_result"] == "DOWNLOADED"]
    status = "PARTIAL_SOURCE_COVERAGE" if list_success else "DOWNLOAD_FAILED"
    manifest = {
        "source": "GDACS", "status": status,
        "requested_start_date": REQUESTED_START, "requested_end_date": REQUESTED_END,
        "actual_min_date": dates[0] if dates else None, "actual_max_date": dates[-1] if dates else None,
        "coverage_gaps": [
            "country-filtered official event lists cover all requested alert levels",
            "separate event detail/geometry enrichment is limited deterministically to orange/red alerts to respect API load",
        ] if list_success else ["country-filtered discovery requests failed"],
        "retrieval_timestamp": utc_timestamp(), "event_types": list(GDACS_TYPES),
        "spatial_filter": {"countries": list(TARGET_COUNTRIES), "fallback_bbox": TARGET_BBOX},
        "downloaded_country_filtered_discovery_records": len(all_features),
        "target_event_count_before_exact_id_deduplication": len(target),
        "event_count": len(unique), "exact_api_duplicate_count": duplicate_count,
        "page_count": len(list_success), "elevated_alert_event_count": len(enrichment),
        "detail_count": detail_success,
        "files": sum(row["status_result"] == "DOWNLOADED" for row in logs),
        "bytes": sum(row["size_bytes"] for row in logs),
    }
    finalize_source(source_dir, manifest, logs, schema)
    return manifest


def collect_desinventar(root: Path) -> dict[str, Any]:
    source_dir = root / "desinventar"
    raw = source_dir / "raw"
    logs: list[dict[str, Any]] = []
    databases = {
        "Colombia": ("col", "1914", "2018"),
        "Ecuador": ("ecu", "1970", "2023"),
        "Peru": ("per", "1970", "2025"),
    }
    downloaded = []
    schemas = {}
    archive_audits: dict[str, dict[str, Any]] = {}
    for country, (code, available_start, available_end) in databases.items():
        destination = raw / f"DI_export_{code}.zip"
        log, payload = download_bytes(
            source="DesInventar",
            endpoint=f"https://www.desinventar.net/DesInventar/download/DI_export_{code}.zip",
            parameters=None, destination=destination, timeout=300,
        )
        log["country"] = country
        log["available_time_coverage"] = f"{available_start}-{available_end}"
        if payload is not None:
            audit = inspect_desinventar_archive(destination)
            log["record_count"] = audit["raw_event_record_count"]
            schemas[country] = audit
            archive_audits[country] = audit
            downloaded.append(country)
        logs.append(log)
    status = "PARTIAL_SOURCE_COVERAGE" if downloaded else "DOWNLOAD_FAILED"
    gaps = [
        "country exports predate requested end 2026-08-31",
        "Ecuador archive download failed after the server interrupted the response; no partial file was retained",
        "downloaded archive records reach 2018-02-23 for Colombia and 2015-12-31 for Peru",
        "raw ZIP/XML exports are retained without missing-field inference",
    ]
    scoped_dates = [
        value
        for audit in archive_audits.values()
        for value in (audit["in_scope_min_date"], audit["in_scope_max_date"])
        if value is not None
    ]
    manifest = {
        "source": "DesInventar", "status": status,
        "requested_start_date": REQUESTED_START, "requested_end_date": REQUESTED_END,
        "actual_min_date": min(scoped_dates) if scoped_dates else None,
        "actual_max_date": max(scoped_dates) if scoped_dates else None,
        "coverage_gaps": gaps, "retrieval_timestamp": utc_timestamp(),
        "countries_requested": list(databases), "countries_downloaded": downloaded,
        "file_format": "official ZIP containing original XML/map exports",
        "source_organization": "DesInventar/UNDRR and contributing national institutions",
        "raw_event_record_count": sum(audit["raw_event_record_count"] for audit in archive_audits.values()),
        "in_scope_event_record_count": sum(audit["in_scope_event_record_count"] for audit in archive_audits.values()),
        "record_count": sum(audit["in_scope_event_record_count"] for audit in archive_audits.values()),
        "country_archive_audits": archive_audits,
        "files": len(downloaded), "bytes": sum(row["size_bytes"] for row in logs),
    }
    finalize_source(source_dir, manifest, logs, schemas)
    return manifest


def inspect_desinventar_archive(path: Path) -> dict[str, Any]:
    """Read source dates without extracting or modifying the official archive."""
    with zipfile.ZipFile(path) as archive:
        members = archive.namelist()
        xml_member = next(member for member in members if member.lower().endswith(".xml"))
        record_count = 0
        dated_count = 0
        all_dates: list[str] = []
        scoped_dates: list[str] = []
        in_fichas = False
        with archive.open(xml_member) as handle:
            for action, element in ET.iterparse(handle, events=("start", "end")):
                if action == "start" and element.tag == "fichas":
                    in_fichas = True
                elif action == "end" and element.tag == "TR" and in_fichas:
                    record_count += 1
                    values = {child.tag: (child.text or "").strip() for child in element}
                    try:
                        year = int(values["fechano"])
                        month = max(1, int(values.get("fechames") or 1))
                        day = max(1, int(values.get("fechadia") or 1))
                        date = datetime(year, month, day).date().isoformat()
                    except (KeyError, TypeError, ValueError):
                        date = None
                    if date is not None:
                        dated_count += 1
                        all_dates.append(date)
                        if REQUESTED_START[:10] <= date <= REQUESTED_END[:10]:
                            scoped_dates.append(date)
                    element.clear()
                elif action == "end" and element.tag == "fichas":
                    in_fichas = False
                    element.clear()
        return {
            "format": "ZIP",
            "members": members,
            "xml_member": xml_member,
            "raw_event_record_count": record_count,
            "dated_event_record_count": dated_count,
            "raw_min_date": min(all_dates) if all_dates else None,
            "raw_max_date": max(all_dates) if all_dates else None,
            "in_scope_event_record_count": len(scoped_dates),
            "in_scope_min_date": min(scoped_dates) if scoped_dates else None,
            "in_scope_max_date": max(scoped_dates) if scoped_dates else None,
        }


def refresh_desinventar_metadata(corpus_root: Path) -> None:
    """Reconcile derived archive metadata after an interrupted/resumed download."""
    source_dir = corpus_root / "desinventar"
    manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
    with (source_dir / "download_log.csv").open(encoding="utf-8", newline="") as handle:
        logs = list(csv.DictReader(handle))
    audits: dict[str, dict[str, Any]] = {}
    for row in logs:
        path = source_dir / "raw" / row["filename"]
        if row["status_result"] == "DOWNLOADED" and path.exists():
            audit = inspect_desinventar_archive(path)
            audits[row["country"]] = audit
            row["record_count"] = audit["raw_event_record_count"]
    scoped_dates = [
        value
        for audit in audits.values()
        for value in (audit["in_scope_min_date"], audit["in_scope_max_date"])
        if value is not None
    ]
    manifest.update({
        "actual_min_date": min(scoped_dates) if scoped_dates else None,
        "actual_max_date": max(scoped_dates) if scoped_dates else None,
        "raw_event_record_count": sum(audit["raw_event_record_count"] for audit in audits.values()),
        "in_scope_event_record_count": sum(audit["in_scope_event_record_count"] for audit in audits.values()),
        "record_count": sum(audit["in_scope_event_record_count"] for audit in audits.values()),
        "country_archive_audits": audits,
        "coverage_gaps": [
            "country exports predate requested end 2026-08-31",
            "Ecuador archive download failed after the server interrupted the response; no partial file was retained",
            "downloaded archive records reach 2018-02-23 for Colombia and 2015-12-31 for Peru",
            "raw ZIP/XML exports are retained without missing-field inference",
        ],
    })
    finalize_source(source_dir, manifest, logs, audits)


def collect_copernicus(root: Path) -> dict[str, Any]:
    source_dir = root / "copernicus"
    raw = source_dir / "raw"
    logs: list[dict[str, Any]] = []
    schema: dict[str, Any] = {}
    selected: list[tuple[str, dict[str, Any]]] = []
    services = {
        "rapid": "https://mapping.emergency.copernicus.eu/activations/api/activations/",
        "risk_recovery": "https://riskandrecovery.emergency.copernicus.eu/api/public-activations/",
    }
    for service, endpoint in services.items():
        log, payload = download_bytes(
            source="Copernicus EMS", endpoint=endpoint, parameters={"limit": 2000},
            destination=raw / f"{service}_activation_list.json",
        )
        logs.append(log)
        if payload is None:
            continue
        value = parse_json(payload)
        records = value.get("results", [])
        log["record_count"] = len(records)
        schema[service] = schema_snapshot(value)
        for record in records:
            date = str(record.get("eventTime") or record.get("activationTime") or "")
            if copernicus_target(record) and "2015-01-01" <= date[:10] <= "2026-08-31":
                selected.append((service, record))
    detail_count = 0
    for service, record in selected:
        code = record.get("code")
        if service == "rapid":
            endpoint = f"{services[service]}{code}/"
            parameters = None
        else:
            endpoint = services[service]
            parameters = {"code": code}
        log, payload = download_bytes(
            source="Copernicus EMS", endpoint=endpoint, parameters=parameters,
            destination=raw / f"{service}_{code}.json",
        )
        logs.append(log)
        detail_count += int(payload is not None)
    dates = sorted(
        str(record.get("eventTime") or record.get("activationTime"))
        for _, record in selected if record.get("eventTime") or record.get("activationTime")
    )
    list_success = sum(row["filename"].endswith("activation_list.json") and row["status_result"] == "DOWNLOADED" for row in logs)
    status = "COMPLETE_FOR_REQUESTED_SCOPE" if list_success == 2 and detail_count == len(selected) else (
        "PARTIAL_SOURCE_COVERAGE" if list_success else "DOWNLOAD_FAILED"
    )
    categories = sorted({
        (record.get("category", {}).get("name") if isinstance(record.get("category"), dict) else record.get("category"))
        for _, record in selected if record.get("category")
    })
    manifest = {
        "source": "Copernicus EMS", "status": status,
        "requested_start_date": REQUESTED_START, "requested_end_date": REQUESTED_END,
        "actual_min_date": dates[0] if dates else None, "actual_max_date": dates[-1] if dates else None,
        "coverage_gaps": [] if status == "COMPLETE_FOR_REQUESTED_SCOPE" else ["one or more activation list/detail requests failed"],
        "retrieval_timestamp": utc_timestamp(), "geographic_filter": list(TARGET_COUNTRIES),
        "activation_count": len(selected),
        "rapid_mapping_count": sum(service == "rapid" for service, _ in selected),
        "risk_recovery_count": sum(service == "risk_recovery" for service, _ in selected),
        "categories": categories, "detail_count": detail_count,
        "files": sum(row["status_result"] == "DOWNLOADED" for row in logs),
        "bytes": sum(row["size_bytes"] for row in logs),
        "raster_payloads_downloaded": 0,
    }
    finalize_source(source_dir, manifest, logs, schema)
    return manifest


def record_reliefweb_block(root: Path) -> dict[str, Any]:
    source_dir = root / "reliefweb"
    planned = {
        "reports_endpoint": "https://api.reliefweb.int/v2/reports",
        "disasters_endpoint": "https://api.reliefweb.int/v2/disasters",
        "required_action": "register and supply a pre-approved ReliefWeb API appname",
        "planned_filters": {
            "countries": list(TARGET_COUNTRIES),
            "date": {"from": "2015-01-01", "to": "2026-08-31"},
            "risk_concepts": list(RISK_QUERY_FAMILIES.values()),
            "pagination_limit": 1000,
        },
    }
    logs = [{
        "source": "ReliefWeb", "filename": "", "source_url": planned["reports_endpoint"],
        "query_parameters": json.dumps(planned["planned_filters"], sort_keys=True),
        "download_timestamp": utc_timestamp(), "http_status": None,
        "status_result": "AUTH_BLOCKED", "size_bytes": 0, "sha256": "",
        "record_count": 0, "error": "pre-approved appname not supplied; request not bypassed",
    }]
    manifest = {
        "source": "ReliefWeb", "status": "AUTH_BLOCKED",
        "requested_start_date": REQUESTED_START, "requested_end_date": REQUESTED_END,
        "actual_min_date": None, "actual_max_date": None,
        "coverage_gaps": ["entire requested window pending approved appname"],
        "retrieval_timestamp": utc_timestamp(), "record_count": 0,
        "auth_state": "RELIEFWEB_AUTH_BLOCKED", **planned,
        "files": 0, "bytes": 0,
    }
    finalize_source(source_dir, manifest, logs, {"status": "not retrieved due to required pre-approved appname"})
    return manifest


def record_gdelt_limitation(root: Path) -> dict[str, Any]:
    source_dir = root / "gdelt"
    raw = source_dir / "raw"
    query = f"({' OR '.join(TARGET_COUNTRIES)}) {RISK_QUERY_FAMILIES['natural_hazards']}"
    parameters = {
        "query": query, "mode": "artlist", "maxrecords": 250, "format": "json",
        "startdatetime": "20170101000000", "enddatetime": "20170331235959",
        "sort": "datedesc",
    }
    log, payload = download_bytes(
        source="GDELT", endpoint="https://api.gdeltproject.org/api/v2/doc/doc",
        parameters=parameters, destination=raw / "gdelt_probe_2017q1.json", timeout=90,
    )
    logs = [log]
    records = 0
    schema: dict[str, Any] = {}
    if payload is not None:
        value = parse_json(payload)
        records = json_record_count(value) or 0
        log["record_count"] = records
        schema = schema_snapshot(value)
    status = "PARTIAL_SOURCE_COVERAGE" if records else "API_LIMITATION"
    gaps = [
        "GDELT DOC API documented historical coverage begins in 2017, leaving 2015-2016 unavailable through this interface",
        "ArticleList returns at most 250 records per query and offers no complete historical pagination",
    ]
    if log["http_status"] == 429:
        gaps.append("official API returned HTTP 429 requiring no more than one request per five seconds; shared-client throttling prevented acquisition")
    manifest = {
        "source": "GDELT", "status": status,
        "requested_start_date": REQUESTED_START, "requested_end_date": REQUESTED_END,
        "actual_min_date": None, "actual_max_date": None,
        "coverage_gaps": gaps, "retrieval_timestamp": utc_timestamp(),
        "record_count": records, "geographic_query": list(TARGET_COUNTRIES),
        "query_families": RISK_QUERY_FAMILIES,
        "planned_safe_windows": "quarterly from 2017-01-01 through 2026-08-31",
        "rate_limit_behavior": log.get("error", "") or "single official API probe",
        "duplicates": 0, "files": int(payload is not None),
        "bytes": log["size_bytes"],
        "global_archive_downloaded": False,
    }
    finalize_source(source_dir, manifest, logs, schema or {"status": "no response schema available"})
    return manifest


def build_artifacts(corpus_root: Path, paper2_checkout: Path) -> None:
    refresh_desinventar_metadata(corpus_root)
    manifests = [
        json.loads((corpus_root / source / "manifest.json").read_text(encoding="utf-8"))
        for source in SOURCES
    ]
    artifact_dir = ROOT / "artifacts" / "risk_corpus_raw_stage5a"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    source_rows = []
    coverage_rows = []
    download_rows = []
    for manifest in manifests:
        source_rows.append({
            "source": manifest["source"], "status": manifest["status"],
            "record_count": manifest.get("event_count", manifest.get("activation_count", manifest.get("record_count"))),
            "file_count": manifest.get("files", 0), "total_bytes": manifest.get("bytes", 0),
            "actual_min_date": manifest.get("actual_min_date"), "actual_max_date": manifest.get("actual_max_date"),
        })
        coverage_rows.append({
            "source": manifest["source"], "requested_start_date": REQUESTED_START,
            "requested_end_date": REQUESTED_END, "actual_min_date": manifest.get("actual_min_date"),
            "actual_max_date": manifest.get("actual_max_date"),
            "coverage_gaps": json.dumps(manifest.get("coverage_gaps", []), ensure_ascii=False),
            "status": manifest["status"],
        })
    for source in SOURCES:
        log_path = corpus_root / source / "download_log.csv"
        with log_path.open(encoding="utf-8", newline="") as handle:
            download_rows.extend(csv.DictReader(handle))
    write_csv(artifact_dir / "source_summary.csv", source_rows)
    write_csv(artifact_dir / "source_coverage.csv", coverage_rows)
    write_csv(artifact_dir / "download_manifest.csv", download_rows)
    write_json(artifact_dir / "source_status.json", {
        "requested_start_date": REQUESTED_START, "requested_end_date": REQUESTED_END,
        "sources": {manifest["source"]: manifest["status"] for manifest in manifests},
    })
    successful_files = [row for row in download_rows if row.get("status_result") == "DOWNLOADED"]
    protected_paths = (
        "artifacts/compound_risk_stage1",
        "artifacts/compound_risk_stage2",
        "artifacts/risk_event_library_stage3",
        "artifacts/risk_impact_mapping_stage3c",
        "artifacts/route_risk_calibration_stage3d",
        "artifacts/m5_demand_calibration_stage4",
        "artifacts/m5_event_proxy_refinement_stage4a",
    )
    changed_paths = subprocess.check_output(
        ["git", "diff", "--name-only", STAGE4A_COMMIT, "--", *protected_paths],
        cwd=ROOT, text=True,
    ).splitlines()
    paper2_git = ["git", "-c", f"safe.directory={paper2_checkout.as_posix()}"]
    paper2_sha = subprocess.check_output(
        [*paper2_git, "rev-parse", "HEAD"], cwd=paper2_checkout, text=True,
    ).strip()
    paper2_clean = not bool(subprocess.check_output(
        [*paper2_git, "status", "--porcelain"], cwd=paper2_checkout, text=True,
    ).strip())
    write_json(artifact_dir / "execution_audit.json", {
        "status": "STAGE_5A_RAW_CORPUS_ACQUISITION_COMPLETE_WITH_DISCLOSED_GAPS",
        "retrieval_timestamp": utc_timestamp(),
        "source_count": 6, "total_raw_files": len(successful_files),
        "total_raw_bytes": sum(int(row.get("size_bytes") or 0) for row in successful_files),
        "total_records_reported": sum(int(row.get("record_count") or 0) for row in source_rows),
        "source_fusion_performed": False, "missing_value_imputation_performed": False,
        "canonical_events_created": False, "genai_dispatches": 0,
        "optimizer_dispatches": 0, "ml_training_dispatches": 0,
        "stage4a_base_commit": STAGE4A_COMMIT,
        "prior_stage_artifacts_changed": changed_paths,
        "prior_stage_artifacts_unchanged": not changed_paths,
        "paper2_expected_sha": PAPER2_SHA,
        "paper2_actual_sha": paper2_sha,
        "paper2_worktree_clean": paper2_clean,
        "raw_root": str(corpus_root),
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "local" / "raw_risk_corpus")
    parser.add_argument("--paper2-checkout", required=True, type=Path)
    parser.add_argument(
        "--summarize-only", action="store_true",
        help="rebuild committed audit artifacts from existing source manifests without network access",
    )
    args = parser.parse_args()
    corpus_root = args.output_dir.resolve()
    for source in SOURCES:
        (corpus_root / source / "raw").mkdir(parents=True, exist_ok=True)

    if not args.summarize_only:
        record_gdelt_limitation(corpus_root)
        record_reliefweb_block(corpus_root)
        collect_gdacs(corpus_root)
        collect_desinventar(corpus_root)
        collect_usgs(corpus_root)
        collect_copernicus(corpus_root)
    build_artifacts(corpus_root, args.paper2_checkout.resolve())
    print("STAGE_5A_RAW_CORPUS_ACQUISITION_COMPLETE_WITH_DISCLOSED_GAPS")


if __name__ == "__main__":
    main()
