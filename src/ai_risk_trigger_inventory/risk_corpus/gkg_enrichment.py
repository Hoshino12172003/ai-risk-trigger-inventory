"""Stage 5B-2G bounded GKG acquisition and Flash-demand enrichment."""

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
import csv
from datetime import date, datetime, timedelta, timezone
import gzip
from hashlib import sha256
import io
import json
from pathlib import Path
import re
from typing import Any, Iterable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import zipfile

from .candidate_screening import file_sha256, read_tsv
from .flash_evidence import classify_flash_evidence


GDELT_BASE = "https://data.gdeltproject.org/gdeltv2"
SLOT_UTC = "120000"
MAX_COMPRESSED_BYTES = 128 * 1024 * 1024
TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}
GKG_FIELDS = [
    "GKGRECORDID", "V2DATE", "V2SOURCECOLLECTIONIDENTIFIER", "V2SOURCECOMMONNAME",
    "V2DOCUMENTIDENTIFIER", "V1COUNTS", "V2COUNTS", "V1THEMES", "V2THEMES",
    "V1LOCATIONS", "V2LOCATIONS", "V1PERSONS", "V2PERSONS", "V1ORGANIZATIONS",
    "V2ORGANIZATIONS", "V1TONE", "V2ENHANCEDDATES", "V2GCAM", "V2SHARINGIMAGE",
    "V2RELATEDIMAGES", "V2SOCIALIMAGEEMBEDS", "V2SOCIALVIDEOEMBEDS", "V2QUOTATIONS",
    "V2ALLNAMES", "V2AMOUNTS", "V2TRANSLATIONINFO", "V2EXTRASXML",
]
TARGET_CODES = {"EC", "ECU", "CO", "COL", "PE", "PER"}
TARGET_NAMES = {"ecuador", "colombia", "peru", "perú"}


def canonicalize_url(value: str) -> str:
    value = value.strip()
    if not value: return ""
    parsed = urlsplit(value if "://" in value else "https://" + value)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."): host = host[4:]
    port = f":{parsed.port}" if parsed.port and parsed.port not in {80, 443} else ""
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
    query = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
             if key.lower() not in TRACKING_KEYS and not key.lower().startswith("utm_")]
    return urlunsplit(("https", host + port, path, urlencode(sorted(query)), ""))


def request_dates(target_dates: Iterable[str]) -> list[str]:
    requested = set()
    for value in target_dates:
        center = date.fromisoformat(value)
        requested.update((center + timedelta(days=offset)).isoformat() for offset in (-1, 0, 1))
    return sorted(requested)


def request_filename(day: str) -> str:
    return day.replace("-", "") + SLOT_UTC + ".gkg.csv.zip"


def match_type(event_url: str, gkg_url: str, *, event_date: str = "", gkg_date: str = "",
               event_geo: str = "", gkg_locations: str = "", themes: str = "") -> str:
    if event_url.strip() and event_url.strip() == gkg_url.strip(): return "EXACT_URL"
    if canonicalize_url(event_url) and canonicalize_url(event_url) == canonicalize_url(gkg_url): return "NORMALIZED_URL"
    event_host = urlsplit(canonicalize_url(event_url)).hostname or ""
    gkg_host = urlsplit(canonicalize_url(gkg_url)).hostname or ""
    locations = gkg_locations.lower()
    geo_tokens = {token.lower() for token in re.split(r"[,;#\s]+", event_geo) if len(token) >= 2}
    geo_overlap = bool(geo_tokens & set(re.split(r"[,;#\s]+", locations))) or any(name in locations for name in TARGET_NAMES)
    same_day = bool(event_date and gkg_date.startswith(event_date.replace("-", "")))
    context = bool(re.search(r"demand|sales|shortage|buying|compras|demanda|ventas", themes, re.I))
    if event_host and event_host == gkg_host and same_day and (geo_overlap or context):
        return "DATE_DOMAIN_GEO_CANDIDATE"
    return "NO_MATCH"


def classify_post_gkg(event: dict[str, str], matched: list[dict[str, str]]) -> tuple[str, str, list[str], list[str]]:
    if not matched:
        return "PLAUSIBLE_BUT_INSUFFICIENT", "NO_GKG_MATCH", [], []
    evidence = " ".join(" ".join(row.get(name, "") for name in (
        "V1THEMES", "V2THEMES", "V1LOCATIONS", "V2LOCATIONS", "V1PERSONS", "V2PERSONS",
        "V1ORGANIZATIONS", "V2ORGANIZATIONS", "V2ALLNAMES", "V2AMOUNTS", "V2QUOTATIONS",
        "V2DOCUMENTIDENTIFIER",
    )) for row in matched)
    lower = evidence.lower()
    url_lower = event.get("SOURCEURL", "").lower()
    if any(term in lower or term in url_lower for term in (
        "weed-wednesday", "marijuana", "thanksgiving", "shortage-of-large-sizes",
        "seasonal-demand", "seasonal demand",
    )):
        return "FALSE_POSITIVE", "GKG_CONFIRMS_NON_FLASH_OR_SEASONAL_CONTEXT", [], []
    result = classify_flash_evidence(evidence, event.get("flash_geo_status", ""), source="GDELT")
    exactish = any(row["match_type"] in {"EXACT_URL", "NORMALIZED_URL"} for row in matched)
    target_location = any(_target_location(row.get("V1LOCATIONS", "") + " " + row.get("V2LOCATIONS", "")) for row in matched)
    if result["flash_evidence_status"] == "TRUE_FLASH_EVIDENCE" and exactish and target_location:
        return "TRUE_FLASH_EVIDENCE", "EXACT_GKG_DEMAND_AND_GOODS_EVIDENCE", result["flash_evidence_phrases"], result["flash_goods_context"]
    return "PLAUSIBLE_BUT_INSUFFICIENT", "GKG_MATCH_WITHOUT_SUFFICIENT_DIRECT_TARGET_EVIDENCE", result["flash_evidence_phrases"], result["flash_goods_context"]


def _target_location(text: str) -> bool:
    lower = text.lower()
    return any(re.search(rf"(?:^|[#;,]){code}(?:[#;,]|$)", text, re.I) for code in TARGET_CODES) or any(name in lower for name in TARGET_NAMES)


def _download(url: str, destination: Path) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = {"source_url": url, "downloaded_at": now, "download_status": "DOWNLOAD_FAILED", "file_size": 0, "sha256": "", "error": ""}
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    try:
        request = Request(url, headers={"User-Agent": "ai-risk-trigger-inventory-stage5b2g/1.0"})
        with urlopen(request, timeout=90) as response, part.open("wb") as output:
            advertised = response.headers.get("Content-Length")
            if advertised and int(advertised) > MAX_COMPRESSED_BYTES: raise RuntimeError("REMOTE_FILE_EXCEEDS_SAFETY_CAP")
            digest = sha256(); size = 0
            for block in iter(lambda: response.read(1024 * 1024), b""):
                size += len(block)
                if size > MAX_COMPRESSED_BYTES: raise RuntimeError("REMOTE_FILE_EXCEEDS_SAFETY_CAP")
                output.write(block); digest.update(block)
        part.replace(destination)
        row.update(download_status="DOWNLOADED", file_size=size, sha256=digest.hexdigest(), error="")
    except HTTPError as error:
        row.update(download_status="MISSING_REMOTE_FILE" if error.code == 404 else "DOWNLOAD_FAILED", error=f"HTTP {error.code}")
        part.unlink(missing_ok=True)
    except (URLError, TimeoutError, RuntimeError, OSError) as error:
        row["error"] = f"{type(error).__name__}: {error}"; part.unlink(missing_ok=True)
    return row


def _read_archive(path: Path) -> Iterator[dict[str, str]]:
    with zipfile.ZipFile(path) as archive:
        member = archive.namelist()[0]
        with archive.open(member) as stream:
            for line_number, line in enumerate(stream, start=1):
                values = line.decode("utf-8", errors="replace").rstrip("\r\n").split("\t")
                if len(values) < 16: continue
                values += [""] * (len(GKG_FIELDS) - len(values))
                row = dict(zip(GKG_FIELDS, values[:len(GKG_FIELDS)])); row["raw_field_count"] = str(len(values)); row["raw_line_number"] = str(line_number)
                yield row


@contextmanager
def _gzip_writer(path: Path, fields: list[str]) -> Iterator[csv.DictWriter]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                writer = csv.DictWriter(text, fieldnames=fields, delimiter="\t", lineterminator="\n", extrasaction="ignore")
                writer.writeheader(); yield writer


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_targeted_gkg_enrichment(root: Path) -> dict[str, Any]:
    targets_dir, candidates, processed, audit = root / "gkg_targets", root / "candidates", root / "processed", root / "audit"
    raw_dir, enrichment = root.parent / "raw_risk_corpus/gdelt/gkg_flash_targeted", root / "gkg_enrichment"
    input_paths = [targets_dir / "flash_gkg_target_events.tsv.gz", targets_dir / "flash_gkg_target_dates.csv",
        candidates / "flash_gdelt_evidence_review.tsv.gz", candidates / "flash_candidate_evidence_index.tsv.gz",
        processed / "gdelt_event_standardized.tsv.gz"]
    stage2f = json.loads((audit / "stage5b2f_flash_summary.json").read_text(encoding="utf-8"))
    input_paths += [Path(path) for path in stage2f["protected_input_sha256"]]
    input_paths += [Path(path) for path in stage2f["outputs"].values()]
    input_paths = sorted(set(input_paths), key=lambda path: str(path))
    before = {str(path.resolve()): file_sha256(path) for path in input_paths}

    target_events = list(read_tsv(targets_dir / "flash_gkg_target_events.tsv.gz"))
    with (targets_dir / "flash_gkg_target_dates.csv").open(encoding="utf-8", newline="") as stream:
        target_dates = [row["date"] for row in csv.DictReader(stream)]
    if len(target_events) != 6 or len(set(target_dates)) != 5:
        raise RuntimeError("Stage 5B-2F target scope does not match its audit")
    requested_days = request_dates(target_dates)

    manifest_path = audit / "stage5b2g_gkg_download_manifest.csv"
    prior_manifest = {}
    if manifest_path.exists():
        with manifest_path.open(encoding="utf-8", newline="") as stream:
            prior_manifest = {row["remote_filename"]: row for row in csv.DictReader(stream)}
    manifest: list[dict[str, Any]] = []
    for day in requested_days:
        filename = request_filename(day); destination = raw_dir / filename; url = f"{GDELT_BASE}/{filename}"
        if destination.exists():
            prior = prior_manifest.get(filename, {})
            row = {"remote_filename": filename, "date": day, "file_size": destination.stat().st_size,
                "sha256": file_sha256(destination), "download_status": "DOWNLOADED", "source_url": url,
                "downloaded_at": prior.get("downloaded_at") or datetime.fromtimestamp(destination.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"), "error": ""}
        elif filename in prior_manifest and (
            prior_manifest[filename].get("download_status") == "MISSING_REMOTE_FILE"
            or (prior_manifest[filename].get("download_status") == "DOWNLOAD_FAILED"
                and "FileNotFoundError" not in prior_manifest[filename].get("error", ""))
        ):
            row = dict(prior_manifest[filename])
        else:
            result = _download(url, destination)
            row = {"remote_filename": filename, "date": day, **result}
        manifest.append(row)
    manifest_fields = ["remote_filename", "date", "file_size", "sha256", "download_status", "source_url", "downloaded_at", "error"]
    _write_csv(manifest_path, manifest, manifest_fields)

    reviews = {row["GlobalEventID"]: row for row in read_tsv(candidates / "flash_gdelt_evidence_review.tsv.gz") if row.get("needs_gkg_enrichment") == "true"}
    if set(reviews) != {row["GlobalEventID"] for row in target_events}: raise RuntimeError("Only the six audited targets may be enriched")
    event_by_id = {row["GlobalEventID"]: row for row in target_events}
    matches: dict[str, list[dict[str, str]]] = defaultdict(list)
    matched_output: list[dict[str, str]] = []
    for item in manifest:
        if item["download_status"] != "DOWNLOADED": continue
        path = raw_dir / item["remote_filename"]
        for gkg in _read_archive(path):
            gkg_url = gkg.get("V2DOCUMENTIDENTIFIER", "")
            gkg_date = gkg.get("V2DATE", "")
            for event_id, event in event_by_id.items():
                if item["date"] not in request_dates([event["event_date"]]): continue
                typ = match_type(event["SOURCEURL"], gkg_url, event_date=event["event_date"], gkg_date=gkg_date,
                    event_geo=event["ActionGeo_FullName"], gkg_locations=gkg.get("V1LOCATIONS", "") + " " + gkg.get("V2LOCATIONS", ""),
                    themes=gkg.get("V1THEMES", "") + " " + gkg.get("V2THEMES", "") + " " + gkg.get("V2ALLNAMES", ""))
                if typ == "NO_MATCH": continue
                result = {"GlobalEventID": event_id, "target_event_date": event["event_date"], "raw_file": item["remote_filename"], "match_type": typ, **gkg}
                matches[event_id].append(result); matched_output.append(result)

    event_rows: list[dict[str, Any]] = []
    for event_id in sorted(event_by_id):
        event = {**reviews[event_id], **event_by_id[event_id]}; rows = matches[event_id]
        status, reason, demand, goods = classify_post_gkg(event, rows)
        types = Counter(row["match_type"] for row in rows)
        event_rows.append({"GlobalEventID": event_id, "event_date": event["event_date"], "SOURCEURL": event["SOURCEURL"],
            "pre_gkg_flash_status": event["current_flash_evidence_status"], "gkg_match_found": str(bool(rows)).lower(),
            "gkg_match_type": next((kind for kind in ("EXACT_URL", "NORMALIZED_URL", "DATE_DOMAIN_GEO_CANDIDATE") if types[kind]), "NO_MATCH"),
            "gkg_record_count": len(rows), "gkg_theme_evidence": json.dumps(sorted({row.get('V2THEMES','') for row in rows if row.get('V2THEMES')}), ensure_ascii=False),
            "gkg_location_evidence": json.dumps(sorted({row.get('V2LOCATIONS','') for row in rows if row.get('V2LOCATIONS')}), ensure_ascii=False),
            "gkg_demand_evidence": json.dumps(demand, ensure_ascii=False), "gkg_goods_context": json.dumps(goods, ensure_ascii=False),
            "post_gkg_flash_status": status, "post_gkg_reason": reason})

    matched_fields = ["GlobalEventID", "target_event_date", "raw_file", "match_type", *GKG_FIELDS, "raw_field_count", "raw_line_number"]
    event_fields = ["GlobalEventID", "event_date", "SOURCEURL", "pre_gkg_flash_status", "gkg_match_found", "gkg_match_type", "gkg_record_count", "gkg_theme_evidence", "gkg_location_evidence", "gkg_demand_evidence", "gkg_goods_context", "post_gkg_flash_status", "post_gkg_reason"]
    paths = {"matched": enrichment / "flash_gkg_matched_records.tsv.gz", "events": enrichment / "flash_gkg_event_enrichment.tsv.gz",
        "unmatched": enrichment / "flash_gkg_unmatched_targets.tsv.gz", "review": candidates / "flash_gdelt_evidence_review_post_gkg.tsv.gz"}
    with _gzip_writer(paths["matched"], matched_fields) as writer: writer.writerows(sorted(matched_output, key=lambda row: (row["GlobalEventID"], row["GKGRECORDID"], row["raw_file"])))
    with _gzip_writer(paths["events"], event_fields) as writer: writer.writerows(event_rows)
    with _gzip_writer(paths["unmatched"], event_fields) as writer: writer.writerows(row for row in event_rows if row["gkg_match_found"] == "false")
    original_review_fields = list(next(iter(read_tsv(candidates / "flash_gdelt_evidence_review.tsv.gz"))).keys())
    update_fields = [name for name in event_fields if name not in {"GlobalEventID", "event_date", "SOURCEURL", "pre_gkg_flash_status"}]
    enrichment_by_id = {row["GlobalEventID"]: row for row in event_rows}
    with _gzip_writer(paths["review"], original_review_fields + update_fields) as writer:
        for row in read_tsv(candidates / "flash_gdelt_evidence_review.tsv.gz"):
            writer.writerow({**row, **enrichment_by_id.get(row["GlobalEventID"], {})})

    post = Counter(row["post_gkg_flash_status"] for row in event_rows); match_counts = Counter(row["gkg_match_type"] for row in event_rows)
    overall = {"TRUE_FLASH_EVIDENCE": 3 + post["TRUE_FLASH_EVIDENCE"],
        "PLAUSIBLE_BUT_INSUFFICIENT": 59 + post["PLAUSIBLE_BUT_INSUFFICIENT"],
        "FALSE_POSITIVE": 45 + 41 + post["FALSE_POSITIVE"]}
    if sum(overall.values()) != 154: raise RuntimeError("Post-GKG Flash population does not reconcile to 154")
    after = {path: file_sha256(Path(path)) for path in before}
    if before != after: raise RuntimeError("A prior Stage 5B or standardized input changed")
    summary = {"target_events": len(event_rows), "target_dates": len(set(target_dates)), "requested_dates": requested_days,
        "requested_files": len(manifest), "downloaded_files": sum(row["download_status"] == "DOWNLOADED" for row in manifest),
        "missing_files": sum(row["download_status"] == "MISSING_REMOTE_FILE" for row in manifest),
        "failed_files": sum(row["download_status"] == "DOWNLOAD_FAILED" for row in manifest),
        "exact_url_matches": match_counts["EXACT_URL"], "normalized_url_matches": match_counts["NORMALIZED_URL"],
        "candidate_matches": match_counts["DATE_DOMAIN_GEO_CANDIDATE"], "unmatched_targets": match_counts["NO_MATCH"],
        "post_target_status_counts": dict(post), "newly_confirmed_true": post["TRUE_FLASH_EVIDENCE"],
        "remaining_plausible": post["PLAUSIBLE_BUT_INSUFFICIENT"], "newly_rejected_false": post["FALSE_POSITIVE"],
        "overall_flash_status_counts": overall, "raw_manifest_sha_complete": all(row["sha256"] for row in manifest if row["download_status"] == "DOWNLOADED"),
        "prior_input_sha_unchanged": True, "input_sha256": before,
        "outputs": {name: str(path.resolve()) for name, path in paths.items()}, "raw_root": str(raw_dir.resolve()),
        "download_manifest": str(manifest_path.resolve())}
    summary_rows = [{"metric": key, "value": value} for key, value in (
        ("TARGET_EVENTS", summary["target_events"]), ("TARGET_DATES", summary["target_dates"]), ("REQUESTED_FILES", summary["requested_files"]),
        ("DOWNLOADED_FILES", summary["downloaded_files"]), ("MISSING_FILES", summary["missing_files"]), ("FAILED_FILES", summary["failed_files"]),
        ("EXACT_URL_MATCHES", summary["exact_url_matches"]), ("NORMALIZED_URL_MATCHES", summary["normalized_url_matches"]),
        ("CANDIDATE_MATCHES", summary["candidate_matches"]), ("UNMATCHED_TARGETS", summary["unmatched_targets"]),
        ("NEWLY_CONFIRMED_TRUE", summary["newly_confirmed_true"]), ("REMAINING_PLAUSIBLE", summary["remaining_plausible"]),
        ("NEWLY_REJECTED_FALSE", summary["newly_rejected_false"]))]
    _write_csv(audit / "stage5b2g_flash_gkg_summary.csv", summary_rows, ["metric", "value"])
    _write_json(audit / "stage5b2g_flash_gkg_summary.json", summary)
    _write_report(audit / "stage5b2g_flash_gkg_report.md", summary, manifest, event_rows)
    return summary


def _write_report(path: Path, summary: dict[str, Any], manifest: list[dict[str, Any]], events: list[dict[str, Any]]) -> None:
    lines = ["# Stage 5B-2G targeted GKG enrichment", "",
        "The acquisition is limited to the five Stage 5B-2F target dates and D-1/D/D+1. GDELT 2.0 has 15-minute GKG update archives rather than a daily bundle, so this bounded run uses the existing Stage 5A convention: the 12:00 UTC update slot on each requested day. It does not crawl every update in a day.", "",
        "## Acquisition", "", f"- Target events: {summary['target_events']}", f"- Target dates: {summary['target_dates']}",
        f"- Requested dates/files: {summary['requested_files']}", f"- Downloaded: {summary['downloaded_files']}",
        f"- Missing remote: {summary['missing_files']}", f"- Download failures: {summary['failed_files']}", "",
        "| Date | File | Status | Bytes | SHA-256 |", "|---|---|---|---:|---|"]
    lines += [f"| {row['date']} | {row['remote_filename']} | {row['download_status']} | {row['file_size']} | {row['sha256']} |" for row in manifest]
    lines += ["", "## Matching", "", f"- Exact URL target events: {summary['exact_url_matches']}",
        f"- Normalized URL target events: {summary['normalized_url_matches']}", f"- Candidate-matched target events: {summary['candidate_matches']}",
        f"- Unmatched target events: {summary['unmatched_targets']}", "", "## Post-GKG decisions", "",
        "| GlobalEventID | Match | Records | Pre | Post | Reason |", "|---|---|---:|---|---|---|"]
    lines += [f"| {row['GlobalEventID']} | {row['gkg_match_type']} | {row['gkg_record_count']} | {row['pre_gkg_flash_status']} | {row['post_gkg_flash_status']} | {row['post_gkg_reason']} |" for row in events]
    lines += ["", f"New TRUE: {summary['newly_confirmed_true']}; remaining plausible: {summary['remaining_plausible']}; newly FALSE: {summary['newly_rejected_false']}.", "",
        "Updated 154-record totals: " + ", ".join(f"{key}={value}" for key, value in summary["overall_flash_status_counts"].items()) + ".", "",
        "Candidate URL/domain/date matches are evidence candidates, not exact matches. Shortage or hoarding alone cannot produce TRUE. No GenAI, broad GKG crawl, clustering, cross-source fusion, canonical event creation, severity/impact mapping, scenario generation, or optimization was performed.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
