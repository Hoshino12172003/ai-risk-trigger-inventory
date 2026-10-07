"""Freeze the validated matcher and formalize six-target demand-risk GKG evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
import gzip
import io
import json
from pathlib import Path
from typing import Any, Iterator

from .candidate_screening import file_sha256, read_tsv
from .gkg_enrichment import GKG_FIELDS, classify_post_gkg
from .gkg_revalidation import GKG_DOCUMENT_URL_INDEX, GKG_SOURCE_COMMON_NAME_INDEX


MATCHER_SPEC = {
    "gkg_version": "2.1",
    "source_common_name_index": GKG_SOURCE_COMMON_NAME_INDEX,
    "document_url_index": GKG_DOCUMENT_URL_INDEX,
    "reliable_priority": ["EXACT_URL", "NORMALIZED_URL", "CANONICAL_PATH"],
    "title_similarity_policy": "REVIEW_ONLY",
}


def _read_selected_row(archive: Path, row_number: int) -> dict[str, str]:
    with __import__("zipfile").ZipFile(archive) as zipped:
        member = next(name for name in zipped.namelist() if not name.endswith("/"))
        with zipped.open(member) as stream:
            for current, raw_line in enumerate(stream, start=1):
                if current != row_number:
                    continue
                values = raw_line.decode("utf-8", errors="replace").rstrip("\r\n").split("\t")
                if len(values) <= GKG_DOCUMENT_URL_INDEX:
                    raise RuntimeError(f"Short GKG row {row_number} in {archive.name}")
                values += [""] * (len(GKG_FIELDS) - len(values))
                row = dict(zip(GKG_FIELDS, values[: len(GKG_FIELDS)]))
                row["raw_field_count"] = str(len(values))
                row["raw_line_number"] = str(row_number)
                return row
    raise RuntimeError(f"GKG row {row_number} not found in {archive.name}")


def _gzip_writer(path: Path, fields: list[str]) -> Iterator[csv.DictWriter]:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.open("wb")
    compressed = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0)
    text = io.TextIOWrapper(compressed, encoding="utf-8", newline="")
    writer = csv.DictWriter(text, fieldnames=fields, delimiter="\t", lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    try:
        yield writer
    finally:
        text.close()


def _write_gzip(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    context = _gzip_writer(path, fields)
    writer = next(context)
    writer.writerows(rows)
    try:
        next(context)
    except StopIteration:
        pass


def run_formal_demand_risk_gkg(root: Path) -> dict[str, Any]:
    revalidation = root / "gkg_enrichment/gkg_revalidation_6targets"
    output = root / "gkg_enrichment/demand_risk_formal"
    audit = root / "audit"
    target_path = root / "gkg_targets/flash_gkg_target_events.tsv.gz"
    review_path = root / "candidates/flash_gdelt_evidence_review.tsv.gz"
    protected = [target_path, review_path, revalidation / "download_manifest.csv", revalidation / "url_matches.tsv", revalidation / "revalidation_summary.json"]
    protected_before = {str(path.resolve()): file_sha256(path) for path in protected}

    targets = {row["GlobalEventID"]: row for row in read_tsv(target_path)}
    reviews = {row["GlobalEventID"]: row for row in read_tsv(review_path)}
    with (revalidation / "url_matches.tsv").open(encoding="utf-8", newline="") as stream:
        matches = list(csv.DictReader(stream, delimiter="\t"))
    if set(targets) != set(reviews) & set(targets) or len(targets) != 6:
        raise RuntimeError("Formal GKG target/review scope is not the frozen six-target set")
    if {row["target_id"] for row in matches} != set(targets):
        raise RuntimeError("Revalidation does not contain reliable matches for all six targets")

    raw_root = revalidation / "raw_gkg"
    selected: list[dict[str, str]] = []
    by_target: dict[str, list[dict[str, str]]] = defaultdict(list)
    for match in sorted(matches, key=lambda row: (row["target_id"], row["gkg_file"], int(row["gkg_row_number"]))):
        if match["match_method"] not in MATCHER_SPEC["reliable_priority"]:
            continue
        archive = raw_root / match["gkg_file"]
        gkg = _read_selected_row(archive, int(match["gkg_row_number"]))
        if gkg["V2DOCUMENTIDENTIFIER"] != match["matched_gkg_document_url"]:
            raise RuntimeError("Selected GKG row URL does not match the validated URL")
        row = {
            "GlobalEventID": match["target_id"],
            "target_event_date": match["target_date"],
            "match_type": match["match_method"],
            "date_offset": match["date_offset"],
            "raw_file": match["gkg_file"],
            "raw_sha256": match["gkg_sha256"],
            **gkg,
        }
        selected.append(row)
        by_target[match["target_id"]].append(row)

    event_rows: list[dict[str, Any]] = []
    for event_id in sorted(targets):
        evidence_event = {**reviews[event_id], **targets[event_id]}
        status, reason, demand, goods = classify_post_gkg(evidence_event, by_target[event_id])
        methods = Counter(row["match_type"] for row in by_target[event_id])
        event_rows.append({
            "GlobalEventID": event_id,
            "event_date": targets[event_id]["event_date"],
            "SOURCEURL": targets[event_id]["SOURCEURL"],
            "gkg_match_found": "true",
            "gkg_match_type": next(kind for kind in MATCHER_SPEC["reliable_priority"] if methods[kind]),
            "gkg_record_count": len(by_target[event_id]),
            "gkg_demand_evidence": json.dumps(demand, ensure_ascii=False),
            "gkg_goods_context": json.dumps(goods, ensure_ascii=False),
            "post_gkg_flash_status": status,
            "post_gkg_reason": reason,
        })

    matched_fields = ["GlobalEventID", "target_event_date", "match_type", "date_offset", "raw_file", "raw_sha256", *GKG_FIELDS, "raw_field_count", "raw_line_number"]
    event_fields = ["GlobalEventID", "event_date", "SOURCEURL", "gkg_match_found", "gkg_match_type", "gkg_record_count", "gkg_demand_evidence", "gkg_goods_context", "post_gkg_flash_status", "post_gkg_reason"]
    paths = {
        "matched_records": output / "gkg_demand_risk_matched_records.tsv.gz",
        "event_enrichment": output / "gkg_demand_risk_event_enrichment.tsv.gz",
        "unmatched_targets": output / "gkg_demand_risk_unmatched_targets.tsv.gz",
    }
    _write_gzip(paths["matched_records"], selected, matched_fields)
    _write_gzip(paths["event_enrichment"], event_rows, event_fields)
    _write_gzip(paths["unmatched_targets"], [], event_fields)

    protected_after = {path: file_sha256(Path(path)) for path in protected_before}
    if protected_before != protected_after:
        raise RuntimeError("A protected input changed during formalization")
    statuses = Counter(row["post_gkg_flash_status"] for row in event_rows)
    summary = {
        "status": "FORMAL_DEMAND_RISK_GKG_ACQUISITION_COMPLETE",
        "matcher_spec": MATCHER_SPEC,
        "target_events": len(targets),
        "reliable_matches": len(event_rows),
        "unmatched": 0,
        "raw_archives_retained": len(list(raw_root.glob("*.zip"))),
        "matched_records": len(selected),
        "post_gkg_status_counts": dict(statuses),
        "protected_inputs_sha_unchanged": True,
        "protected_input_sha256": protected_before,
        "outputs": {name: str(path.resolve()) for name, path in paths.items()},
    }
    summary_path = audit / "formal_demand_risk_gkg_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = [
        "# Formal demand-risk GKG acquisition", "",
        "The frozen GKG 2.1 matcher reads DocumentIdentifier at zero-based index 4; index 3 is SourceCommonName.", "",
        f"- Target records: {len(targets)}", f"- Reliable URL matches: {len(event_rows)}", "- Date expansion used: none; all matches were on offset 0.",
        f"- Raw full-day archives retained: {summary['raw_archives_retained']}", f"- Selected GKG evidence rows: {len(selected)}", "",
        "Title similarity remains review-only and cannot create a reliable match. Existing corpus and revalidation inputs were read-only.", "",
        "## Post-GKG evidence status", "",
    ] + [f"- {key}: {value}" for key, value in sorted(statuses.items())]
    (audit / "formal_demand_risk_gkg_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return summary
