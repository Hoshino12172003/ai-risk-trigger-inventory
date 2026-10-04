"""Bounded GKG revalidation for the six demand-risk targets."""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Iterable, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

from .gkg_unmatched_diagnostic import (
    METHOD_PRIORITY,
    classify_candidate,
    domain,
    normalize_url,
    title_from_url,
)


GDELT_BASE = "https://data.gdeltproject.org/gdeltv2"
GKG_SOURCE_COMMON_NAME_INDEX = 3
GKG_DOCUMENT_URL_INDEX = 4
SLOTS_PER_DAY = 96


def extract_gkg_document_url(fields: list[str]) -> str:
    """Return GKG 2.1 DocumentIdentifier, never SourceCommonName."""
    if len(fields) <= GKG_DOCUMENT_URL_INDEX:
        return ""
    return fields[GKG_DOCUMENT_URL_INDEX].strip()


def day_slots(day: str) -> list[str]:
    start = datetime.strptime(day, "%Y-%m-%d")
    return [(start + timedelta(minutes=15 * index)).strftime("%Y%m%d%H%M%S") for index in range(SLOTS_PER_DAY)]


def expansion_days(target_days: Iterable[str], offsets: Iterable[int]) -> list[str]:
    return sorted({(date.fromisoformat(day) + timedelta(days=offset)).isoformat() for day in target_days for offset in offsets})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_one(timestamp: str, raw_dir: Path, timeout: int = 90, retries: int = 3) -> dict[str, Any]:
    filename = f"{timestamp}.gkg.csv.zip"
    destination = raw_dir / filename
    remote_url = f"{GDELT_BASE}/{filename}"
    if destination.exists() and destination.stat().st_size:
        status, error = "EXISTS", ""
    else:
        raw_dir.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        if partial.exists():
            partial.unlink()
        status, error = "ERROR", "unknown"
        for attempt in range(1, retries + 1):
            try:
                request = Request(remote_url, headers={"User-Agent": "AcademicResearch-GKG-Revalidation/1.0"})
                with urlopen(request, timeout=timeout) as response, partial.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                partial.replace(destination)
                status, error = "DOWNLOADED", ""
                break
            except HTTPError as exc:
                if partial.exists():
                    partial.unlink()
                if exc.code == 404:
                    status, error = "MISSING", "HTTP 404"
                    break
                error = f"HTTP {exc.code}"
            except (URLError, TimeoutError, ConnectionError, OSError) as exc:
                if partial.exists():
                    partial.unlink()
                error = repr(exc)
            if attempt < retries:
                time.sleep(attempt)
    return {
        "timestamp": timestamp,
        "date": f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}",
        "remote_url": remote_url,
        "local_file": str(destination.resolve()),
        "download_status": status,
        "error": error,
        "file_size": destination.stat().st_size if destination.exists() else "",
        "sha256": sha256_file(destination) if destination.exists() else "",
    }


def download_days(days: Iterable[str], raw_dir: Path, workers: int = 8) -> list[dict[str, Any]]:
    timestamps = sorted({timestamp for day in days for timestamp in day_slots(day)})
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_download_one, timestamp, raw_dir): timestamp for timestamp in timestamps}
        for future in as_completed(futures):
            rows.append(future.result())
    return sorted(rows, key=lambda row: row["timestamp"])


def iter_gkg_urls(path: Path) -> Iterator[tuple[int, str]]:
    with zipfile.ZipFile(path) as archive:
        for member in archive.namelist():
            if member.endswith("/"):
                continue
            with archive.open(member) as stream:
                for row_number, raw_line in enumerate(stream, start=1):
                    fields = raw_line.decode("utf-8", errors="replace").rstrip("\r\n").split("\t")
                    value = extract_gkg_document_url(fields)
                    if value:
                        yield row_number, value


def _archive_day(path: Path) -> str:
    stamp = path.name[:8]
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"


def _offset(target_day: str, archive_day: str) -> int:
    return (date.fromisoformat(archive_day) - date.fromisoformat(target_day)).days


def scan_archives(
    archives: Iterable[Path], targets: list[dict[str, str]], allowed_offsets: set[int]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_domain: dict[str, list[dict[str, str]]] = defaultdict(list)
    for target in targets:
        by_domain[domain(target["SOURCEURL"])].append(target)

    def scan_one(archive: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        archive_reliable: list[dict[str, Any]] = []
        archive_candidates: list[dict[str, Any]] = []
        archive_day = _archive_day(archive)
        eligible_by_domain: dict[str, list[tuple[dict[str, str], int]]] = defaultdict(list)
        for target_domain, domain_targets in by_domain.items():
            for target in domain_targets:
                offset = _offset(target["event_date"], archive_day)
                if offset in allowed_offsets:
                    eligible_by_domain[target_domain].append((target, offset))
        if not eligible_by_domain:
            return archive_reliable, archive_candidates
        archive_hash = sha256_file(archive)
        for row_number, candidate_url in iter_gkg_urls(archive):
            lowered_url = candidate_url.lower()
            if not any(target_domain in lowered_url for target_domain in eligible_by_domain):
                continue
            candidate_domain = domain(candidate_url)
            if candidate_domain not in eligible_by_domain:
                continue
            for target, offset in eligible_by_domain[candidate_domain]:
                method, confidence, similarity = classify_candidate(target["SOURCEURL"], candidate_url)
                if method not in {"EXACT_URL", "NORMALIZED_URL", "CANONICAL_PATH", "TITLE_DOMAIN_CANDIDATE"}:
                    continue
                row = {
                    "target_id": target["GlobalEventID"],
                    "target_date": target["event_date"],
                    "domain": domain(target["SOURCEURL"]),
                    "original_url": target["SOURCEURL"],
                    "matched_gkg_document_url": candidate_url,
                    "match_method": method,
                    "confidence": confidence,
                    "date_offset": offset,
                    "gkg_timestamp": archive.name[:14],
                    "gkg_file": archive.name,
                    "gkg_sha256": archive_hash,
                    "gkg_row_number": row_number,
                    "candidate_title": title_from_url(candidate_url),
                    "title_similarity": f"{similarity:.6f}",
                }
                (archive_candidates if method == "TITLE_DOMAIN_CANDIDATE" else archive_reliable).append(row)
        return archive_reliable, archive_candidates

    reliable: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        for archive_reliable, archive_candidates in executor.map(scan_one, sorted(archives)):
            reliable.extend(archive_reliable)
            candidates.extend(archive_candidates)
    order = lambda row: (row["target_id"], METHOD_PRIORITY[row["match_method"]], abs(row["date_offset"]), row["gkg_timestamp"], row["matched_gkg_document_url"])
    return sorted(reliable, key=order), sorted(candidates, key=order)


def _write_table(path: Path, rows: list[dict[str, Any]], fields: list[str], delimiter: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter=delimiter, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_revalidation(processed_root: Path, workers: int = 8) -> dict[str, Any]:
    old_root = processed_root / "gkg_enrichment/flash_full_day"
    output_root = processed_root / "gkg_enrichment/gkg_revalidation_6targets"
    raw_dir = output_root / "raw_gkg"
    output_root.mkdir(parents=True, exist_ok=True)
    formal_paths = [old_root / name for name in ("full_day_download_manifest.csv", "full_day_url_matches.tsv", "unmatched_targets.tsv", "full_day_summary.json", "checkpoint.json")]
    formal_before = {str(path.resolve()): sha256_file(path) for path in formal_paths}
    with (old_root / "unmatched_targets.tsv").open(encoding="utf-8", newline="") as stream:
        targets = list(csv.DictReader(stream, delimiter="\t"))
    if len(targets) != 6:
        raise RuntimeError(f"Expected six targets, found {len(targets)}")

    all_manifest: dict[str, dict[str, Any]] = {}
    reliable_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    matched_ids: set[str] = set()
    phases = [("TARGET_DATE", {0}), ("PLUS_MINUS_1", {-1, 1}), ("PLUS_MINUS_2", {-2, 2})]
    target_days = {target["event_date"] for target in targets}

    for phase, offsets in phases:
        unresolved = [target for target in targets if target["GlobalEventID"] not in matched_ids]
        if not unresolved:
            break
        days = expansion_days({target["event_date"] for target in unresolved}, offsets)
        phase_manifest = download_days(days, raw_dir, workers=workers)
        for row in phase_manifest:
            row["phase"] = phase
            all_manifest.setdefault(row["timestamp"], row)
        available = [raw_dir / f"{timestamp}.gkg.csv.zip" for timestamp in all_manifest if (raw_dir / f"{timestamp}.gkg.csv.zip").exists()]
        phase_reliable, phase_candidates = scan_archives(available, unresolved, offsets)
        reliable_rows.extend(phase_reliable)
        candidate_rows.extend(phase_candidates)
        matched_ids.update(row["target_id"] for row in phase_reliable)

    reliable_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    candidate_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in reliable_rows:
        reliable_by_id[row["target_id"]].append(row)
    for row in candidate_rows:
        candidate_by_id[row["target_id"]].append(row)

    result_rows = []
    unmatched_rows = []
    candidate_only = 0
    for target in sorted(targets, key=lambda row: row["GlobalEventID"]):
        target_id = target["GlobalEventID"]
        matches = reliable_by_id[target_id]
        best = matches[0] if matches else (candidate_by_id[target_id][0] if candidate_by_id[target_id] else {})
        if not matches:
            unmatched_rows.append(target)
            if best:
                candidate_only += 1
        result_rows.append({
            "target_id": target_id,
            "target_date": target["event_date"],
            "domain": domain(target["SOURCEURL"]),
            "original_url": target["SOURCEURL"],
            "exact_match": str(any(row["match_method"] == "EXACT_URL" for row in matches)).lower(),
            "normalized_match": str(any(row["match_method"] == "NORMALIZED_URL" for row in matches)).lower(),
            "canonical_path_match": str(any(row["match_method"] == "CANONICAL_PATH" for row in matches)).lower(),
            "date_offset": best.get("date_offset", ""),
            "matched_gkg_document_url": best.get("matched_gkg_document_url", ""),
            "match_method": best.get("match_method", "NO_MATCH"),
            "confidence": best.get("confidence", "NONE"),
            "failure_reason": "" if matches else ("TITLE_SIMILARITY_REQUIRES_HUMAN_REVIEW" if best else "NO_URL_OR_TITLE_DOMAIN_MATCH_WITHIN_TARGET_DATE_PLUS_MINUS_2_DAYS"),
        })

    manifest_fields = ["timestamp", "date", "phase", "remote_url", "local_file", "download_status", "error", "file_size", "sha256"]
    match_fields = ["target_id", "target_date", "domain", "original_url", "matched_gkg_document_url", "match_method", "confidence", "date_offset", "gkg_timestamp", "gkg_file", "gkg_sha256", "gkg_row_number", "candidate_title", "title_similarity"]
    _write_table(output_root / "download_manifest.csv", list(all_manifest.values()), manifest_fields, ",")
    _write_table(output_root / "url_matches.tsv", reliable_rows, match_fields, "\t")
    _write_table(output_root / "candidate_matches.tsv", candidate_rows, match_fields, "\t")
    _write_table(output_root / "unmatched_targets.tsv", unmatched_rows, list(targets[0]), "\t")
    _write_table(output_root / "target_results.tsv", result_rows, list(result_rows[0]), "\t")

    formal_after = {path: sha256_file(Path(path)) for path in formal_before}
    if formal_before != formal_after:
        raise RuntimeError("Existing formal GKG outputs changed")
    archive_paths = sorted(raw_dir.glob("*.zip"))
    reliable_count = len(reliable_by_id)
    decision = "CONTINUE_GKG_FORMAL_ENRICHMENT" if reliable_count >= 4 else ("STOP_GKG_USE_SPECIALIZED_DEMAND_SOURCE" if reliable_count <= 1 else "AUXILIARY_GKG_ADD_SECOND_DEMAND_SOURCE")
    summary = {
        "target_events": 6,
        "unique_target_dates": len(target_days),
        "gkg_document_url_index": GKG_DOCUMENT_URL_INDEX,
        "gkg_source_common_name_index": GKG_SOURCE_COMMON_NAME_INDEX,
        "reliable_matches": reliable_count,
        "candidate_only_matches": candidate_only,
        "unmatched": 6 - reliable_count - candidate_only,
        "slots_attempted": len(all_manifest),
        "zip_files_retained": len(archive_paths),
        "zip_files_acquired_for_revalidation": len(archive_paths),
        "downloaded_this_run": sum(row["download_status"] == "DOWNLOADED" for row in all_manifest.values()),
        "existing_files_reused": sum(row["download_status"] == "EXISTS" for row in all_manifest.values()),
        "missing_remote_files": sum(row["download_status"] == "MISSING" for row in all_manifest.values()),
        "download_errors": sum(row["download_status"] == "ERROR" for row in all_manifest.values()),
        "raw_zip_bytes": sum(path.stat().st_size for path in archive_paths),
        "formal_outputs_sha_unchanged": True,
        "formal_sha256": formal_before,
        "decision": decision,
        "target_results": result_rows,
        "output_root": str(output_root.resolve()),
        "raw_gkg_root": str(raw_dir.resolve()),
    }
    (output_root / "revalidation_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary
