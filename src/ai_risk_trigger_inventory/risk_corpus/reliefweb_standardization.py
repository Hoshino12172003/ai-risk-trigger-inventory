"""Read-only ReliefWeb audit and deterministic Stage 5B-1R standardization."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import io
import json
import math
from pathlib import Path
import re
from typing import Any, Iterator
import unicodedata
from urllib.parse import urlparse


DOWNLOAD_START = datetime(2015, 1, 1, tzinfo=timezone.utc)
DOWNLOAD_END = datetime(2026, 8, 31, 23, 59, 59, tzinfo=timezone.utc)
PAGE_SIZE = 1000
COUNTRY_FOLDER_TO_CODE = {"ecuador": "ecu", "colombia": "col", "peru": "per"}
TARGET_ISO3 = {"ECU", "COL", "PER"}
EXPECTED_TOTALS = {
    ("reports", "ecuador"): 9032,
    ("reports", "colombia"): 20501,
    ("reports", "peru"): 11312,
    ("disasters", "ecuador"): 16,
    ("disasters", "colombia"): 24,
    ("disasters", "peru"): 22,
}

REPORT_FIELDS = [
    "source_name", "source_record_type", "source_record_id", "source_uuid",
    "title", "body_text", "body_text_raw_hash", "body_html_present", "origin_url",
    "reliefweb_url", "url_alias", "date_original", "date_created", "date_changed",
    "primary_country_name", "primary_country_iso3", "country_names", "country_iso3_list",
    "target_country_match", "target_country_iso3_list", "target_geo_match_basis",
    "source_names", "source_shortnames", "source_types", "language_names",
    "language_codes", "theme_names", "format_names", "ocha_product_names",
    "disaster_ids", "disaster_names", "disaster_glides", "disaster_type_names",
    "disaster_type_codes", "vulnerable_group_names", "attachment_count",
    "attachment_urls", "attachment_filenames", "attachment_mimetypes",
    "download_country_membership", "download_copy_count", "source_files",
    "source_file_membership", "source_page_indices", "source_row_numbers",
    "source_copy_provenance", "source_endpoint",
    "source_query_endpoint", "raw_payload_hash", "quality_flags",
]

DISASTER_FIELDS = [
    "source_name", "source_record_type", "source_record_id", "source_uuid",
    "event_name", "event_description", "status", "glide", "event_date",
    "date_created", "date_changed", "primary_country_name", "primary_country_iso3",
    "country_names", "country_iso3_list", "target_country_match",
    "target_country_iso3_list", "target_geo_match_basis",
    "primary_disaster_type_name", "primary_disaster_type_code",
    "disaster_type_names", "disaster_type_codes", "reliefweb_url", "url_alias",
    "download_country_membership", "download_copy_count", "source_files",
    "source_file_membership", "source_page_indices", "source_row_numbers",
    "source_copy_provenance", "source_endpoint",
    "source_query_endpoint", "raw_payload_hash", "quality_flags",
]

REPORT_INDEX_FIELDS = [
    "source_name", "source_record_type", "source_record_id", "date_original", "title",
    "primary_country_iso3", "target_country_iso3_list", "language_codes", "source_names",
    "reliefweb_url", "disaster_ids", "quality_flags", "source_files",
]

COMMON_ADDITION_FIELDS = [
    "source_name", "source_record_type", "source_record_id", "event_date",
    "event_end_date", "country_iso3", "admin1_raw", "latitude", "longitude",
    "event_type_raw", "title_raw", "source_url", "quality_flag",
]

LINK_FIELDS = [
    "report_id", "disaster_id", "disaster_name", "glide", "link_source", "source_file",
]

MISSING_BODY_AUDIT_FIELDS = [
    "source_record_id", "title_present", "origin_url_present", "reliefweb_url_present",
    "attachment_metadata_present", "native_disaster_link_present",
    "usable_non_body_evidence_present", "identifier_only_no_usable_content",
    "source_files", "quality_flags",
]


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_list(values: list[Any]) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _unique(values: Iterator[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if value is None or value == "":
            continue
        key = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _text(value: Any) -> str:
    if value is None:
        return ""
    return unicodedata.normalize("NFC", str(value)).strip()


def clean_body(value: Any) -> str:
    """Apply only the preregistered whitespace and Unicode normalization."""
    text = unicodedata.normalize("NFC", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text)


def _raw_text_hash(value: Any) -> str:
    return sha256(str(value or "").encode("utf-8")).hexdigest()


def _payload_hash(record: dict[str, Any]) -> str:
    """Exclude query-dependent score while retaining official id, href, and fields."""
    payload = {key: value for key, value in record.items() if key != "score"}
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(normalized.encode("utf-8")).hexdigest()


def _iso_datetime(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _valid_url(value: Any) -> bool:
    text = _text(value)
    if not text:
        return True
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _objects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _nested_values(items: list[dict[str, Any]], key: str) -> list[str]:
    return _unique(_text(item.get(key)) for item in items)


def _country_fields(fields: dict[str, Any]) -> dict[str, Any]:
    countries = _objects(fields.get("country"))
    primary = fields.get("primary_country") if isinstance(fields.get("primary_country"), dict) else {}
    iso3_list = _unique(_text(item.get("iso3")).upper() for item in countries)
    target = sorted(set(iso3_list) & TARGET_ISO3)
    primary_iso3 = _text(primary.get("iso3")).upper()
    if len(target) > 1:
        basis = "MULTI_COUNTRY"
    elif primary_iso3 in TARGET_ISO3:
        basis = "PRIMARY_COUNTRY"
    elif target:
        basis = "COUNTRY_LIST"
    else:
        basis = ""
    return {
        "primary_country_name": _text(primary.get("name")),
        "primary_country_iso3": primary_iso3,
        "country_names": _json_list(_nested_values(countries, "name")),
        "country_iso3_list": _json_list(iso3_list),
        "target_country_match": str(bool(target)).lower(),
        "target_country_iso3_list": _json_list(target),
        "target_geo_match_basis": basis,
        "target_count": len(target),
        "country_count": len(iso3_list),
    }


def _quality(flags: list[str]) -> str:
    return "|".join(sorted(set(flags)))


@contextmanager
def _gzip_text_writer(path: Path) -> Iterator[io.TextIOWrapper]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                yield text


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    names = fieldnames or sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def audit_raw_files(
    raw_root: Path,
    audit_dir: Path,
    expected_totals: dict[tuple[str, str], int] = EXPECTED_TOTALS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Write the raw inventory before any standardization and validate pagination."""
    rows: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for record_type, country in expected_totals:
        folder = raw_root / record_type / country
        files = sorted(folder.glob("page_*.json")) if folder.exists() else []
        expected_total = expected_totals[(record_type, country)]
        expected_pages = math.ceil(expected_total / PAGE_SIZE)
        page_indices: list[int] = []
        group_rows = 0
        total_counts: set[int] = set()
        errors: list[str] = []
        for path in files:
            match = re.fullmatch(r"page_(\d{4})\.json", path.name)
            page_index = int(match.group(1)) if match else -1
            page_indices.append(page_index)
            parse_status = "OK"
            endpoint = ""
            api_total: int | None = None
            api_count: int | None = None
            data_count: int | None = None
            try:
                with path.open(encoding="utf-8-sig") as handle:
                    payload = json.load(handle)
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise ValueError("top-level object or data array missing")
                endpoint = _text(payload.get("href"))
                api_total = int(payload.get("totalCount"))
                api_count = int(payload.get("count"))
                data_count = len(payload["data"])
                total_counts.add(api_total)
                group_rows += data_count
                if api_count != data_count:
                    parse_status = "COUNT_MISMATCH"
                    errors.append(f"{path.name}: count={api_count}, data={data_count}")
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
                parse_status = "JSON_PARSE_FAILURE"
                errors.append(f"{path.name}: {error}")
            rows.append({
                "record_type": record_type.upper(),
                "download_country": COUNTRY_FOLDER_TO_CODE[country],
                "absolute_path": str(path.resolve()),
                "relative_raw_path": path.relative_to(raw_root).as_posix(),
                "filename": path.name,
                "page_index": page_index,
                "expected_offset": page_index * PAGE_SIZE if page_index >= 0 else "",
                "size_bytes": path.stat().st_size,
                "sha256": file_sha256(path),
                "zero_byte": str(path.stat().st_size == 0).lower(),
                "json_status": parse_status,
                "api_endpoint": endpoint,
                "api_total_count": api_total if api_total is not None else "",
                "api_page_count": api_count if api_count is not None else "",
                "data_record_count": data_count if data_count is not None else "",
                "record_count_consistent": str(api_count == data_count if data_count is not None else False).lower(),
            })
        expected_indices = list(range(expected_pages))
        missing = sorted(set(expected_indices) - set(page_indices))
        unexpected = sorted(set(page_indices) - set(expected_indices))
        if missing:
            errors.append(f"missing pages: {missing}")
        if unexpected:
            errors.append(f"unexpected pages: {unexpected}")
        if total_counts != {expected_total}:
            errors.append(f"API totalCount values {sorted(total_counts)} != expected {expected_total}")
        if group_rows != expected_total:
            errors.append(f"summed data rows {group_rows} != expected {expected_total}")
        groups[(record_type, country)] = {
            "record_type": record_type.upper(),
            "download_country": COUNTRY_FOLDER_TO_CODE[country],
            "expected_total": expected_total,
            "observed_rows": group_rows,
            "expected_pages": expected_pages,
            "observed_pages": len(files),
            "page_indices": page_indices,
            "missing_pages": missing,
            "unexpected_pages": unexpected,
            "status": "PASS" if not errors else "FAIL",
            "issues": errors,
        }

    rows.sort(key=lambda row: (row["record_type"], row["download_country"], row["page_index"]))
    group_values = [groups[key] for key in sorted(groups)]
    all_pass = all(group["status"] == "PASS" for group in group_values)
    summary = {
        "raw_root": str(raw_root.resolve()),
        "raw_json_file_count": len(rows),
        "zero_byte_file_count": sum(row["zero_byte"] == "true" for row in rows),
        "json_failure_count": sum(row["json_status"] == "JSON_PARSE_FAILURE" for row in rows),
        "all_groups_pass": all_pass,
        "groups": group_values,
    }
    _write_csv(audit_dir / "reliefweb_source_file_inventory.csv", rows)
    _write_json(audit_dir / "reliefweb_source_file_inventory.json", {"summary": summary, "files": rows})
    lines = [
        "# ReliefWeb raw integrity report", "",
        f"Raw root: `{raw_root.resolve()}`", "",
        f"Files: {len(rows)}. Zero-byte files: {summary['zero_byte_file_count']}. JSON parse failures: {summary['json_failure_count']}.", "",
        "| Type | Download country | Expected rows | Observed rows | Expected pages | Observed pages | Status |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for group in group_values:
        lines.append(
            f"| {group['record_type']} | {group['download_country']} | {group['expected_total']:,} | "
            f"{group['observed_rows']:,} | {group['expected_pages']} | {group['observed_pages']} | {group['status']} |"
        )
    lines.extend([
        "", "Page continuity is checked from the deterministic `page_NNNN.json` filenames. The response payloads expose totalCount and page count but do not preserve the POST request offset, so expected offsets are filename-derived rather than claimed as source-returned metadata.",
        "", "Every inventory row records the absolute raw path and SHA-256. The files are opened read-only.", "",
    ])
    (audit_dir / "reliefweb_raw_integrity_report.md").write_text("\n".join(lines), encoding="utf-8")
    return rows, summary


def _copy_metadata(raw_root: Path) -> dict[str, dict[str, dict[str, Any]]]:
    metadata: dict[str, dict[str, dict[str, Any]]] = {"reports": {}, "disasters": {}}
    country_rank = {"ecu": 0, "col": 1, "per": 2}
    for record_type in ("reports", "disasters"):
        for country_name, country_code in COUNTRY_FOLDER_TO_CODE.items():
            for path in sorted((raw_root / record_type / country_name).glob("page_*.json")):
                page_index = int(path.stem.split("_")[1])
                with path.open(encoding="utf-8-sig") as handle:
                    payload = json.load(handle)
                query_endpoint = _text(payload.get("href"))
                for row_number, record in enumerate(payload["data"], start=1):
                    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
                    record_id = _text(record.get("id") or fields.get("id"))
                    if not record_id:
                        raise ValueError(f"Missing ReliefWeb id in {path}:{row_number}")
                    digest = _payload_hash(record)
                    key = (country_rank[country_code], str(path.resolve()), row_number)
                    item = metadata[record_type].setdefault(record_id, {
                        "copy_count": 0, "countries": set(), "files": set(), "pages": set(),
                        "rows": set(), "hashes": set(), "copies": [], "canonical_key": key,
                        "canonical_file": str(path.resolve()), "canonical_row": row_number,
                        "query_endpoint": query_endpoint,
                    })
                    item["copy_count"] += 1
                    item["countries"].add(country_code)
                    item["files"].add(str(path.resolve()))
                    item["pages"].add(page_index)
                    item["rows"].add(row_number)
                    item["hashes"].add(digest)
                    item["copies"].append({
                        "download_country": country_code, "source_file": str(path.resolve()),
                        "page_index": page_index, "source_row_number": row_number,
                        "raw_payload_hash": digest,
                    })
                    if key < item["canonical_key"]:
                        item["canonical_key"] = key
                        item["canonical_file"] = str(path.resolve())
                        item["canonical_row"] = row_number
                        item["query_endpoint"] = query_endpoint
    return metadata


def _provenance(meta: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    source_files = _json_list(sorted(meta["files"]))
    return {
        "download_country_membership": _json_list(sorted(meta["countries"])),
        "download_copy_count": meta["copy_count"],
        "source_files": source_files,
        "source_file_membership": source_files,
        "source_page_indices": _json_list(sorted(meta["pages"])),
        "source_row_numbers": _json_list(sorted(meta["rows"])),
        "source_copy_provenance": json.dumps(
            sorted(meta["copies"], key=lambda row: (row["source_file"], row["source_row_number"])),
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ),
        "source_endpoint": _text(record.get("href")),
        "source_query_endpoint": meta["query_endpoint"],
        "raw_payload_hash": _payload_hash(record),
    }


def standardize_report(record: dict[str, Any], meta: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    sources = _objects(fields.get("source"))
    languages = _objects(fields.get("language"))
    disasters = _objects(fields.get("disaster"))
    disaster_types = _objects(fields.get("disaster_type"))
    attachments = _objects(fields.get("file"))
    dates = fields.get("date") if isinstance(fields.get("date"), dict) else {}
    country = _country_fields(fields)
    date_original = _iso_datetime(dates.get("original"))
    flags: list[str] = []
    if not _text(fields.get("title")): flags.append("MISSING_TITLE")
    if not _text(fields.get("body")): flags.append("MISSING_BODY")
    if not _text(dates.get("original")): flags.append("MISSING_ORIGINAL_DATE")
    elif not date_original: flags.append("INVALID_ORIGINAL_DATE")
    if not _text(fields.get("primary_country", {}).get("name") if isinstance(fields.get("primary_country"), dict) else ""): flags.append("MISSING_PRIMARY_COUNTRY")
    if not _objects(fields.get("country")): flags.append("MISSING_COUNTRY_LIST")
    if not sources: flags.append("MISSING_SOURCE")
    if not languages: flags.append("MISSING_LANGUAGE")
    if country["country_count"] > 1: flags.append("MULTI_COUNTRY_RECORD")
    if country["target_count"] > 1: flags.append("MULTI_TARGET_COUNTRY_RECORD")
    if meta["copy_count"] > 1: flags.append("DUPLICATE_DOWNLOAD_COPY")
    if len(meta["hashes"]) > 1: flags.append("DUPLICATE_ID_PAYLOAD_CONFLICT")
    if not disasters: flags.append("NO_NATIVE_DISASTER_LINK")
    if date_original:
        parsed = _as_datetime(date_original)
        if parsed and not DOWNLOAD_START <= parsed <= DOWNLOAD_END:
            flags.append("REPORT_DATE_OUTSIDE_DOWNLOAD_WINDOW")
    if not _valid_url(fields.get("url")): flags.append("INVALID_RELIEFWEB_URL")
    if not _valid_url(fields.get("origin")): flags.append("INVALID_ORIGIN_URL")
    source_types = _unique(
        _text(item.get("type", {}).get("name"))
        for item in sources if isinstance(item.get("type"), dict)
    )
    row = {
        "source_name": "RELIEFWEB", "source_record_type": "REPORT",
        "source_record_id": _text(record.get("id") or fields.get("id")),
        "source_uuid": _text(fields.get("uuid")), "title": _text(fields.get("title")),
        "body_text": clean_body(fields.get("body")), "body_text_raw_hash": _raw_text_hash(fields.get("body")),
        "body_html_present": str(bool(_text(fields.get("body-html")))).lower(),
        "origin_url": _text(fields.get("origin")), "reliefweb_url": _text(fields.get("url")),
        "url_alias": _text(fields.get("url_alias")), "date_original": date_original,
        "date_created": _iso_datetime(dates.get("created")), "date_changed": _iso_datetime(dates.get("changed")),
        "primary_country_name": country["primary_country_name"], "primary_country_iso3": country["primary_country_iso3"],
        "country_names": country["country_names"], "country_iso3_list": country["country_iso3_list"],
        "target_country_match": country["target_country_match"], "target_country_iso3_list": country["target_country_iso3_list"],
        "target_geo_match_basis": country["target_geo_match_basis"],
        "source_names": _json_list(_nested_values(sources, "name")),
        "source_shortnames": _json_list(_nested_values(sources, "shortname")),
        "source_types": _json_list(source_types), "language_names": _json_list(_nested_values(languages, "name")),
        "language_codes": _json_list(_nested_values(languages, "code")),
        "theme_names": _json_list(_nested_values(_objects(fields.get("theme")), "name")),
        "format_names": _json_list(_nested_values(_objects(fields.get("format")), "name")),
        "ocha_product_names": _json_list(_nested_values(_objects(fields.get("ocha_product")), "name")),
        "disaster_ids": _json_list(_nested_values(disasters, "id")),
        "disaster_names": _json_list(_nested_values(disasters, "name")),
        "disaster_glides": _json_list(_nested_values(disasters, "glide")),
        "disaster_type_names": _json_list(_nested_values(disaster_types, "name")),
        "disaster_type_codes": _json_list(_nested_values(disaster_types, "code")),
        "vulnerable_group_names": _json_list(_nested_values(_objects(fields.get("vulnerable_groups")), "name")),
        "attachment_count": len(attachments), "attachment_urls": _json_list(_nested_values(attachments, "url")),
        "attachment_filenames": _json_list(_nested_values(attachments, "filename")),
        "attachment_mimetypes": _json_list(_nested_values(attachments, "mimetype")),
        **_provenance(meta, record), "quality_flags": _quality(flags),
    }
    links = [{
        "report_id": row["source_record_id"], "disaster_id": _text(disaster.get("id")),
        "disaster_name": _text(disaster.get("name")), "glide": _text(disaster.get("glide")),
        "link_source": "RELIEFWEB_NATIVE", "source_file": meta["canonical_file"],
    } for disaster in disasters if _text(disaster.get("id"))]
    return row, links


def standardize_disaster(record: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
    dates = fields.get("date") if isinstance(fields.get("date"), dict) else {}
    types = _objects(fields.get("type"))
    primary_type = fields.get("primary_type") if isinstance(fields.get("primary_type"), dict) else {}
    country = _country_fields(fields)
    event_date = _iso_datetime(dates.get("event"))
    flags: list[str] = []
    if not _text(fields.get("name")): flags.append("MISSING_TITLE")
    if not _text(dates.get("event")): flags.append("MISSING_EVENT_DATE")
    elif not event_date: flags.append("INVALID_EVENT_DATE")
    if not country["primary_country_name"]: flags.append("MISSING_PRIMARY_COUNTRY")
    if not _objects(fields.get("country")): flags.append("MISSING_COUNTRY_LIST")
    if country["country_count"] > 1: flags.append("MULTI_COUNTRY_RECORD")
    if country["target_count"] > 1: flags.append("MULTI_TARGET_COUNTRY_RECORD")
    if meta["copy_count"] > 1: flags.append("DUPLICATE_DOWNLOAD_COPY")
    if len(meta["hashes"]) > 1: flags.append("DUPLICATE_ID_PAYLOAD_CONFLICT")
    if event_date:
        parsed = _as_datetime(event_date)
        if parsed and not DOWNLOAD_START <= parsed <= DOWNLOAD_END:
            flags.append("EVENT_DATE_OUTSIDE_DOWNLOAD_WINDOW")
    if not _valid_url(fields.get("url")): flags.append("INVALID_RELIEFWEB_URL")
    return {
        "source_name": "RELIEFWEB", "source_record_type": "DISASTER",
        "source_record_id": _text(record.get("id") or fields.get("id")),
        "source_uuid": _text(fields.get("uuid")), "event_name": _text(fields.get("name")),
        "event_description": _text(fields.get("description")), "status": _text(fields.get("status")),
        "glide": _text(fields.get("glide")), "event_date": event_date,
        "date_created": _iso_datetime(dates.get("created")), "date_changed": _iso_datetime(dates.get("changed")),
        "primary_country_name": country["primary_country_name"], "primary_country_iso3": country["primary_country_iso3"],
        "country_names": country["country_names"], "country_iso3_list": country["country_iso3_list"],
        "target_country_match": country["target_country_match"], "target_country_iso3_list": country["target_country_iso3_list"],
        "target_geo_match_basis": country["target_geo_match_basis"],
        "primary_disaster_type_name": _text(primary_type.get("name")),
        "primary_disaster_type_code": _text(primary_type.get("code")),
        "disaster_type_names": _json_list(_nested_values(types, "name")),
        "disaster_type_codes": _json_list(_nested_values(types, "code")),
        "reliefweb_url": _text(fields.get("url")), "url_alias": _text(fields.get("url_alias")),
        **_provenance(meta, record), "quality_flags": _quality(flags),
    }


def _canonical_records(raw_root: Path, record_type: str, metadata: dict[str, dict[str, Any]]) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    for country_name in COUNTRY_FOLDER_TO_CODE:
        for path in sorted((raw_root / record_type / country_name).glob("page_*.json")):
            with path.open(encoding="utf-8-sig") as handle:
                payload = json.load(handle)
            for row_number, record in enumerate(payload["data"], start=1):
                fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
                record_id = _text(record.get("id") or fields.get("id"))
                meta = metadata[record_id]
                if str(path.resolve()) == meta["canonical_file"] and row_number == meta["canonical_row"]:
                    yield record, meta


def _date_bucket(value: str, *, event: bool) -> str:
    if not value:
        return "MISSING_OR_INVALID"
    parsed = _as_datetime(value)
    if parsed is None:
        return "MISSING_OR_INVALID"
    if parsed < DOWNLOAD_START:
        return "BEFORE_DOWNLOAD_WINDOW"
    if parsed > DOWNLOAD_END:
        return "AFTER_DOWNLOAD_WINDOW"
    return "IN_DOWNLOAD_WINDOW"


def run_reliefweb_standardization(
    raw_root: Path,
    output_root: Path,
    expected_totals: dict[tuple[str, str], int] = EXPECTED_TOTALS,
) -> dict[str, Any]:
    raw_root = raw_root.resolve()
    output_root = output_root.resolve()
    audit_dir = output_root / "audit"
    processed_dir = output_root / "processed"
    audit_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    common_index = processed_dir / "common_event_index.tsv.gz"
    common_index_before = file_sha256(common_index) if common_index.exists() else None

    inventory, raw_audit = audit_raw_files(raw_root, audit_dir, expected_totals)
    if not raw_audit["all_groups_pass"]:
        raise RuntimeError("ReliefWeb raw pagination/integrity audit failed; see reliefweb_raw_integrity_report.md")
    before_hashes = {row["absolute_path"]: row["sha256"] for row in inventory}
    metadata = _copy_metadata(raw_root)

    duplicate_rows: list[dict[str, Any]] = []
    conflict_rows: list[dict[str, Any]] = []
    for record_type in ("reports", "disasters"):
        metas = metadata[record_type]
        record_label = record_type[:-1].upper()
        duplicate_rows.append({
            "record_type": record_label, "raw_rows": sum(item["copy_count"] for item in metas.values()),
            "unique_ids": len(metas), "duplicate_copies": sum(item["copy_count"] - 1 for item in metas.values()),
            "cross_country_duplicate_ids": sum(len(item["countries"]) > 1 for item in metas.values()),
            "payload_conflict_ids": sum(len(item["hashes"]) > 1 for item in metas.values()),
        })
        for record_id, item in sorted(metas.items(), key=lambda pair: (int(pair[0]) if pair[0].isdigit() else math.inf, pair[0])):
            if len(item["hashes"]) > 1:
                conflict_rows.append({
                    "record_type": record_label, "source_record_id": record_id,
                    "download_copy_count": item["copy_count"],
                    "download_country_membership": _json_list(sorted(item["countries"])),
                    "normalized_payload_hashes": _json_list(sorted(item["hashes"])),
                    "source_files": _json_list(sorted(item["files"])),
                    "copy_audit": json.dumps(sorted(item["copies"], key=lambda row: (row["source_file"], row["source_row_number"])), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                })
    _write_csv(audit_dir / "reliefweb_duplicate_summary.csv", duplicate_rows)
    _write_csv(audit_dir / "reliefweb_duplicate_conflicts.csv", conflict_rows, [
        "record_type", "source_record_id", "download_copy_count", "download_country_membership",
        "normalized_payload_hashes", "source_files", "copy_audit",
    ])

    paths = {
        "reports": processed_dir / "reliefweb_reports_standardized.tsv.gz",
        "disasters": processed_dir / "reliefweb_disasters_standardized.tsv.gz",
        "links": processed_dir / "reliefweb_report_disaster_links.tsv.gz",
        "report_index": processed_dir / "reliefweb_report_index.tsv.gz",
        "common_addition": processed_dir / "common_event_index_reliefweb_addition.tsv.gz",
    }
    quality_counts: dict[str, Counter[str]] = {"REPORT": Counter(), "DISASTER": Counter()}
    language_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    disaster_type_counts: dict[str, Counter[str]] = {"REPORT": Counter(), "DISASTER": Counter()}
    target_overlap: dict[str, Counter[str]] = {"REPORT": Counter(), "DISASTER": Counter()}
    dates_by_type: dict[str, list[str]] = {"REPORT": [], "DISASTER": []}
    date_buckets: dict[str, Counter[str]] = {"REPORT": Counter(), "DISASTER": Counter()}
    report_linked = 0
    report_unlinked = 0
    report_link_rows = 0
    missing_body_rows: list[dict[str, Any]] = []

    with (
        _gzip_text_writer(paths["reports"]) as report_handle,
        _gzip_text_writer(paths["disasters"]) as disaster_handle,
        _gzip_text_writer(paths["links"]) as link_handle,
        _gzip_text_writer(paths["report_index"]) as report_index_handle,
        _gzip_text_writer(paths["common_addition"]) as common_handle,
    ):
        report_writer = csv.DictWriter(report_handle, fieldnames=REPORT_FIELDS, delimiter="\t", lineterminator="\n")
        disaster_writer = csv.DictWriter(disaster_handle, fieldnames=DISASTER_FIELDS, delimiter="\t", lineterminator="\n")
        link_writer = csv.DictWriter(link_handle, fieldnames=LINK_FIELDS, delimiter="\t", lineterminator="\n")
        report_index_writer = csv.DictWriter(report_index_handle, fieldnames=REPORT_INDEX_FIELDS, delimiter="\t", lineterminator="\n")
        common_writer = csv.DictWriter(common_handle, fieldnames=COMMON_ADDITION_FIELDS, delimiter="\t", lineterminator="\n")
        for writer in (report_writer, disaster_writer, link_writer, report_index_writer, common_writer): writer.writeheader()

        for record, meta in _canonical_records(raw_root, "reports", metadata["reports"]):
            row, links = standardize_report(record, meta)
            report_writer.writerow(row)
            report_index_writer.writerow({field: row[field] for field in REPORT_INDEX_FIELDS})
            for link in links:
                link_writer.writerow(link)
                report_link_rows += 1
            report_linked += int(bool(links)); report_unlinked += int(not links)
            for flag in filter(None, row["quality_flags"].split("|")): quality_counts["REPORT"][flag] += 1
            if "MISSING_BODY" in row["quality_flags"].split("|"):
                title_present = bool(row["title"])
                origin_present = bool(row["origin_url"])
                reliefweb_url_present = bool(row["reliefweb_url"])
                attachment_present = int(row["attachment_count"]) > 0
                native_link_present = bool(links)
                usable_evidence = title_present or origin_present or attachment_present or native_link_present
                missing_body_rows.append({
                    "source_record_id": row["source_record_id"],
                    "title_present": str(title_present).lower(),
                    "origin_url_present": str(origin_present).lower(),
                    "reliefweb_url_present": str(reliefweb_url_present).lower(),
                    "attachment_metadata_present": str(attachment_present).lower(),
                    "native_disaster_link_present": str(native_link_present).lower(),
                    "usable_non_body_evidence_present": str(usable_evidence).lower(),
                    "identifier_only_no_usable_content": str(not usable_evidence).lower(),
                    "source_files": row["source_files"],
                    "quality_flags": row["quality_flags"],
                })
            for value in json.loads(row["language_names"]): language_counts[value] += 1
            for value in json.loads(row["source_names"]): source_counts[value] += 1
            for value in json.loads(row["disaster_type_names"]): disaster_type_counts["REPORT"][value] += 1
            targets = json.loads(row["target_country_iso3_list"])
            target_overlap["REPORT"][_json_list(targets)] += 1
            date_buckets["REPORT"][_date_bucket(row["date_original"], event=False)] += 1
            if row["date_original"]: dates_by_type["REPORT"].append(row["date_original"])

        for record, meta in _canonical_records(raw_root, "disasters", metadata["disasters"]):
            row = standardize_disaster(record, meta)
            disaster_writer.writerow(row)
            common_writer.writerow({
                "source_name": "RELIEFWEB", "source_record_type": "DISASTER",
                "source_record_id": row["source_record_id"], "event_date": row["event_date"],
                "event_end_date": "", "country_iso3": row["primary_country_iso3"],
                "admin1_raw": "", "latitude": "", "longitude": "",
                "event_type_raw": row["primary_disaster_type_name"], "title_raw": row["event_name"],
                "source_url": row["reliefweb_url"],
                "quality_flag": "FLAGGED" if row["quality_flags"] else "PASS",
            })
            for flag in filter(None, row["quality_flags"].split("|")): quality_counts["DISASTER"][flag] += 1
            for value in json.loads(row["disaster_type_names"]): disaster_type_counts["DISASTER"][value] += 1
            targets = json.loads(row["target_country_iso3_list"])
            target_overlap["DISASTER"][_json_list(targets)] += 1
            date_buckets["DISASTER"][_date_bucket(row["event_date"], event=True)] += 1
            if row["event_date"]: dates_by_type["DISASTER"].append(row["event_date"])

    cleaning_rows = []
    for duplicate in duplicate_rows:
        kind = duplicate["record_type"]
        date_values = dates_by_type[kind]
        cleaning_rows.append({
            **duplicate,
            "earliest_formal_date": min(date_values) if date_values else "",
            "latest_formal_date": max(date_values) if date_values else "",
            "output_path": str(paths["reports" if kind == "REPORT" else "disasters"].resolve()),
        })
    _write_csv(audit_dir / "reliefweb_cleaning_summary.csv", cleaning_rows)
    quality_rows = [
        {"record_type": kind, "quality_flag": flag, "record_count": count}
        for kind in ("REPORT", "DISASTER") for flag, count in sorted(quality_counts[kind].items())
    ]
    _write_csv(audit_dir / "reliefweb_quality_summary.csv", quality_rows)
    date_rows = [
        {
            "record_type": kind, "date_field": "date.original" if kind == "REPORT" else "date.event",
            "date_bucket": bucket, "record_count": date_buckets[kind].get(bucket, 0),
            "earliest_valid_date": min(dates_by_type[kind]) if dates_by_type[kind] else "",
            "latest_valid_date": max(dates_by_type[kind]) if dates_by_type[kind] else "",
        }
        for kind in ("REPORT", "DISASTER")
        for bucket in ("BEFORE_DOWNLOAD_WINDOW", "IN_DOWNLOAD_WINDOW", "AFTER_DOWNLOAD_WINDOW", "MISSING_OR_INVALID")
    ]
    _write_csv(audit_dir / "reliefweb_date_audit.csv", date_rows)
    _write_csv(
        audit_dir / "reliefweb_missing_body_audit.csv",
        missing_body_rows,
        MISSING_BODY_AUDIT_FIELDS,
    )
    missing_body_summary = {
        "population_definition": "Unique standardized ReliefWeb reports flagged MISSING_BODY.",
        "usable_non_body_evidence_definition": (
            "At least one of title, origin_url, attachment metadata, or a native ReliefWeb "
            "disaster link is present. reliefweb_url alone is treated as identifier/retrieval metadata."
        ),
        "missing_body_reports": len(missing_body_rows),
        "title_present": sum(row["title_present"] == "true" for row in missing_body_rows),
        "origin_url_present": sum(row["origin_url_present"] == "true" for row in missing_body_rows),
        "reliefweb_url_present": sum(row["reliefweb_url_present"] == "true" for row in missing_body_rows),
        "attachment_metadata_present": sum(
            row["attachment_metadata_present"] == "true" for row in missing_body_rows
        ),
        "native_disaster_link_present": sum(
            row["native_disaster_link_present"] == "true" for row in missing_body_rows
        ),
        "usable_non_body_evidence_present": sum(
            row["usable_non_body_evidence_present"] == "true" for row in missing_body_rows
        ),
        "identifier_only_no_usable_content": sum(
            row["identifier_only_no_usable_content"] == "true" for row in missing_body_rows
        ),
    }
    _write_json(audit_dir / "reliefweb_missing_body_summary.json", missing_body_summary)

    after_hashes = {path: file_sha256(Path(path)) for path in before_hashes}
    changed = sorted(path for path in before_hashes if before_hashes[path] != after_hashes[path])
    if changed:
        raise RuntimeError(f"ReliefWeb raw files changed: {changed}")

    summary = {
        "raw_root": str(raw_root), "output_root": str(output_root),
        "raw_file_count": len(inventory), "raw_sha_unchanged": not changed,
        "raw_reports": next(row["raw_rows"] for row in duplicate_rows if row["record_type"] == "REPORT"),
        "raw_disasters": next(row["raw_rows"] for row in duplicate_rows if row["record_type"] == "DISASTER"),
        "unique_reports": len(metadata["reports"]), "unique_disasters": len(metadata["disasters"]),
        "report_duplicate_copies": next(row["duplicate_copies"] for row in duplicate_rows if row["record_type"] == "REPORT"),
        "disaster_duplicate_copies": next(row["duplicate_copies"] for row in duplicate_rows if row["record_type"] == "DISASTER"),
        "payload_conflict_ids": len(conflict_rows), "reports_with_native_disaster_link": report_linked,
        "reports_without_native_disaster_link": report_unlinked, "native_link_rows": report_link_rows,
        "earliest_report_date": min(dates_by_type["REPORT"]) if dates_by_type["REPORT"] else None,
        "latest_report_date": max(dates_by_type["REPORT"]) if dates_by_type["REPORT"] else None,
        "earliest_disaster_event_date": min(dates_by_type["DISASTER"]) if dates_by_type["DISASTER"] else None,
        "latest_disaster_event_date": max(dates_by_type["DISASTER"]) if dates_by_type["DISASTER"] else None,
        "language_distribution": dict(language_counts.most_common()),
        "source_organization_distribution": dict(source_counts.most_common()),
        "disaster_type_distribution": {
            kind: dict(disaster_type_counts[kind].most_common()) for kind in ("REPORT", "DISASTER")
        },
        "target_country_overlap": {kind: dict(sorted(counts.items())) for kind, counts in target_overlap.items()},
        "quality_counts": {kind: dict(sorted(counts.items())) for kind, counts in quality_counts.items()},
        "date_audit": {kind: dict(date_buckets[kind]) for kind in date_buckets},
        "missing_body_audit": missing_body_summary,
        "outputs": {key: str(path.resolve()) for key, path in paths.items()},
        "output_sha256": {key: file_sha256(path) for key, path in paths.items()},
        "existing_common_index_sha256_before": common_index_before,
        "existing_common_index_sha256_after": file_sha256(common_index) if common_index.exists() else None,
        "existing_common_index_unchanged": (
            common_index_before == (file_sha256(common_index) if common_index.exists() else None)
        ),
        "common_index_action": "Created schema-explicit ReliefWeb disaster addition; existing common_event_index.tsv.gz was not modified because it lacks source_name and source_record_type.",
    }
    _write_standardization_report(audit_dir / "reliefweb_standardization_report.md", summary, duplicate_rows)
    return summary


def _distribution_lines(values: dict[str, int]) -> list[str]:
    return [f"- {name or '[missing]'}: {count:,}" for name, count in values.items()]


def _write_standardization_report(path: Path, summary: dict[str, Any], duplicate_rows: list[dict[str, Any]]) -> None:
    reports = next(row for row in duplicate_rows if row["record_type"] == "REPORT")
    disasters = next(row for row in duplicate_rows if row["record_type"] == "DISASTER")
    report_overlap = summary["target_country_overlap"]["REPORT"]
    disaster_overlap = summary["target_country_overlap"]["DISASTER"]
    lines = [
        "# ReliefWeb Stage 5B-1R standardization report", "",
        "## Boundary", "",
        "Reports are document/evidence records. Disasters are event records. Reports were not inserted into any event index. No GenAI, risk-family screening, clustering, cross-source fusion, GDELT linkage, optimization, Gurobi, CCG, external download, attachment download, or Paper-2 modification was performed.", "",
        f"Raw files remained external and read-only at `{summary['raw_root']}`. All outputs were written inside the existing Stage 5B-1 root `{summary['output_root']}`.", "",
        "## Counts", "",
        "| Type | Raw downloaded rows | Unique ids | Duplicate copies | Cross-country duplicate ids | Payload conflicts |",
        "|---|---:|---:|---:|---:|---:|",
        f"| Reports | {reports['raw_rows']:,} | {reports['unique_ids']:,} | {reports['duplicate_copies']:,} | {reports['cross_country_duplicate_ids']:,} | {reports['payload_conflict_ids']:,} |",
        f"| Disasters | {disasters['raw_rows']:,} | {disasters['unique_ids']:,} | {disasters['duplicate_copies']:,} | {disasters['cross_country_duplicate_ids']:,} | {disasters['payload_conflict_ids']:,} |", "",
        "Country-specific raw totals were preserved: reports Ecuador 9,032, Colombia 20,501, Peru 11,312; disasters Ecuador 16, Colombia 24, Peru 22. These are download-copy counts, not unique-event counts.", "",
        "## Dates", "",
        f"Report `date.original`: {summary['earliest_report_date']} through {summary['latest_report_date']}.",
        f"Disaster `date.event`: {summary['earliest_disaster_event_date']} through {summary['latest_disaster_event_date']}.", "",
        "Disaster downloads were filtered by record creation date. A source-native `date.event` outside 2015-01-01 through 2026-08-31 is therefore retained and flagged `EVENT_DATE_OUTSIDE_DOWNLOAD_WINDOW`; creation/query date and event date are not the same concept.", "",
        "## Native report-disaster links", "",
        f"Reports with at least one native ReliefWeb disaster link: {summary['reports_with_native_disaster_link']:,}.",
        f"Reports without a native link: {summary['reports_without_native_disaster_link']:,}.",
        f"Native link rows: {summary['native_link_rows']:,}. No links were inferred from text.", "",
        "## Quality and missing fields", "",
        "Reports:", *_distribution_lines(summary["quality_counts"]["REPORT"]), "",
        "Disasters:", *_distribution_lines(summary["quality_counts"]["DISASTER"]), "",
        "Flags are non-destructive: flagged records remain in the standardized outputs. Missing-field flags absent from these lists have a zero count.", "",
        "## Language distribution", "", *_distribution_lines(summary["language_distribution"]), "",
        "## Source organization distribution", "", *_distribution_lines(summary["source_organization_distribution"]), "",
        "## Disaster-type distribution", "",
        "Reports:", *_distribution_lines(summary["disaster_type_distribution"]["REPORT"]), "",
        "Disasters:", *_distribution_lines(summary["disaster_type_distribution"]["DISASTER"]), "",
        "## Target-country overlap", "", "Reports:", *_distribution_lines(report_overlap), "", "Disasters:", *_distribution_lines(disaster_overlap), "",
        "Primary country was never overwritten. `target_country_iso3_list` is derived from the full source-native country list, so regional records remain eligible even when their primary country is outside ECU/COL/PER.", "",
        "## Common event index", "",
        "The existing common index schema lacks the explicit `source_name` and `source_record_type` fields required here. It was left byte-for-byte untouched. `common_event_index_reliefweb_addition.tsv.gz` contains ReliefWeb disasters only and records the schema gap; reports remain solely in the report index.", "",
        "## Integrity and provenance", "",
        f"Raw SHA-256 unchanged: `{str(summary['raw_sha_unchanged']).lower()}` across {summary['raw_file_count']} JSON files. Existing common index unchanged: `{str(summary['existing_common_index_unchanged']).lower()}`. Every standardized row carries the ReliefWeb id, source endpoint, raw payload hash, source files, page indices, row numbers, exact copy provenance, and download-country membership. Payload normalization excludes only query-dependent `score`.", "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
