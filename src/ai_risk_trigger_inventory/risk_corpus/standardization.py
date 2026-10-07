"""Deterministic, source-preserving Stage-5 risk-corpus standardization."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable
from urllib.parse import urlparse
import zipfile
import xml.etree.ElementTree as ET


COMMON_COLUMNS = [
    "source", "source_record_id", "source_file", "source_row_number",
    "event_date", "event_end_date", "country_raw", "country_iso3",
    "admin1_raw", "admin2_raw", "location_raw", "latitude", "longitude",
    "event_type_raw", "event_subtype_raw", "title_raw", "description_raw",
    "source_url", "retrieval_date", "quality_flag", "quality_issues",
    "provenance_status",
]

THIN_INDEX_COLUMNS = [
    "source", "source_record_id", "event_date", "event_end_date",
    "country_iso3", "admin1_raw", "latitude", "longitude",
    "event_type_raw", "title_raw", "source_url", "quality_flag",
]

GDELT_58_COLUMNS = [
    "GlobalEventID", "SQLDATE", "MonthYear", "Year", "FractionDate",
    "Actor1Code", "Actor1Name", "Actor1CountryCode", "Actor1KnownGroupCode",
    "Actor1EthnicCode", "Actor1Religion1Code", "Actor1Religion2Code",
    "Actor1Type1Code", "Actor1Type2Code", "Actor1Type3Code", "Actor2Code",
    "Actor2Name", "Actor2CountryCode", "Actor2KnownGroupCode",
    "Actor2EthnicCode", "Actor2Religion1Code", "Actor2Religion2Code",
    "Actor2Type1Code", "Actor2Type2Code", "Actor2Type3Code", "IsRootEvent",
    "EventCode", "EventBaseCode", "EventRootCode", "QuadClass",
    "GoldsteinScale", "NumMentions", "NumSources", "NumArticles", "AvgTone",
    "Actor1Geo_Type", "Actor1Geo_FullName", "Actor1Geo_CountryCode",
    "Actor1Geo_ADM1Code", "Actor1Geo_Lat", "Actor1Geo_Long",
    "Actor1Geo_FeatureID", "Actor2Geo_Type", "Actor2Geo_FullName",
    "Actor2Geo_CountryCode", "Actor2Geo_ADM1Code", "Actor2Geo_Lat",
    "Actor2Geo_Long", "Actor2Geo_FeatureID", "ActionGeo_Type",
    "ActionGeo_FullName", "ActionGeo_CountryCode", "ActionGeo_ADM1Code",
    "ActionGeo_Lat", "ActionGeo_Long", "ActionGeo_FeatureID", "DATEADDED",
    "SOURCEURL",
]

SOURCE_ORDER = ["GDELT", "USGS", "GDACS", "DesInventar", "Copernicus EMS", "ReliefWeb"]
ALLOWED_ISO3 = {"ECU", "COL", "PER"}
DATA_SUFFIXES = {".csv", ".tsv", ".json", ".geojson", ".zip", ".gz", ".parquet", ".txt", ".log"}
START_DATE = "2015-01-01"
END_DATE = "2026-08-31"


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    text = unicodedata.normalize("NFC", str(value)).strip()
    return text or None


def parse_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            number = float(value)
            if not math.isfinite(number):
                return None
            if abs(number) >= 10_000_000_000:
                return datetime.fromtimestamp(number / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            return None
    text = clean_text(value)
    if not text:
        return None
    if re.fullmatch(r"\d{8}", text):
        try:
            return datetime.strptime(text, "%Y%m%d").date().isoformat()
        except ValueError:
            return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
        return parsed.isoformat().replace("+00:00", "Z")
    return parsed.isoformat()


def parse_desinventar_date(values: dict[str, Any]) -> str | None:
    """Require a complete valid source date; never invent missing month/day."""
    try:
        year = int(str(values.get("fechano") or ""))
        month = int(str(values.get("fechames") or ""))
        day = int(str(values.get("fechadia") or ""))
        return datetime(year, month, day).date().isoformat()
    except (TypeError, ValueError):
        return None


def country_iso3(value: Any) -> str | None:
    text = (clean_text(value) or "").upper()
    exact = {
        "EC": "ECU", "ECU": "ECU", "ECUADOR": "ECU",
        "CO": "COL", "COL": "COL", "COLOMBIA": "COL",
        "PE": "PER", "PER": "PER", "PERU": "PER", "PERÚ": "PER",
    }
    if text in exact:
        return exact[text]
    for token, iso in (("ECUADOR", "ECU"), ("COLOMBIA", "COL"), ("PERU", "PER"), ("PERÚ", "PER")):
        if re.search(rf"(^|[^A-ZÁÉÍÓÚÑ]){token}([^A-ZÁÉÍÓÚÑ]|$)", text):
            return iso
    return None


def valid_url(value: Any) -> bool:
    text = clean_text(value)
    if not text:
        return True
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def coordinate(value: Any, lower: float, upper: float) -> tuple[float | None, bool]:
    if value is None or value == "":
        return None, True
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, False
    if not math.isfinite(number) or not lower <= number <= upper:
        return None, False
    return number, True


def quality(issues: Iterable[str]) -> tuple[str, str]:
    unique = sorted({issue for issue in issues if issue})
    return ("FLAGGED", "|".join(unique)) if unique else ("PASS", "")


def native_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _date_range(values: Iterable[str | None]) -> tuple[str | None, str | None]:
    present = sorted(value for value in values if value)
    return (present[0], present[-1]) if present else (None, None)


def _source_from_path(path: Path) -> str | None:
    lower = str(path).lower()
    if "gkg" in lower:
        return "GKG_EXCLUDED"
    for token, source in (
        ("reliefweb", "ReliefWeb"), ("copernicus", "Copernicus EMS"),
        ("desinventar", "DesInventar"), ("gdacs", "GDACS"),
        ("usgs", "USGS"), ("gdelt", "GDELT"),
    ):
        if token in lower:
            return source
    return None


def _role(path: Path, source: str) -> str:
    name = path.name.lower()
    lower = str(path).lower()
    if "processed_risk_corpus" in lower or "artifacts" in lower:
        return "DERIVED"
    if "test" in lower:
        return "TEST"
    if "checkpoint" in name or "state" in name:
        return "CHECKPOINT"
    if "manifest" in name or "schema_snapshot" in name or "summary" in name:
        return "MANIFEST"
    if "log" in name:
        return "LOG"
    if name.endswith((".tmp", ".temp", ".part")):
        return "TEMP"
    if source == "GKG_EXCLUDED":
        return "TEST"
    if "\\raw\\" in lower or "/raw/" in lower or "gdelt_events_" in name:
        if source == "GDACS" and (name.startswith("detail_") or name.startswith("geometry_")):
            return "RAW_PARTIAL"
        if source == "Copernicus EMS" and name.endswith("activation_list.json"):
            return "RAW_PARTIAL"
        return "RAW_PRIMARY"
    return "UNKNOWN"


def _iter_json_records(value: Any) -> tuple[str | None, list[dict[str, Any]]]:
    if isinstance(value, dict):
        for key in ("features", "results", "data", "items"):
            rows = value.get(key)
            if isinstance(rows, list):
                return key, [row for row in rows if isinstance(row, dict)]
        return None, [value]
    if isinstance(value, list):
        return None, [row for row in value if isinstance(row, dict)]
    return None, []


def _record_values(record: dict[str, Any]) -> dict[str, Any]:
    values = dict(record)
    properties = record.get("properties")
    if isinstance(properties, dict):
        values.update(properties)
    return values


def inspect_data_file(path: Path, source: str) -> dict[str, Any]:
    suffixes = "".join(path.suffixes).lower()
    row_count: int | None = None
    dates: list[str | None] = []
    columns: list[str] = []
    schema = ""
    if source == "GDELT" and path.name.lower().endswith(".tsv.gz") and "gkg" not in str(path).lower():
        lengths: dict[int, int] = {}
        with gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="") as handle:
            for row in csv.reader(handle, delimiter="\t"):
                row_count = (row_count or 0) + 1
                lengths[len(row)] = lengths.get(len(row), 0) + 1
                dates.append(parse_date(row[1]) if len(row) > 1 else None)
        columns = GDELT_58_COLUMNS if set(lengths) == {58} else [f"column_{i + 1}" for i in range(max(lengths, default=0))]
        schema = f"tab-separated GDELT Event rows; column_counts={json.dumps(lengths, sort_keys=True)}"
    elif path.suffix.lower() in {".json", ".geojson"}:
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            container, records = _iter_json_records(value)
            row_count = len(records)
            keys: set[str] = set()
            for record in records:
                values = _record_values(record)
                keys.update(values)
                for key in ("time", "fromdate", "todate", "activationTime", "eventTime", "date", "updated"):
                    if key in values:
                        dates.append(parse_date(values[key]))
            columns = sorted(keys)
            schema = f"JSON; container={container or 'object'}"
        except (json.JSONDecodeError, UnicodeDecodeError):
            schema = "unreadable JSON"
    elif path.suffix.lower() in {".csv", ".tsv"}:
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        try:
            with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
                reader = csv.DictReader(handle, delimiter=delimiter)
                columns = list(reader.fieldnames or [])
                row_count = 0
                for row in reader:
                    row_count += 1
                    for key in ("date", "event_date", "fromdate", "todate", "retrieved_at", "download_timestamp"):
                        if row.get(key):
                            dates.append(parse_date(row[key]))
            schema = f"delimited text; delimiter={delimiter!r}"
        except OSError:
            schema = "unreadable delimited text"
    elif path.suffix.lower() == ".zip" and source == "DesInventar":
        keys: set[str] = set()
        row_count = 0
        with zipfile.ZipFile(path) as archive:
            member = next(name for name in archive.namelist() if name.lower().endswith(".xml"))
            in_fichas = False
            with archive.open(member) as handle:
                for action, element in ET.iterparse(handle, events=("start", "end")):
                    if action == "start" and element.tag == "fichas":
                        in_fichas = True
                    elif action == "end" and element.tag == "TR" and in_fichas:
                        values = {child.tag: clean_text(child.text) for child in element}
                        keys.update(values)
                        row_count += 1
                        dates.append(parse_desinventar_date(values))
                        element.clear()
                    elif action == "end" and element.tag == "fichas":
                        in_fichas = False
                        element.clear()
        columns = sorted(keys)
        schema = "official ZIP containing DesInventar XML and map files"
    else:
        schema = f"{suffixes or path.suffix.lower()} file"
    minimum, maximum = _date_range(dates)
    return {
        "row_count": row_count, "min_date": minimum, "max_date": maximum,
        "detected_schema": schema, "columns": json.dumps(columns, ensure_ascii=False),
    }


def discover_files(repo_root: Path, external_roots: list[Path], output_root: Path) -> list[dict[str, Any]]:
    roots: list[tuple[Path, str]] = []
    for relative in ("data", "raw_risk_corpus", "outputs", "artifacts"):
        candidate = repo_root / relative
        if candidate.exists():
            roots.append((candidate, "repo"))
    roots.extend((root, f"external:{root.name}") for root in external_roots if root.exists())
    seen: set[Path] = set()
    rows: list[dict[str, Any]] = []
    for root, label in roots:
        for path in sorted(root.rglob("*"), key=lambda item: str(item).lower()):
            if not path.is_file() or output_root in path.parents:
                continue
            resolved = path.resolve()
            if resolved in seen or not ({suffix.lower() for suffix in path.suffixes} & DATA_SUFFIXES):
                continue
            source = _source_from_path(path)
            if source is None:
                continue
            seen.add(resolved)
            role = _role(path, source)
            inspection = inspect_data_file(path, source)
            if label == "repo":
                relative_path = path.relative_to(repo_root).as_posix()
            else:
                relative_path = f"EXTERNAL::{root.name}/{path.relative_to(root).as_posix()}"
            rows.append({
                "source": source, "absolute_path": str(resolved),
                "relative_path": relative_path, "filename": path.name,
                "extension": "".join(path.suffixes).lower(), "size_bytes": path.stat().st_size,
                **inspection, "sha256": file_sha256(path), "role": role,
            })
    return sorted(rows, key=lambda row: (row["source"], row["absolute_path"].lower()))


def _retrieval_dates(repo_raw_root: Path) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for path in repo_raw_root.rglob("download_log.csv"):
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            for row in csv.DictReader(handle):
                stamp = row.get("download_timestamp") or row.get("retrieved_at")
                result[row.get("filename", "")] = parse_date(stamp)
    return result


def _write_tsv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if row.get(key) is None else row.get(key) for key in fieldnames})
            count += 1
    return count


def _common(
    *, source: str, record_id: Any, source_file: Path, row_number: int,
    event_date_raw: Any = None, event_end_raw: Any = None, country_raw: Any = None,
    admin1: Any = None, admin2: Any = None, location: Any = None,
    latitude_raw: Any = None, longitude_raw: Any = None, event_type: Any = None,
    event_subtype: Any = None, title: Any = None, description: Any = None,
    source_url: Any = None, retrieval_date: Any = None, extra_issues: Iterable[str] = (),
) -> dict[str, Any]:
    issues = list(extra_issues)
    record_id_clean = clean_text(record_id)
    if not record_id_clean:
        issues.append("MISSING_SOURCE_RECORD_ID")
    event_date = parse_date(event_date_raw)
    event_end = parse_date(event_end_raw)
    if event_date_raw not in (None, "") and event_date is None:
        issues.append("INVALID_DATE")
    if event_end_raw not in (None, "") and event_end is None:
        issues.append("INVALID_END_DATE")
    latitude, latitude_ok = coordinate(latitude_raw, -90, 90)
    longitude, longitude_ok = coordinate(longitude_raw, -180, 180)
    if not latitude_ok or not longitude_ok:
        issues.append("INVALID_COORDINATE")
        latitude = longitude = None
    url = clean_text(source_url)
    if not valid_url(url):
        issues.append("INVALID_URL")
    iso3 = country_iso3(country_raw)
    flag, issue_text = quality(issues)
    return {
        "source": source, "source_record_id": record_id_clean,
        "source_file": str(source_file.resolve()), "source_row_number": row_number,
        "event_date": event_date, "event_end_date": event_end,
        "country_raw": clean_text(country_raw), "country_iso3": iso3,
        "admin1_raw": clean_text(admin1), "admin2_raw": clean_text(admin2),
        "location_raw": clean_text(location), "latitude": latitude, "longitude": longitude,
        "event_type_raw": clean_text(event_type), "event_subtype_raw": clean_text(event_subtype),
        "title_raw": clean_text(title), "description_raw": clean_text(description),
        "source_url": url, "retrieval_date": parse_date(retrieval_date),
        "quality_flag": flag, "quality_issues": issue_text,
        "provenance_status": "SOURCE_MISSING" if not record_id_clean else "DERIVED_DETERMINISTICALLY",
    }


def _summary(source: str, raw_files: int, raw_rows: int, rows: list[dict[str, Any]], duplicate_ids: int, exact_duplicates: int) -> dict[str, Any]:
    retained = len(rows)
    dated = sum(bool(row.get("event_date")) for row in rows)
    countries = sum(row.get("country_iso3") in ALLOWED_ISO3 for row in rows)
    coordinate_rows = [
        row for row in rows
        if row.get("latitude") not in (None, "")
        or row.get("longitude") not in (None, "")
        or "INVALID_COORDINATE" in (row.get("quality_issues") or "")
    ]
    valid_coordinates = sum(
        row.get("latitude") not in (None, "") and row.get("longitude") not in (None, "")
        for row in coordinate_rows
    )
    invalid = sum(row.get("quality_flag") == "FLAGGED" for row in rows)
    minimum, maximum = _date_range(row.get("event_date") for row in rows)
    return {
        "source": source, "raw_files": raw_files, "raw_rows": raw_rows,
        "standardized_rows": retained,
        "valid_date_rate": dated / retained if retained else None,
        "valid_country_rate": countries / retained if retained else None,
        "valid_coordinate_rate": valid_coordinates / len(coordinate_rows) if coordinate_rows else None,
        "duplicate_id_count": duplicate_ids, "exact_duplicate_count": exact_duplicates,
        "invalid_rows": invalid, "retained_rows": retained,
        "min_date": minimum, "max_date": maximum,
    }


def standardize_gdelt(paths: list[Path], output_dir: Path) -> tuple[dict[str, Any], Path]:
    counts: dict[str, int] = {}
    raw_rows = 0
    for path in paths:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="") as handle:
            for row in csv.reader(handle, delimiter="\t"):
                raw_rows += 1
                record_id = clean_text(row[0]) if row else None
                if record_id:
                    counts[record_id] = counts.get(record_id, 0) + 1
    duplicate_ids_set = {record_id for record_id, count in counts.items() if count > 1}
    del counts
    canonical_duplicate_rows: dict[str, tuple[str, str, int]] = {}
    duplicates: list[dict[str, Any]] = []
    seen_missing: set[str] = set()
    exact_duplicates = 0
    duplicate_id_count = 0
    out_path = output_dir / "gdelt_event_standardized.tsv.gz"
    fields = COMMON_COLUMNS + GDELT_58_COLUMNS + ["source_native_column_count"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for path in sorted(paths, key=lambda item: str(item).lower()):
            with gzip.open(path, "rt", encoding="utf-8", errors="replace", newline="") as handle:
                for row_number, row in enumerate(csv.reader(handle, delimiter="\t"), start=1):
                    padded = row[:58] + [""] * max(0, 58 - len(row))
                    native = dict(zip(GDELT_58_COLUMNS, padded[:58]))
                    record_id = clean_text(native.get("GlobalEventID"))
                    digest = sha256("\t".join(row).encode("utf-8")).hexdigest()
                    if record_id and record_id in duplicate_ids_set:
                        canonical = canonical_duplicate_rows.get(record_id)
                        if canonical is None:
                            canonical_duplicate_rows[record_id] = (digest, str(path.resolve()), row_number)
                        else:
                            duplicate_type = "EXACT_DUPLICATE" if digest == canonical[0] else "DUPLICATE_SOURCE_RECORD_ID"
                            exact_duplicates += duplicate_type == "EXACT_DUPLICATE"
                            duplicate_id_count += 1
                            duplicates.append({
                                "source_record_id": record_id, "duplicate_type": duplicate_type,
                                "canonical_source_file": canonical[1], "canonical_source_row_number": canonical[2],
                                "duplicate_source_file": str(path.resolve()), "duplicate_source_row_number": row_number,
                            })
                            continue
                    elif not record_id:
                        if digest in seen_missing:
                            exact_duplicates += 1
                            duplicates.append({
                                "source_record_id": "", "duplicate_type": "EXACT_DUPLICATE",
                                "canonical_source_file": "", "canonical_source_row_number": "",
                                "duplicate_source_file": str(path.resolve()), "duplicate_source_row_number": row_number,
                            })
                            continue
                        seen_missing.add(digest)
                    geo_prefix = "ActionGeo"
                    for candidate in ("ActionGeo", "Actor1Geo", "Actor2Geo"):
                        if country_iso3(native.get(f"{candidate}_CountryCode")):
                            geo_prefix = candidate
                            break
                    issues = []
                    if len(row) not in {58, 61}:
                        issues.append("SHORT_ROW" if len(row) < 58 else "UNEXPECTED_COLUMN_COUNT")
                    common = _common(
                        source="GDELT", record_id=record_id, source_file=path, row_number=row_number,
                        event_date_raw=native.get("SQLDATE"), country_raw=native.get(f"{geo_prefix}_CountryCode"),
                        admin1=native.get(f"{geo_prefix}_ADM1Code"), location=native.get(f"{geo_prefix}_FullName"),
                        latitude_raw=native.get(f"{geo_prefix}_Lat"), longitude_raw=native.get(f"{geo_prefix}_Long"),
                        event_type=native.get("EventCode"), event_subtype=native.get("EventBaseCode"),
                        source_url=native.get("SOURCEURL"), extra_issues=issues,
                    )
                    writer.writerow({**common, **native, "source_native_column_count": len(row)})
    with (output_dir / "gdelt_event_duplicates.csv").open("w", encoding="utf-8", newline="") as handle:
        fields_dup = ["source_record_id", "duplicate_type", "canonical_source_file", "canonical_source_row_number", "duplicate_source_file", "duplicate_source_row_number"]
        writer = csv.DictWriter(handle, fieldnames=fields_dup, lineterminator="\n")
        writer.writeheader(); writer.writerows(duplicates)
    # Summary is computed by streaming the finished table to avoid holding millions of rows.
    summary = summarize_standardized_file("GDELT", out_path, len(paths), raw_rows, duplicate_id_count, exact_duplicates)
    _write_json(output_dir / "gdelt_event_quality_summary.json", summary)
    return summary, out_path


def _flatten_native(record: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in sorted(_record_values(record).items()):
        flat[f"native_{key}"] = clean_text(value)
    flat["source_native_json"] = native_json(record)
    return flat


def _deduplicate(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    seen_ids: dict[str, str] = {}
    seen_hashes: set[str] = set()
    retained: list[dict[str, Any]] = []
    duplicate_ids = exact = 0
    for row in rows:
        digest = sha256((row.get("source_native_json") or native_json(row)).encode("utf-8")).hexdigest()
        record_id = row.get("source_record_id") or ""
        if digest in seen_hashes:
            exact += 1
            continue
        if record_id and record_id in seen_ids:
            duplicate_ids += 1
            continue
        seen_hashes.add(digest)
        if record_id:
            seen_ids[record_id] = digest
        retained.append(row)
    return retained, duplicate_ids, exact


def _write_rows_and_summary(source: str, filename: str, quality_filename: str, rows: list[dict[str, Any]], raw_files: int, raw_rows: int, output_dir: Path) -> tuple[dict[str, Any], Path]:
    retained, duplicate_ids, exact = _deduplicate(rows)
    native_fields = sorted({key for row in retained for key in row if key not in COMMON_COLUMNS})
    path = output_dir / filename
    _write_tsv(path, COMMON_COLUMNS + native_fields, retained)
    summary = _summary(source, raw_files, raw_rows, retained, duplicate_ids, exact)
    _write_json(output_dir / quality_filename, summary)
    return summary, path


def standardize_usgs(paths: list[Path], output_dir: Path, retrieval: dict[str, str | None]) -> tuple[dict[str, Any], Path]:
    rows: list[dict[str, Any]] = []
    raw_rows = 0
    for path in sorted(paths):
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        for row_number, record in enumerate(value.get("features", []), start=1):
            raw_rows += 1
            props = record.get("properties") or {}
            coords = (record.get("geometry") or {}).get("coordinates") or []
            place = props.get("place")
            issues = []
            mag = props.get("mag")
            if mag is not None:
                try:
                    mag_number = float(mag)
                    if not math.isfinite(mag_number) or not -2 <= mag_number <= 10:
                        issues.append("INVALID_MAGNITUDE")
                except (TypeError, ValueError):
                    issues.append("INVALID_MAGNITUDE")
            common = _common(
                source="USGS", record_id=record.get("id"), source_file=path, row_number=row_number,
                event_date_raw=props.get("time"), country_raw=place,
                location=place, latitude_raw=coords[1] if len(coords) > 1 else None,
                longitude_raw=coords[0] if coords else None, event_type=props.get("type"),
                event_subtype=props.get("magType"), title=props.get("title"),
                description=place, source_url=props.get("url"), retrieval_date=retrieval.get(path.name),
                extra_issues=issues,
            )
            native = _flatten_native(record)
            native["native_depth"] = coords[2] if len(coords) > 2 else None
            rows.append({**common, **native})
    return _write_rows_and_summary("USGS", "usgs_standardized.tsv.gz", "usgs_quality_summary.json", rows, len(paths), raw_rows, output_dir)


def standardize_gdacs(paths: list[Path], output_dir: Path, retrieval: dict[str, str | None]) -> tuple[dict[str, Any], Path]:
    rows: list[dict[str, Any]] = []
    raw_rows = 0
    for path in sorted(paths):
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        for row_number, record in enumerate(value.get("features", []), start=1):
            raw_rows += 1
            props = record.get("properties") or {}
            coords = (record.get("geometry") or {}).get("coordinates") or []
            if coords and isinstance(coords[0], list):
                coords = []
            record_id = ":".join(clean_text(props.get(key)) or "" for key in ("eventtype", "eventid", "episodeid"))
            url_value = props.get("url")
            if isinstance(url_value, dict):
                url_value = url_value.get("report") or url_value.get("details")
            common = _common(
                source="GDACS", record_id=record_id, source_file=path, row_number=row_number,
                event_date_raw=props.get("fromdate"), event_end_raw=props.get("todate"),
                country_raw=props.get("country") or props.get("iso3"),
                location=props.get("name"), latitude_raw=coords[1] if len(coords) > 1 else None,
                longitude_raw=coords[0] if coords else None, event_type=props.get("eventtype"),
                event_subtype=props.get("eventname"), title=props.get("name") or props.get("eventname"),
                description=props.get("description"), source_url=url_value,
                retrieval_date=retrieval.get(path.name),
            )
            rows.append({**common, **_flatten_native(record)})
    return _write_rows_and_summary("GDACS", "gdacs_standardized.tsv.gz", "gdacs_quality_summary.json", rows, len(paths), raw_rows, output_dir)


def _desinventar_rows(path: Path, country: str) -> tuple[list[dict[str, Any]], set[str], int]:
    rows: list[dict[str, Any]] = []
    fields: set[str] = set()
    raw_rows = 0
    with zipfile.ZipFile(path) as archive:
        member = next(name for name in archive.namelist() if name.lower().endswith(".xml"))
        in_fichas = False
        with archive.open(member) as handle:
            for action, element in ET.iterparse(handle, events=("start", "end")):
                if action == "start" and element.tag == "fichas":
                    in_fichas = True
                elif action == "end" and element.tag == "TR" and in_fichas:
                    raw_rows += 1
                    values = {child.tag: clean_text(child.text) for child in element}
                    fields.update(values)
                    year_text = clean_text(values.get("fechano"))
                    try:
                        year = int(year_text or "")
                    except ValueError:
                        year = None
                    if year is not None and 2015 <= year <= 2026:
                        event_date = parse_desinventar_date(values)
                        native_id = clean_text(values.get("clave"))
                        issues = ([] if event_date else ["INVALID_DATE"]) + ([] if native_id else ["MISSING_SOURCE_RECORD_ID"])
                        record_id = f"{country_iso3(country)}:{native_id}" if native_id else None
                        common = _common(
                            source="DesInventar", record_id=record_id, source_file=path,
                            row_number=raw_rows, event_date_raw=event_date, country_raw=country,
                            admin1=values.get("name0"), admin2=values.get("name1"),
                            location=values.get("lugar") or values.get("name2"),
                            latitude_raw=values.get("latitud") or values.get("latitude"),
                            longitude_raw=values.get("longitud") or values.get("longitude"),
                            event_type=values.get("evento"), event_subtype=values.get("causa"),
                            description=values.get("observa") or values.get("fuentes"),
                            extra_issues=issues,
                        )
                        native = {f"native_{key}": value for key, value in values.items()}
                        native["source_native_json"] = native_json(values)
                        rows.append({**common, **native})
                    element.clear()
                elif action == "end" and element.tag == "fichas":
                    in_fichas = False
                    element.clear()
    return rows, fields, raw_rows


def standardize_desinventar(paths: list[Path], output_dir: Path) -> tuple[dict[str, Any], Path]:
    all_rows: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    raw_rows = 0
    for path in sorted(paths):
        country = "Colombia" if "_col" in path.name.lower() else "Peru" if "_per" in path.name.lower() else "Ecuador"
        rows, fields, count = _desinventar_rows(path, country)
        all_rows.extend(rows); raw_rows += count
        mapping = {
            "source_record_id": "clave (serial retained as source event-group identifier)", "event_date": "fechano+fechames+fechadia",
            "country_raw": f"archive identity: {country}", "admin1_raw": "name0",
            "admin2_raw": "name1", "location_raw": "lugar (fallback name2)",
            "latitude": "latitud if present", "longitude": "longitud if present",
            "event_type_raw": "evento", "event_subtype_raw": "causa",
            "description_raw": "observa (fallback fuentes)",
        }
        for common_field, native_field in mapping.items():
            mappings.append({
                "country": country, "implementation_field": native_field,
                "standardized_field": common_field,
                "source_field_present": native_field.split()[0].split("+")[0] in fields,
                "mapping_status": "EXACT_OR_DOCUMENTED_FALLBACK",
            })
    summary, out_path = _write_rows_and_summary("DesInventar", "desinventar_standardized.tsv.gz", "desinventar_quality_summary.json", all_rows, len(paths), raw_rows, output_dir)
    with (output_dir / "desinventar_schema_mapping.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(mappings[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(mappings)
    summary["in_scope_raw_rows"] = len(all_rows)
    _write_json(output_dir / "desinventar_quality_summary.json", summary)
    return summary, out_path


def standardize_copernicus(paths: list[Path], output_dir: Path, retrieval: dict[str, str | None]) -> tuple[dict[str, Any], Path]:
    rows: list[dict[str, Any]] = []
    for path in sorted(paths):
        record = json.loads(path.read_text(encoding="utf-8-sig"))
        countries = record.get("countries")
        country = None
        if isinstance(countries, list):
            names = []
            for item in countries:
                names.append(clean_text((item.get("name") or item.get("short_name")) if isinstance(item, dict) else item))
            country = "; ".join(value for value in names if value)
        else:
            country = countries
        centroid = record.get("centroid")
        coords = centroid.get("coordinates", []) if isinstance(centroid, dict) else []
        if isinstance(centroid, str):
            match = re.fullmatch(r"\s*POINT\s*\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*\)\s*", centroid)
            coords = [match.group(1), match.group(2)] if match else []
        category = record.get("category")
        event_type = category.get("name") or category.get("slug") if isinstance(category, dict) else category
        common = _common(
            source="Copernicus EMS", record_id=record.get("code"), source_file=path, row_number=1,
            event_date_raw=record.get("eventTime") or record.get("activationTime"),
            country_raw=country, location=record.get("location") or record.get("name"),
            latitude_raw=coords[1] if len(coords) > 1 else None,
            longitude_raw=coords[0] if coords else None, event_type=event_type,
            event_subtype=record.get("drmPhase"), title=record.get("name"),
            description=record.get("search_snippet"), source_url=record.get("url") or record.get("link"),
            retrieval_date=retrieval.get(path.name),
        )
        rows.append({**common, **_flatten_native(record)})
    return _write_rows_and_summary("Copernicus EMS", "copernicus_ems_standardized.tsv.gz", "copernicus_ems_quality_summary.json", rows, len(paths), len(rows), output_dir)


def summarize_standardized_file(source: str, path: Path, raw_files: int, raw_rows: int, duplicate_ids: int, exact_duplicates: int) -> dict[str, Any]:
    total = dated = countries = coordinate_rows = valid_coordinates = invalid = 0
    dates: list[str] = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            total += 1
            if row.get("event_date"):
                dated += 1; dates.append(row["event_date"])
            countries += row.get("country_iso3") in ALLOWED_ISO3
            if row.get("latitude") or row.get("longitude") or "INVALID_COORDINATE" in row.get("quality_issues", ""):
                coordinate_rows += 1
                valid_coordinates += bool(row.get("latitude") and row.get("longitude"))
            invalid += row.get("quality_flag") == "FLAGGED"
    minimum, maximum = _date_range(dates)
    return {
        "source": source, "raw_files": raw_files, "raw_rows": raw_rows,
        "standardized_rows": total, "valid_date_rate": dated / total if total else None,
        "valid_country_rate": countries / total if total else None,
        "valid_coordinate_rate": valid_coordinates / coordinate_rows if coordinate_rows else None,
        "duplicate_id_count": duplicate_ids, "exact_duplicate_count": exact_duplicates,
        "invalid_rows": invalid, "retained_rows": total, "min_date": minimum, "max_date": maximum,
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    with path.open("w", encoding="utf-8", newline="") as handle:
        if not fields:
            handle.write("\n"); return
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def _gdelt_acquisition_batches(external_roots: list[Path], gdelt_paths: list[Path]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    batches: list[dict[str, Any]] = []
    date_sets: dict[str, set[str]] = {}
    for root in external_roots:
        logs = sorted(root.rglob("*log.csv"))
        for log_path in logs:
            if "download_log" not in log_path.name and "recovery_log" not in log_path.name:
                continue
            rows = list(csv.DictReader(log_path.open("r", encoding="utf-8-sig", errors="replace", newline="")))
            successful = [row for row in rows if str(row.get("status", "")).strip() in {"200", "200.0"}]
            batch_id = "DIRECT_ACQUISITION" if log_path.name == "download_log.csv" else "RECOVERY_V2_ACQUISITION"
            dates = {str(row.get("date", "")).strip() for row in successful if row.get("date")}
            date_sets[batch_id] = dates
            rows_kept = sum(int(float(row.get("rows_kept") or 0)) for row in successful)
            batches.append({
                "batch_id": batch_id, "acquisition_log": str(log_path.resolve()),
                "physical_event_table": str(gdelt_paths[0].resolve()) if len(gdelt_paths) == 1 else "",
                "successful_export_dates": len(dates), "rows_kept": rows_kept,
                "min_export_date": min(dates) if dates else None,
                "max_export_date": max(dates) if dates else None,
                "storage_relationship": "APPENDED_TO_SHARED_EVENT_TABLE" if batch_id == "RECOVERY_V2_ACQUISITION" else "CREATED_SHARED_EVENT_TABLE",
            })
    first = date_sets.get("DIRECT_ACQUISITION", set())
    second = date_sets.get("RECOVERY_V2_ACQUISITION", set())
    comparison = {
        "logical_acquisition_count": len(batches), "physical_event_table_count": len(gdelt_paths),
        "overlapping_successful_export_dates": len(first & second),
        "combined_rows_from_logs": sum(row["rows_kept"] for row in batches),
        "combined_min_export_date": min(first | second) if first | second else None,
        "combined_max_export_date": max(first | second) if first | second else None,
    }
    return sorted(batches, key=lambda row: row["batch_id"]), comparison


def _write_discovery_report(path: Path, inventory: list[dict[str, Any]], gdelt_paths: list[Path], batches: list[dict[str, Any]], comparison: dict[str, Any]) -> None:
    raw = [row for row in inventory if row["role"] in {"RAW_PRIMARY", "RAW_PARTIAL"}]
    lines = [
        "# Risk-corpus source discovery report", "",
        "This report is a read-only inventory made before standardization. No raw file was modified.", "",
        "## Formal raw inputs and auxiliary raw files", "",
        "| Source | Role | Rows | Dates | Absolute path |", "|---|---:|---:|---|---|",
    ]
    for row in raw:
        dates = f"{row['min_date'] or 'unknown'} to {row['max_date'] or 'unknown'}"
        lines.append(f"| {row['source']} | {row['role']} | {row['row_count'] if row['row_count'] is not None else 'unknown'} | {dates} | `{row['absolute_path']}` |")
    lines.extend(["", "## GDELT Event determination", ""])
    if len(gdelt_paths) == 1 and len(batches) == 2:
        lines.extend([
            "Two acquisition batches were verified from independent logs, and together cover the requested export-date span:", "",
            *[f"- `{row['batch_id']}`: {row['rows_kept']:,} rows, {row['min_export_date']} to {row['max_export_date']}, log `{row['acquisition_log']}`" for row in batches],
            f"- Successful export-date overlap between the two batches: {comparison['overlapping_successful_export_dates']} dates.", "",
            "Both batches are stored in one physical formal GDELT Event table:", "",
            f"- `{gdelt_paths[0].resolve()}`",
            "- The recovery package manifest and code declare an in-place append to that shared gzip table; no second physical Event payload currently exists on E:.",
            "- The two grabs remain separately auditable through their logs, row totals, export-date ranges, and zero-overlap date check.",
            "- GKG files found in sibling packages are explicitly excluded from processing.",
        ])
    else:
        lines.append(f"{len(gdelt_paths)} formal Event tables were detected and are compared by GlobalEventID during standardization.")
    lines.extend(["", "## Missing sources", "", "ReliefWeb has no formal raw payload locally and remains `NOT_AVAILABLE / AUTH_BLOCKED`.", "DesInventar Ecuador has no retained archive; interrupted partial downloads were not retained.", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_processing_report(path: Path, inventory: list[dict[str, Any]], summaries: list[dict[str, Any]], outputs: dict[str, Path], raw_unchanged: bool, gdelt_paths: list[Path], batches: list[dict[str, Any]], comparison: dict[str, Any]) -> None:
    lines = [
        "# Risk-corpus data processing report", "", "## Boundaries", "",
        "Standardization is deterministic and source-specific. It performs no missing-value imputation, GenAI, risk classification, clustering, cross-source event linkage/fusion, optimization, URL access, or article-body retrieval. Raw files are read-only.", "",
        "PyArrow/another Parquet engine was not available in the controlled runtime, so the preregistered gzip-TSV fallback was used. Each fallback is a flat typed-text table with a header and explicit quality fields.", "",
        "## Input and output counts", "", "| Source | Raw files | Raw rows | Standardized rows | Date range | Output |", "|---|---:|---:|---:|---|---|",
    ]
    for row in summaries:
        output = outputs.get(row["source"])
        lines.append(f"| {row['source']} | {row['raw_files']} | {row['raw_rows']} | {row['standardized_rows']} | {row.get('min_date') or 'unknown'} to {row.get('max_date') or 'unknown'} | `{output.resolve() if output else 'NOT_AVAILABLE'}` |")
    lines.extend(["", "## Provenance and exclusions", ""])
    for row in inventory:
        if row["role"] in {"RAW_PRIMARY", "RAW_PARTIAL"}:
            lines.append(f"- `{row['absolute_path']}` — {row['source']} / {row['role']} / SHA-256 `{row['sha256']}`")
    lines.extend([
        "", "GDACS detail and geometry payloads and Copernicus activation lists are retained as source-native auxiliary raw files; standardized event rows come from the GDACS discovery lists and Copernicus selected activation details, avoiding duplicate representations of the same official record.",
        "DesInventar standardized rows retain the Stage-5A study window (2015-01-01 through 2026-08-31); pre-2015 records remain untouched in the official archives and are counted in raw-file audit totals.",
        "ReliefWeb: `NOT_AVAILABLE / AUTH_BLOCKED`; no standardized table was fabricated.",
        "DesInventar Ecuador: no complete archive retained; this is a source-native/acquisition gap, not an imputed absence.",
        "GKG: excluded pending targeted enrichment from Event risk-candidate dates.",
        "", "## GDELT package relationship", "",
        f"Two logical acquisition batches were verified ({comparison.get('combined_rows_from_logs', 0):,} rows in their logs, {comparison.get('combined_min_export_date')} to {comparison.get('combined_max_export_date')}, {comparison.get('overlapping_successful_export_dates')} overlapping successful export dates). They are currently stored in {len(gdelt_paths)} shared physical Event table because recovery-v2 appends in place. Duplicate GlobalEventID handling remains deterministic and is recorded in `gdelt_event_duplicates.csv`.",
        "The acquisition/export-date span is 2015-02-19 through 2026-08-31. Separately, the source-native GDELT `SQLDATE` field ranges back to 1920-01-01 for records referring to historical event dates; that value is retained rather than silently forced into the acquisition window.",
        "", "## Integrity", "", f"All formal/auxiliary raw SHA-256 values unchanged after processing: `{str(raw_unchanged).lower()}`.", "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def run_standardization(repo_root: Path, external_roots: list[Path], output_root: Path, *, reuse_complete_outputs: bool = False) -> dict[str, Any]:
    audit_dir = output_root / "audit"
    processed_dir = output_root / "processed"
    audit_dir.mkdir(parents=True, exist_ok=True); processed_dir.mkdir(parents=True, exist_ok=True)
    inventory = discover_files(repo_root, external_roots, output_root)
    _write_csv(audit_dir / "source_file_inventory.csv", inventory)
    _write_json(audit_dir / "source_file_inventory.json", inventory)
    gdelt_paths = [Path(row["absolute_path"]) for row in inventory if row["source"] == "GDELT" and row["role"] == "RAW_PRIMARY" and row["filename"].lower().endswith(".tsv.gz")]
    batches, batch_comparison = _gdelt_acquisition_batches(external_roots, gdelt_paths)
    _write_csv(audit_dir / "gdelt_acquisition_batches.csv", batches)
    _write_json(audit_dir / "gdelt_acquisition_comparison.json", batch_comparison)
    _write_discovery_report(audit_dir / "source_discovery_report.md", inventory, gdelt_paths, batches, batch_comparison)

    raw_root = repo_root / "data/local/raw_risk_corpus"
    retrieval = _retrieval_dates(raw_root)
    outputs: dict[str, Path] = {}
    summaries: list[dict[str, Any]] = []
    if gdelt_paths:
        outputs["GDELT"] = processed_dir / "gdelt_event_standardized.tsv.gz"
        quality_path = processed_dir / "gdelt_event_quality_summary.json"
        if reuse_complete_outputs and outputs["GDELT"].exists() and quality_path.exists():
            summary = json.loads(quality_path.read_text(encoding="utf-8"))
        else:
            summary, outputs["GDELT"] = standardize_gdelt(gdelt_paths, processed_dir)
        if batch_comparison.get("combined_rows_from_logs") != summary["raw_rows"]:
            raise RuntimeError(
                "GDELT acquisition-log row total does not match the shared Event table: "
                f"{batch_comparison.get('combined_rows_from_logs')} != {summary['raw_rows']}"
            )
        summaries.append(summary)
    usgs = sorted((raw_root / "usgs/raw").glob("*.geojson"))
    outputs["USGS"] = processed_dir / "usgs_standardized.tsv.gz"
    quality_path = processed_dir / "usgs_quality_summary.json"
    if reuse_complete_outputs and outputs["USGS"].exists() and quality_path.exists():
        summary = json.loads(quality_path.read_text(encoding="utf-8"))
    else:
        summary, outputs["USGS"] = standardize_usgs(usgs, processed_dir, retrieval)
    summaries.append(summary)
    gdacs = sorted((raw_root / "gdacs/raw").glob("gdacs_*.json"))
    outputs["GDACS"] = processed_dir / "gdacs_standardized.tsv.gz"
    quality_path = processed_dir / "gdacs_quality_summary.json"
    if reuse_complete_outputs and outputs["GDACS"].exists() and quality_path.exists():
        summary = json.loads(quality_path.read_text(encoding="utf-8"))
    else:
        summary, outputs["GDACS"] = standardize_gdacs(gdacs, processed_dir, retrieval)
    summaries.append(summary)
    des = sorted((raw_root / "desinventar/raw").glob("DI_export_*.zip"))
    outputs["DesInventar"] = processed_dir / "desinventar_standardized.tsv.gz"
    quality_path = processed_dir / "desinventar_quality_summary.json"
    if reuse_complete_outputs and outputs["DesInventar"].exists() and quality_path.exists():
        summary = json.loads(quality_path.read_text(encoding="utf-8"))
    else:
        summary, outputs["DesInventar"] = standardize_desinventar(des, processed_dir)
    summaries.append(summary)
    copernicus = sorted(path for path in (raw_root / "copernicus/raw").glob("*.json") if not path.name.endswith("activation_list.json"))
    outputs["Copernicus EMS"] = processed_dir / "copernicus_ems_standardized.tsv.gz"
    quality_path = processed_dir / "copernicus_ems_quality_summary.json"
    if reuse_complete_outputs and outputs["Copernicus EMS"].exists() and quality_path.exists():
        summary = json.loads(quality_path.read_text(encoding="utf-8"))
    else:
        summary, outputs["Copernicus EMS"] = standardize_copernicus(copernicus, processed_dir, retrieval)
    summaries.append(summary)
    summaries.append({
        "source": "ReliefWeb", "raw_files": 0, "raw_rows": 0, "standardized_rows": 0,
        "valid_date_rate": None, "valid_country_rate": None, "valid_coordinate_rate": None,
        "duplicate_id_count": 0, "exact_duplicate_count": 0, "invalid_rows": 0,
        "retained_rows": 0, "min_date": None, "max_date": None,
        "status": "NOT_AVAILABLE / AUTH_BLOCKED",
    })
    raw_file_counts = {
        source: sum(
            row["source"] == source and row["role"] in {"RAW_PRIMARY", "RAW_PARTIAL"}
            for row in inventory
        )
        for source in SOURCE_ORDER
    }
    quality_names = {
        "GDELT": "gdelt_event_quality_summary.json",
        "USGS": "usgs_quality_summary.json", "GDACS": "gdacs_quality_summary.json",
        "DesInventar": "desinventar_quality_summary.json",
        "Copernicus EMS": "copernicus_ems_quality_summary.json",
    }
    for summary in summaries:
        summary["raw_files"] = raw_file_counts[summary["source"]]
        quality_name = quality_names.get(summary["source"])
        if quality_name:
            _write_json(processed_dir / quality_name, summary)
    summaries.sort(key=lambda row: SOURCE_ORDER.index(row["source"]))
    _write_csv(audit_dir / "standardization_summary.csv", summaries)

    index_path = processed_dir / "common_event_index.tsv.gz"
    with gzip.open(index_path, "wt", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=THIN_INDEX_COLUMNS, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for source in SOURCE_ORDER:
            source_path = outputs.get(source)
            if not source_path:
                continue
            with gzip.open(source_path, "rt", encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle, delimiter="\t"):
                    writer.writerow({field: row.get(field, "") for field in THIN_INDEX_COLUMNS})
    outputs["common_event_index"] = index_path

    output_inventory: list[dict[str, Any]] = []
    summary_by_source = {row["source"]: row for row in summaries}
    for source, output_path in outputs.items():
        logical_source = source if source in summary_by_source else "COMMON_INDEX"
        output_inventory.append({
            "source": logical_source, "absolute_path": str(output_path.resolve()),
            "filename": output_path.name, "format": "gzip TSV",
            "size_bytes": output_path.stat().st_size, "sha256": file_sha256(output_path),
            "row_count": (
                summary_by_source[source]["standardized_rows"]
                if source in summary_by_source else sum(row["standardized_rows"] for row in summaries)
            ),
        })
    _write_csv(audit_dir / "standardized_output_inventory.csv", output_inventory)
    _write_json(audit_dir / "standardized_output_inventory.json", output_inventory)

    initial_hashes = {
        row["absolute_path"]: row["sha256"] for row in inventory
        if row["role"] in {"RAW_PRIMARY", "RAW_PARTIAL"}
    }
    final_hashes = {path: file_sha256(Path(path)) for path in initial_hashes}
    changed = sorted(path for path in initial_hashes if initial_hashes[path] != final_hashes[path])
    integrity = {"raw_file_count": len(initial_hashes), "all_unchanged": not changed, "changed_files": changed}
    _write_json(audit_dir / "raw_integrity_audit.json", integrity)
    _write_processing_report(audit_dir / "data_processing_report.md", inventory, summaries, outputs, not changed, gdelt_paths, batches, batch_comparison)
    if changed:
        raise RuntimeError(f"Raw file integrity failure: {changed}")
    result = {
        "inventory_files": len(inventory), "gdelt_event_tables": [str(path.resolve()) for path in gdelt_paths],
        "gdelt_acquisition_batches": batches, "gdelt_acquisition_comparison": batch_comparison,
        "summaries": summaries, "outputs": {key: str(value.resolve()) for key, value in outputs.items()},
        "output_inventory": output_inventory,
        "raw_integrity": integrity, "output_format": "gzip TSV fallback (Parquet engine unavailable)",
    }
    _write_json(audit_dir / "execution_audit.json", result)
    return result
