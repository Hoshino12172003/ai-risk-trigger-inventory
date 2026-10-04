"""Read-only enhanced matching diagnostic for six unmatched full-day GKG targets."""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import date
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
from typing import Any, Iterator
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
import zipfile

from .candidate_screening import file_sha256


GKG_DOCUMENT_URL_INDEX = 4
TRACKING = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}
REGION_SEGMENTS = {"africa", "asia", "europe", "north-america", "south-america", "latin-america", "oceania", "global", "en", "es"}
TITLE_STOP = {"a", "an", "and", "at", "based", "for", "from", "in", "of", "on", "the", "to", "with", "y", "de", "del", "la", "las", "los", "en", "para"}
METHOD_PRIORITY = {"EXACT_URL": 0, "NORMALIZED_URL": 1, "CANONICAL_PATH": 2, "TITLE_DOMAIN_CANDIDATE": 3, "SAME_DOMAIN_ONLY": 4}


def normalize_url(value: str) -> str:
    value = value.strip()
    if not value: return ""
    parsed = urlsplit(value if "://" in value else "https://" + value)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."): host = host[4:]
    port = f":{parsed.port}" if parsed.port and parsed.port not in {80, 443} else ""
    path = re.sub(r"/+", "/", unquote(parsed.path or "/")).rstrip("/") or "/"
    query = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
             if key.lower() not in TRACKING and not key.lower().startswith("utm_")]
    return urlunsplit(("https", host + port, path, urlencode(sorted(query)), ""))


def domain(value: str) -> str:
    return urlsplit(normalize_url(value)).hostname or ""


def canonical_path(value: str) -> str:
    path = urlsplit(normalize_url(value)).path.lower()
    segments = [segment for segment in path.split("/") if segment]
    if segments and segments[0] in REGION_SEGMENTS: segments = segments[1:]
    segments = [segment for segment in segments if segment not in {"amp", "index.html", "index.htm"}]
    if segments:
        segments[-1] = re.sub(r"\.(?:html?|aspx?)$", "", segments[-1])
    return "/" + "/".join(segments)


def path_key(value: str) -> str:
    return f"{domain(value)}{canonical_path(value)}"


def title_from_url(value: str) -> str:
    segments = [segment for segment in canonical_path(value).split("/") if segment]
    candidate = segments[-1] if segments else ""
    candidate = re.sub(r"^\d+[-_]", "", candidate)
    words = re.findall(r"[a-záéíóúñü]+", candidate.replace("-", " ").replace("_", " "), re.I)
    return " ".join(word.lower() for word in words if word.lower() not in TITLE_STOP)


def title_similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = set(left.lower().split()), set(right.lower().split())
    if len(left_tokens) < 3 or len(right_tokens) < 3: return 0.0
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    sequence = SequenceMatcher(None, left.lower(), right.lower()).ratio()
    return max(jaccard, sequence)


def classify_candidate(target_url: str, candidate_url: str) -> tuple[str, str, float]:
    if target_url.strip() and target_url.strip() == candidate_url.strip(): return "EXACT_URL", "HIGH", 1.0
    if normalize_url(target_url) and normalize_url(target_url) == normalize_url(candidate_url): return "NORMALIZED_URL", "HIGH", 1.0
    if domain(target_url) and path_key(target_url) == path_key(candidate_url): return "CANONICAL_PATH", "HIGH", 1.0
    if domain(target_url) and domain(target_url) == domain(candidate_url):
        score = title_similarity(title_from_url(target_url), title_from_url(candidate_url))
        if score >= 0.78: return "TITLE_DOMAIN_CANDIDATE", "REVIEW_ONLY", score
        return "SAME_DOMAIN_ONLY", "NONE", score
    return "NO_MATCH", "NONE", 0.0


def date_offsets() -> tuple[int, ...]:
    return (0, -1, 1, -2, 2)


def _archive_date(path: Path) -> str:
    match = re.match(r"(\d{8})", path.name)
    return f"{match.group(1)[:4]}-{match.group(1)[4:6]}-{match.group(1)[6:8]}" if match else ""


def _date_offset(target: str, candidate: str) -> int:
    return (date.fromisoformat(candidate) - date.fromisoformat(target)).days


def _iter_gkg_urls(path: Path) -> Iterator[tuple[int, str]]:
    with zipfile.ZipFile(path) as archive:
        for member in archive.namelist():
            if member.endswith("/"): continue
            with archive.open(member) as stream:
                for row_number, line in enumerate(stream, start=1):
                    fields = line.decode("utf-8", errors="replace").rstrip("\r\n").split("\t")
                    if len(fields) > GKG_DOCUMENT_URL_INDEX and fields[GKG_DOCUMENT_URL_INDEX].strip():
                        yield row_number, fields[GKG_DOCUMENT_URL_INDEX].strip()


def _write_tsv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def run_unmatched_diagnostic(root: Path) -> dict[str, Any]:
    full_day = root / "gkg_enrichment/flash_full_day"
    output = full_day / "enhanced_match_diagnostic"
    raw_full_day = root.parent / "raw_risk_corpus/gdelt/gkg_flash_full_day"
    fallback_raw = root.parent / "raw_risk_corpus/gdelt/gkg_flash_targeted"
    formal = [full_day / name for name in ("full_day_download_manifest.csv", "full_day_url_matches.tsv", "unmatched_targets.tsv", "full_day_summary.json", "checkpoint.json")]
    formal_before = {str(path.resolve()): file_sha256(path) for path in formal}
    targets = []
    with (full_day / "unmatched_targets.tsv").open(encoding="utf-8", newline="") as stream:
        targets = list(csv.DictReader(stream, delimiter="\t"))
    if len(targets) != 6: raise RuntimeError("Expected exactly six full-day unmatched targets")

    retained_full_day = sorted(raw_full_day.glob("*.zip")) if raw_full_day.exists() else []
    local_archives = retained_full_day or sorted(fallback_raw.glob("*.zip"))
    archive_sha = {str(path.resolve()): file_sha256(path) for path in local_archives}
    coverage_mode = "FULL_DAY_RETAINED" if retained_full_day else "FALLBACK_TARGETED_SLOTS_ONLY"
    candidate_rows: list[dict[str, Any]] = []
    counts: dict[str, Counter[str]] = {row["GlobalEventID"]: Counter() for row in targets}
    same_domain_best: dict[str, tuple[tuple[int, int, float, str], dict[str, Any]]] = {}

    for archive in local_archives:
        archive_day = _archive_date(archive)
        active = [target for target in targets if archive_day and abs(_date_offset(target["event_date"], archive_day)) <= 2]
        if not active: continue
        for row_number, candidate_url in _iter_gkg_urls(archive):
            for target in active:
                event_id = target["GlobalEventID"]; offset = _date_offset(target["event_date"], archive_day)
                method, confidence, score = classify_candidate(target["SOURCEURL"], candidate_url)
                if method == "NO_MATCH": continue
                counts[event_id]["same_domain"] += 1
                if method == "EXACT_URL": counts[event_id]["exact"] += 1
                if method == "NORMALIZED_URL": counts[event_id]["normalized"] += 1
                if method == "CANONICAL_PATH": counts[event_id]["path"] += 1
                if method == "TITLE_DOMAIN_CANDIDATE": counts[event_id]["title"] += 1
                row = {"target_id": event_id, "target_date": target["event_date"], "original_url": target["SOURCEURL"],
                    "normalized_url": normalize_url(target["SOURCEURL"]), "domain": domain(target["SOURCEURL"]),
                    "title": title_from_url(target["SOURCEURL"]), "candidate_date": archive_day, "nearest_date_offset": offset,
                    "candidate_gkg_url": candidate_url, "candidate_title": title_from_url(candidate_url),
                    "match_method": method, "confidence": confidence, "title_similarity": f"{score:.6f}",
                    "source_archive": archive.name, "gkg_row_number": row_number, "requires_human_review": str(method == "TITLE_DOMAIN_CANDIDATE").lower()}
                rank = (METHOD_PRIORITY[method], abs(offset), -score, candidate_url)
                if event_id not in same_domain_best or rank < same_domain_best[event_id][0]: same_domain_best[event_id] = (rank, row)
                if method != "SAME_DOMAIN_ONLY": candidate_rows.append(row)

    candidate_rows.sort(key=lambda row: (row["target_id"], METHOD_PRIORITY[row["match_method"]], abs(int(row["nearest_date_offset"])), row["candidate_gkg_url"]))
    diagnostic_rows = []
    reliable = candidate_only = unmatched = 0
    for target in sorted(targets, key=lambda row: row["GlobalEventID"]):
        event_id = target["GlobalEventID"]; counter = counts[event_id]; best = same_domain_best.get(event_id, (None, {}))[1]
        method = best.get("match_method", "NO_MATCH"); confidence = best.get("confidence", "NONE")
        if method in {"EXACT_URL", "NORMALIZED_URL", "CANONICAL_PATH"}: reliable += 1
        elif method == "TITLE_DOMAIN_CANDIDATE": candidate_only += 1
        else: unmatched += 1
        if not retained_full_day:
            limitation = "FULL_DAY_RAW_ARCHIVES_NOT_RETAINED; ORIGINAL_MATCHER_USED_GKG_COLUMN_4_INSTEAD_OF_DOCUMENT_URL_COLUMN_5"
        else: limitation = ""
        if method == "SAME_DOMAIN_ONLY": failure = "SAME_DOMAIN_ONLY_NO_PATH_OR_TITLE_IDENTITY; " + limitation
        elif method == "NO_MATCH": failure = "NO_CANDIDATE_IN_AVAILABLE_LOCAL_GKG; " + limitation
        elif method == "TITLE_DOMAIN_CANDIDATE": failure = "TITLE_MATCH_REQUIRES_HUMAN_REVIEW; " + limitation
        else: failure = limitation
        diagnostic_rows.append({"target_id": event_id, "target_date": target["event_date"], "original_url": target["SOURCEURL"],
            "normalized_url": normalize_url(target["SOURCEURL"]), "domain": domain(target["SOURCEURL"]), "title": title_from_url(target["SOURCEURL"]),
            "exact_match_count": counter["exact"], "normalized_match_count": counter["normalized"],
            "same_domain_candidate_count": counter["same_domain"], "path_candidate_count": counter["path"],
            "title_candidate_count": counter["title"], "nearest_date_offset": best.get("nearest_date_offset", ""),
            "candidate_gkg_url": best.get("candidate_gkg_url", ""), "candidate_title": best.get("candidate_title", ""),
            "match_method": method, "confidence": confidence, "failure_reason": failure})

    diagnostic_fields = ["target_id", "target_date", "original_url", "normalized_url", "domain", "title", "exact_match_count", "normalized_match_count", "same_domain_candidate_count", "path_candidate_count", "title_candidate_count", "nearest_date_offset", "candidate_gkg_url", "candidate_title", "match_method", "confidence", "failure_reason"]
    candidate_fields = ["target_id", "target_date", "original_url", "normalized_url", "domain", "title", "candidate_date", "nearest_date_offset", "candidate_gkg_url", "candidate_title", "match_method", "confidence", "title_similarity", "source_archive", "gkg_row_number", "requires_human_review"]
    diagnostic_path = output / "enhanced_match_diagnostic.tsv"; candidate_path = output / "candidate_matches.tsv"; summary_path = output / "enhanced_match_summary.json"
    _write_tsv(diagnostic_path, diagnostic_rows, diagnostic_fields); _write_tsv(candidate_path, candidate_rows, candidate_fields)
    formal_after = {path: file_sha256(Path(path)) for path in formal_before}
    archive_after = {path: file_sha256(Path(path)) for path in archive_sha}
    if formal_before != formal_after: raise RuntimeError("A formal full-day result was modified")
    if archive_sha != archive_after: raise RuntimeError("A local GKG archive was modified")
    full_day_summary = json.loads((full_day / "full_day_summary.json").read_text(encoding="utf-8"))
    summary = {"target_events": len(targets), "coverage_mode": coverage_mode,
        "full_day_slots_attempted": full_day_summary.get("download_slots_attempted"),
        "full_day_archives_retained": len(retained_full_day), "local_fallback_archives_scanned": len(local_archives),
        "matcher_audit": {"implemented_document_url_index": 3, "correct_document_url_index": 4,
            "status": "COLUMN_INDEX_ERROR_CONFIRMED", "effect": "Original exact and normalized URL comparisons used source common name instead of document URL"},
        "reliable_matches": reliable, "candidate_only_matches": candidate_only, "fully_unmatched": unmatched,
        "method_counts": dict(Counter(row["match_method"] for row in diagnostic_rows)),
        "candidate_rows": len(candidate_rows), "date_offsets_scanned": list(date_offsets()),
        "formal_outputs_sha_unchanged": True, "local_archives_sha_unchanged": True,
        "formal_sha256": formal_before, "archive_sha256": archive_sha,
        "decision": "STOP_URL_RULE_EXPANSION_USE_DUAL_SOURCE_DEMAND_EVIDENCE" if reliable <= 1 else "CONTINUE_GKG_MATCHER",
        "outputs": {"diagnostic": str(diagnostic_path.resolve()), "candidates": str(candidate_path.resolve()), "summary": str(summary_path.resolve())}}
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary
