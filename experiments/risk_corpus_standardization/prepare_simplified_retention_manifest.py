from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path, PureWindowsPath


REPO = Path(__file__).resolve().parents[2]
INPUT = (
    REPO
    / "artifacts"
    / "data_layout_audit_safe_migration"
    / "E_DATA_ROOT"
    / "00_README_AND_MANIFEST"
    / "file_inventory.csv"
)
STAGING_ROOT = (
    REPO
    / "artifacts"
    / "data_layout_audit_safe_migration"
    / "E_DATA_ROOT_SIMPLIFIED"
)
FORMAL_ROOT = PureWindowsPath(r"E:\文献阅读\论文写作\第三版AI\数据")

RETENTION_CLASSES = {
    "KEEP_RAW",
    "KEEP_CURATED",
    "KEEP_EVENT",
    "KEEP_MODEL",
    "KEEP_BASELINE",
    "SUMMARY_ONLY",
    "ARCHIVE_OPTIONAL",
    "DISCARD_AFTER_APPROVAL",
    "MANUAL_REVIEW",
}

SOURCE_DIRS = {
    "GDELT Event": "GDELT",
    "ReliefWeb": "ReliefWeb",
    "USGS": "USGS",
    "GDACS": "GDACS",
    "DesInventar": "DesInventar",
    "Copernicus EMS": "Copernicus",
    "Media Cloud": "MediaCloud",
    "Favorita": "Favorita",
    "M5": "M5",
}

CURATED_FILES = {
    "common_event_index.tsv.gz",
    "common_event_index_reliefweb_addition.tsv.gz",
    "high_confidence_candidate_index.tsv.gz",
    "flash_candidate_evidence_index.tsv.gz",
    "flash_gdelt_evidence_review_post_gkg.tsv.gz",
    "flash_reliefweb_evidence_review.tsv.gz",
    "gkg_demand_risk_event_enrichment.tsv.gz",
    "gkg_demand_risk_matched_records.tsv.gz",
    "reliefweb_report_index.tsv.gz",
    "geographic_harmonization.tsv.gz",
    "andean_subset.tsv.gz",
    "ecuador_subset.tsv.gz",
    "country_code_crosswalk.csv",
}

EVENT_FILES = {
    "flash_demand_canonical_events_v1.xlsx": "CANONICAL_EVENTS",
    "reliefweb_report_disaster_links.tsv.gz": "RISK_MAPPING",
}

MODEL_FILE_TOKENS = (
    "genai_structured",
    "risk_atoms",
    "optimization_inputs",
)

SUMMARY_FILE_TOKENS = (
    "summary",
    "report",
    "quality",
    "inventory",
    "manifest",
    "audit",
)

TEMPORARY_FILE_TOKENS = (
    "candidate_matches",
    "unmatched_targets",
    "checkpoint",
    "diagnostic",
    "human_review",
    "rule_hits",
    "false_positive",
    "deferred_candidate",
    "secondary_candidate",
    "download_manifest",
    "full_day",
    "revalidation",
)


def create_layout() -> None:
    directories = [
        "00_MANIFEST",
        "01_RAW_KEEP/GDELT",
        "01_RAW_KEEP/ReliefWeb",
        "01_RAW_KEEP/USGS",
        "01_RAW_KEEP/GDACS",
        "01_RAW_KEEP/DesInventar",
        "01_RAW_KEEP/Copernicus",
        "01_RAW_KEEP/MediaCloud",
        "02_CURATED/STANDARDIZED",
        "02_CURATED/ACCEPTED_RISK_RECORDS",
        "02_CURATED/REJECTED_SUMMARY",
        "03_EVENTS/SOURCE_EVENTS",
        "03_EVENTS/CANONICAL_EVENTS",
        "03_EVENTS/RISK_MAPPING",
        "04_MODEL_READY/GENAI_STRUCTURED",
        "04_MODEL_READY/RISK_ATOMS",
        "04_MODEL_READY/OPTIMIZATION_INPUTS",
        "05_BASELINE/Favorita",
        "05_BASELINE/M5",
    ]
    for directory in directories:
        (STAGING_ROOT / directory).mkdir(parents=True, exist_ok=True)


def source_dir(row: dict[str, str]) -> str:
    return SOURCE_DIRS.get(row["source_database"], "UNKNOWN")


def destination(row: dict[str, str], subdir: str) -> str:
    return str(FORMAL_ROOT / subdir / source_dir(row) / row["filename"])


def is_test_gkg(row: dict[str, str]) -> bool:
    path = row["original_path"].lower().replace("/", "\\")
    return (
        "gkg_revalidation_6targets" in path
        or "gkg_flash_targeted" in path
        or "flash_full_day" in path
    )


def canonical_duplicate_ids(rows: list[dict[str, str]]) -> set[str]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row["sha256"]].append(row)

    selected: set[str] = set()
    for group in groups.values():
        if len(group) == 1:
            selected.add(group[0]["file_id"])
            continue

        def rank(row: dict[str, str]) -> tuple[int, int, str]:
            path = row["original_path"].lower()
            e_priority = 0 if path.startswith("e:\\") else 1
            current_priority = 0 if "2026-09-28" in path else 1
            return e_priority, current_priority, path

        selected.add(min(group, key=rank)["file_id"])
    return selected


def classify(
    row: dict[str, str], canonical_ids: set[str]
) -> tuple[str, str, str, str, str]:
    filename = row["filename"]
    lower = filename.lower()
    stage = row["processing_stage"]
    file_id = row["file_id"]

    if file_id not in canonical_ids:
        return (
            "DISCARD_AFTER_APPROVAL",
            "Byte-identical duplicate; retain the selected authoritative copy only",
            "",
            "TRUE",
            "FALSE",
        )

    if is_test_gkg(row):
        return (
            "DISCARD_AFTER_APPROVAL",
            "Test or revalidation GKG capture; formal enrichment outputs preserve the result",
            "",
            "TRUE",
            "FALSE",
        )

    if stage == "RAW":
        if lower == "download_log.csv" or "supplement" in row["original_path"].lower():
            return (
                "ARCHIVE_OPTIONAL",
                "Acquisition log or incomplete supplement rather than an authoritative raw payload",
                "",
                "FALSE",
                "FALSE",
            )
        return (
            "KEEP_RAW",
            "Authoritative source payload or provenance/schema companion",
            destination(row, "01_RAW_KEEP"),
            "FALSE",
            "FALSE",
        )

    if stage == "STANDARDIZED":
        return (
            "KEEP_CURATED",
            "Final source-standardized main table",
            destination(row, "02_CURATED/STANDARDIZED"),
            "FALSE",
            "FALSE",
        )

    if filename in CURATED_FILES:
        return (
            "KEEP_CURATED",
            "Final accepted record index, harmonized view, or required curated linkage input",
            destination(row, "02_CURATED/ACCEPTED_RISK_RECORDS"),
            "FALSE",
            "FALSE",
        )

    if filename in EVENT_FILES:
        event_subdir = EVENT_FILES[filename]
        return (
            "KEEP_EVENT",
            "Formal event-level or native event-linkage result",
            destination(row, f"03_EVENTS/{event_subdir}"),
            "FALSE",
            "FALSE",
        )

    if stage == "SOURCE_EVENT":
        return (
            "KEEP_EVENT",
            "Source-internal event table",
            destination(row, "03_EVENTS/SOURCE_EVENTS"),
            "FALSE",
            "FALSE",
        )

    if stage == "CANONICAL_EVENT":
        return (
            "KEEP_EVENT",
            "Canonical real-world event table",
            destination(row, "03_EVENTS/CANONICAL_EVENTS"),
            "FALSE",
            "FALSE",
        )

    if stage == "MODEL_READY" or any(token in lower for token in MODEL_FILE_TOKENS):
        subdir = "OPTIMIZATION_INPUTS"
        if "genai_structured" in lower:
            subdir = "GENAI_STRUCTURED"
        elif "risk_atom" in lower:
            subdir = "RISK_ATOMS"
        return (
            "KEEP_MODEL",
            "Final model-ready data product",
            destination(row, f"04_MODEL_READY/{subdir}"),
            "FALSE",
            "FALSE",
        )

    if stage == "BASELINE":
        return (
            "KEEP_BASELINE",
            "Operational demand baseline or required derived baseline table",
            destination(row, "05_BASELINE"),
            "FALSE",
            "FALSE",
        )

    if filename == "gkg_demand_risk_unmatched_targets.tsv.gz":
        return (
            "SUMMARY_ONLY",
            "No unmatched formal targets remain; retain only the aggregate screening statement",
            str(FORMAL_ROOT / "02_CURATED/REJECTED_SUMMARY/screening_summary.csv"),
            "TRUE",
            "FALSE",
        )

    if any(token in lower for token in TEMPORARY_FILE_TOKENS):
        return (
            "DISCARD_AFTER_APPROVAL",
            "Intermediate screening, rejected-detail, checkpoint, or diagnostic artifact",
            "",
            "TRUE",
            "FALSE",
        )

    if stage in {"AUDIT", "DIAGNOSTIC"} or any(
        token in lower for token in SUMMARY_FILE_TOKENS
    ):
        return (
            "SUMMARY_ONLY",
            "Replace detailed historical audit output with the consolidated screening summary",
            str(FORMAL_ROOT / "02_CURATED/REJECTED_SUMMARY/screening_summary.csv"),
            "FALSE",
            "FALSE",
        )

    if stage == "CANDIDATE":
        return (
            "ARCHIVE_OPTIONAL",
            "Superseded candidate or review table; not part of the accepted-record corpus",
            "",
            "FALSE",
            "FALSE",
        )

    return (
        "MANUAL_REVIEW",
        "Retention purpose cannot be established unambiguously from the inventory metadata",
        "",
        "FALSE",
        "TRUE",
    )


def write_screening_summary() -> None:
    output = STAGING_ROOT / "02_CURATED/REJECTED_SUMMARY/screening_summary.csv"
    fields = [
        "source_database",
        "screening_stage",
        "input_count",
        "accepted_count",
        "rejected_count",
        "main_rejection_reasons",
        "final_output_file",
        "date",
        "notes",
    ]
    rows = [
        {
            "source_database": "GDELT Event",
            "screening_stage": "Stage 5B-2 high-recall screening",
            "input_count": 2737459,
            "accepted_count": 26789,
            "rejected_count": 2710670,
            "main_rejection_reasons": "No deterministic risk-rule hit; outside candidate criteria",
            "final_output_file": "high_confidence_candidate_index.tsv.gz",
            "date": "2026-10-05",
            "notes": "Input count is the in-analysis-window GDELT population.",
        },
        {
            "source_database": "ReliefWeb reports",
            "screening_stage": "Stage 5B-2 evidence screening",
            "input_count": 32598,
            "accepted_count": 19601,
            "rejected_count": 12997,
            "main_rejection_reasons": "No explicit regional, transport, or demand-pressure evidence",
            "final_output_file": "high_confidence_candidate_index.tsv.gz",
            "date": "2026-10-05",
            "notes": "Reports remain document evidence and are not treated as events.",
        },
        {
            "source_database": "MULTI_SOURCE",
            "screening_stage": "Stage 5B-2R high-confidence refinement",
            "input_count": 64508,
            "accepted_count": 40993,
            "rejected_count": 23515,
            "main_rejection_reasons": "BACKGROUND_MENTION; WEAK_EMERGENCY_WORD; ACTOR_GEO_ONLY; WRONG_GEOGRAPHY; PREPAREDNESS_ONLY",
            "final_output_file": "high_confidence_candidate_index.tsv.gz",
            "date": "2026-10-05",
            "notes": "Rejected count combines SECONDARY and DEFERRED tiers for formal retention purposes.",
        },
        {
            "source_database": "GDELT Event; ReliefWeb reports",
            "screening_stage": "Stage 5B-2F flash-demand adjudication",
            "input_count": 154,
            "accepted_count": 3,
            "rejected_count": 151,
            "main_rejection_reasons": "GENERIC_HOARDING_OR_STOCKPILING; WEAK_OR_AMBIGUOUS_KEYWORD; SPY_GEAR_STOCKPILING; LAND_HOARDING",
            "final_output_file": "flash_candidate_evidence_index.tsv.gz",
            "date": "2026-10-05",
            "notes": "Three records had true flash-demand evidence before targeted GKG enrichment.",
        },
        {
            "source_database": "GDELT GKG",
            "screening_stage": "Formal demand-risk GKG enrichment",
            "input_count": 6,
            "accepted_count": 0,
            "rejected_count": 5,
            "main_rejection_reasons": "Matched source evidence did not substantiate flash demand",
            "final_output_file": "gkg_demand_risk_event_enrichment.tsv.gz",
            "date": "2026-10-05",
            "notes": "All six URLs matched reliably; five were false positives and one remained plausible but insufficient.",
        },
    ]
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    create_layout()
    with INPUT.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 833:
        raise ValueError(f"Expected 833 inventory rows, found {len(rows)}")

    canonical_ids = canonical_duplicate_ids(rows)
    output_rows = []
    for row in rows:
        retention, reason, proposed, discard, manual = classify(row, canonical_ids)
        if retention not in RETENTION_CLASSES:
            raise ValueError(f"Invalid retention class: {retention}")
        output_rows.append(
            {
                "file_id": row["file_id"],
                "filename": row["filename"],
                "current_path": row["original_path"],
                "source_database": row["source_database"],
                "processing_stage": row["processing_stage"],
                "retention_class": retention,
                "reason": reason,
                "proposed_destination": proposed,
                "safe_to_discard_later": discard,
                "manual_review_required": manual,
            }
        )

    manifest = STAGING_ROOT / "00_MANIFEST/simplified_retention_manifest.csv"
    fields = list(output_rows[0])
    with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)

    write_screening_summary()

    counts = Counter(row["retention_class"] for row in output_rows)
    print(f"manifest={manifest}")
    print(f"rows={len(output_rows)}")
    for key in sorted(counts):
        print(f"{key}={counts[key]}")


if __name__ == "__main__":
    main()
