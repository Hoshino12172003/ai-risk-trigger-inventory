"""Prepare a non-destructive data-layout inventory and dry-run migration plan."""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path


E_ROOT = Path(r"E:\文献阅读\论文写作\第三版AI\数据")
REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING_ROOT = REPO_ROOT / "artifacts/data_layout_audit_safe_migration/E_DATA_ROOT"
MANIFEST_DIR = STAGING_ROOT / "00_README_AND_MANIFEST"

SCAN_ROOTS = [
    E_ROOT,
    REPO_ROOT / "data",
    Path(r"C:\Users\Hu Jiaxin\Documents\Codex\2026-09-20\files-pasted-by-the-user-github\ai-risk-trigger-inventory\data"),
    Path(r"C:\Users\Hu Jiaxin\Documents\Codex\2026-09-27\hoshino12172003-ai-risk-trigger-inventory-draft\work\ai-risk-trigger-inventory\data"),
    Path(r"C:\Users\Hu Jiaxin\Documents\Codex\2026-09-27\hoshino12172003-ai-risk-trigger-inventory-draft\work\ai-risk-trigger-inventory-stage2\data"),
    Path(r"C:\Users\Hu Jiaxin\Documents\Codex\2026-09-27\hoshino12172003-ai-risk-trigger-inventory-draft\work\ai-risk-trigger-inventory-stage3\data"),
    Path(r"C:\Users\Hu Jiaxin\Documents\Codex\2026-09-27\hoshino12172003-ai-risk-trigger-inventory-draft\work\ai-risk-trigger-inventory-stage3c\data"),
    Path(r"C:\Users\Hu Jiaxin\Documents\Codex\2026-09-27\hoshino12172003-ai-risk-trigger-inventory-draft\work\ai-risk-trigger-inventory-stage3d\data"),
]

SOURCES = ["GDELT Event", "ReliefWeb", "USGS", "GDACS", "DesInventar", "Copernicus EMS", "Media Cloud", "Favorita", "M5"]
STAGES = {"RAW", "STANDARDIZED", "CANDIDATE", "AUDIT", "SOURCE_EVENT", "CANONICAL_EVENT", "MODEL_READY", "DIAGNOSTIC", "BASELINE", "UNKNOWN"}
SCOPES = {"ECUADOR_CORE", "ANDEAN_EXTERNAL", "GLOBAL_OTHER", "MIXED", "UNKNOWN"}
RISK_VALUES = {"FLASH_DEMAND", "REGIONAL_DISRUPTION", "TRANSPORT_DISRUPTION", "MULTI_RISK", "NOT_YET_CLASSIFIED", "NOT_APPLICABLE"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_for(path: Path) -> tuple[str, str, str]:
    text = str(path).lower()
    matches = []
    rules = [
        ("GDELT Event", ("gdelt", "gkg")), ("ReliefWeb", ("reliefweb",)),
        ("USGS", ("usgs",)), ("GDACS", ("gdacs",)), ("DesInventar", ("desinventar",)),
        ("Copernicus EMS", ("copernicus", "emsr")), ("Media Cloud", ("mediacloud", "media_cloud", "media cloud")),
        ("Favorita", ("favorita",)), ("M5", ("\\m5\\", "/m5/")),
    ]
    for source, tokens in rules:
        if any(token in text for token in tokens):
            matches.append(source)
    matches = sorted(set(matches))
    if len(matches) == 1:
        return matches[0], "PATH_AND_FILENAME", "HIGH"
    if len(matches) > 1:
        return "MULTIPLE", "MULTIPLE_SOURCE_TOKENS", "LOW"
    return "UNKNOWN", "NO_SOURCE_TOKEN", "LOW"


def stage_for(path: Path) -> tuple[str, str, str]:
    text = str(path).lower().replace("\\", "/")
    name = path.name.lower()
    if "/raw_risk_corpus/" in text or "/reliefweb/data/" in text:
        return "RAW", "RAW_DIRECTORY", "HIGH"
    if "/processed/" in text and "standardized" in name:
        return "STANDARDIZED", "PROCESSED_STANDARDIZED_FILENAME", "HIGH"
    if "/candidates/" in text or "candidate" in name or "screening" in name or "manual_review" in name or "keep_for_review" in name or "likely_false_positive" in name:
        return "CANDIDATE", "CANDIDATE_PATH_OR_FILENAME", "HIGH"
    if "/audit/" in text or "audit" in name or "summary" in name or "manifest" in name or "report" in name:
        return "AUDIT", "AUDIT_PATH_OR_FILENAME", "HIGH"
    if "source_internal" in text or "source_event" in name:
        return "SOURCE_EVENT", "SOURCE_EVENT_TOKEN", "HIGH"
    if "canonical_event" in text or "canonical_event" in name:
        return "CANONICAL_EVENT", "CANONICAL_EVENT_TOKEN", "HIGH"
    if any(token in text for token in ("model_ready", "risk_atom", "uncertainty_set", "scenario_input", "genai_structured")):
        return "MODEL_READY", "MODEL_READY_TOKEN", "HIGH"
    if "diagnostic" in text or path.suffix.lower() in {".log", ".ps1", ".py"}:
        return "DIAGNOSTIC", "DIAGNOSTIC_TOKEN_OR_SCRIPT", "MEDIUM"
    if any(token in text for token in ("/favorita/", "/m5/", "\\favorita\\", "\\m5\\")):
        return "BASELINE", "OPERATIONAL_BASELINE_DIRECTORY", "HIGH"
    if "/gkg_enrichment/demand_risk_formal/" in text:
        return "CANDIDATE", "FORMAL_GKG_EVIDENCE", "HIGH"
    if "/gkg_enrichment/" in text or "/unified_corpus/" in text or "/gkg_targets/" in text:
        return "AUDIT", "DERIVED_GKG_OR_UNIFIED_VIEW", "MEDIUM"
    if path.is_relative_to(E_ROOT):
        return "UNKNOWN", "EXISTING_E_ROOT_FILE_WITHOUT_STAGE_MARKER", "LOW"
    return "UNKNOWN", "NO_STAGE_MARKER", "LOW"


def geography_for(path: Path, source: str, stage: str) -> tuple[str, str, str]:
    text = str(path).lower().replace("\\", "/")
    name = path.name.lower()
    if "ecuador" in text or "ecuador" in name or source == "Favorita":
        return "ECUADOR_CORE", "PATH_OR_SOURCE_ECUADOR", "HIGH"
    if any(token in text for token in ("colombia", "peru", "perú")):
        return "ANDEAN_EXTERNAL", "PATH_COUNTRY_TOKEN", "HIGH"
    if source == "M5":
        return "GLOBAL_OTHER", "M5_US_BASELINE", "HIGH"
    if source == "GDELT Event" and ("full_day" in text or "raw_gkg" in text or path.suffix.lower() == ".zip"):
        return "MIXED", "GLOBAL_GKG_ARCHIVE", "HIGH"
    if source in {"GDELT Event", "ReliefWeb", "USGS", "GDACS", "DesInventar", "Copernicus EMS"} and stage in {"STANDARDIZED", "CANDIDATE", "AUDIT"}:
        return "MIXED", "MULTI_COUNTRY_PIPELINE_OUTPUT", "MEDIUM"
    if source == "Media Cloud":
        return "UNKNOWN", "SOURCE_PRESENT_WITHOUT_GEO_TOKEN", "LOW"
    return "UNKNOWN", "NO_DETERMINISTIC_FILE_LEVEL_GEOGRAPHY", "LOW"


def risk_for(path: Path, stage: str) -> str:
    text = str(path).lower()
    if stage == "BASELINE":
        return "NOT_APPLICABLE"
    flags = []
    if "flash" in text or "demand_risk" in text:
        flags.append("FLASH_DEMAND")
    if any(token in text for token in ("regional", "emergency", "disaster", "earthquake")):
        flags.append("REGIONAL_DISRUPTION")
    if any(token in text for token in ("transport", "route", "network")):
        flags.append("TRANSPORT_DISRUPTION")
    if len(set(flags)) > 1:
        return "MULTI_RISK"
    if flags:
        return flags[0]
    return "NOT_YET_CLASSIFIED"


def logical_source_name(source: str) -> str:
    return {
        "GDELT Event": "GDELT", "Copernicus EMS": "Copernicus", "Media Cloud": "MediaCloud",
    }.get(source, source.replace(" ", "_"))


def proposed_path(path: Path, source: str, stage: str, scope: str) -> tuple[Path, str, str]:
    if path.is_relative_to(E_ROOT) and stage == "RAW":
        return path, "KEEP_IN_PLACE", "Existing immutable raw artifact under frozen E root"
    source_dir = logical_source_name(source) if source not in {"UNKNOWN", "MULTIPLE"} else "UNKNOWN"
    if stage == "RAW":
        target = E_ROOT / "01_RAW" / source_dir / scope / path.name
    elif stage == "STANDARDIZED":
        target = E_ROOT / "02_STANDARDIZED" / source_dir / path.name
    elif stage == "CANDIDATE":
        target = E_ROOT / "03_CANDIDATES_AND_AUDIT" / source_dir / "candidates" / path.name
    elif stage == "AUDIT":
        target = E_ROOT / "03_CANDIDATES_AND_AUDIT" / source_dir / "audit" / path.name
    elif stage == "SOURCE_EVENT":
        target = E_ROOT / "04_SOURCE_INTERNAL_EVENTS" / source_dir / path.name
    elif stage == "CANONICAL_EVENT":
        target = E_ROOT / "05_CANONICAL_EVENTS" / "canonical_event_table" / path.name
    elif stage == "MODEL_READY":
        target = E_ROOT / "07_MODEL_READY" / "scenario_inputs" / path.name
    elif stage == "BASELINE":
        target = E_ROOT / "08_OPERATIONAL_BASELINE" / source_dir / path.name
    elif stage == "DIAGNOSTIC":
        target = E_ROOT / "99_ARCHIVE" / "diagnostics" / source_dir / path.name
    else:
        return path, "MANUAL_REVIEW", "Source or processing stage is unresolved"
    if path.is_relative_to(E_ROOT) and path == target:
        return target, "KEEP_IN_PLACE", "Already at proposed location"
    return target, "COPY", "Dry-run copy-first plan; verify SHA before any later cleanup"


def create_staging_tree() -> None:
    paths = [
        "00_README_AND_MANIFEST",
        *[f"01_RAW/{source}/{scope}" for source in ("GDELT", "ReliefWeb", "USGS", "GDACS", "DesInventar", "Copernicus", "MediaCloud") for scope in ("ECUADOR_CORE", "ANDEAN_EXTERNAL", "GLOBAL_OTHER", "UNKNOWN")],
        *[f"02_STANDARDIZED/{source}" for source in ("GDELT", "ReliefWeb", "USGS", "GDACS", "DesInventar", "Copernicus", "MediaCloud")],
        *[f"03_CANDIDATES_AND_AUDIT/{source}/{leaf}" for source in ("GDELT", "ReliefWeb", "USGS", "GDACS", "DesInventar", "Copernicus") for leaf in ("candidates", "audit")],
        "03_CANDIDATES_AND_AUDIT/MediaCloud/candidates", "03_CANDIDATES_AND_AUDIT/MediaCloud/screening", "03_CANDIDATES_AND_AUDIT/MediaCloud/manual_review",
        *[f"04_SOURCE_INTERNAL_EVENTS/{source}" for source in ("GDELT", "ReliefWeb", "USGS", "GDACS", "DesInventar", "Copernicus", "MediaCloud")],
        "05_CANONICAL_EVENTS/canonical_event_table", "05_CANONICAL_EVENTS/canonical_event_members", "05_CANONICAL_EVENTS/evidence_packages", "05_CANONICAL_EVENTS/linkage_audit",
        "06_RISK_VIEWS/FLASH_DEMAND", "06_RISK_VIEWS/REGIONAL_DISRUPTION", "06_RISK_VIEWS/TRANSPORT_DISRUPTION", "06_RISK_VIEWS/COMPOUND_EVENTS",
        "07_MODEL_READY/genai_structured_events", "07_MODEL_READY/calibrated_risk_atoms", "07_MODEL_READY/uncertainty_sets", "07_MODEL_READY/scenario_inputs",
        "08_OPERATIONAL_BASELINE/Favorita", "08_OPERATIONAL_BASELINE/M5",
        "99_ARCHIVE/superseded", "99_ARCHIVE/diagnostics", "99_ARCHIVE/legacy_layout",
    ]
    for relative in paths:
        (STAGING_ROOT / relative).mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def main() -> None:
    create_staging_tree()
    paths: list[Path] = []
    scanned_roots = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        scanned_roots.append(str(root.resolve()))
        paths.extend(path for path in root.rglob("*") if path.is_file())
    paths = sorted(set(paths), key=lambda path: str(path).lower())

    base_rows = []
    for index, path in enumerate(paths, start=1):
        source, source_basis, source_conf = source_for(path)
        stage, stage_basis, stage_conf = stage_for(path)
        scope, scope_basis, scope_conf = geography_for(path, source, stage)
        risk = risk_for(path, stage)
        confidence = "LOW" if "LOW" in {source_conf, stage_conf, scope_conf} else ("MEDIUM" if "MEDIUM" in {source_conf, stage_conf, scope_conf} else "HIGH")
        digest = sha256_file(path)
        base_rows.append({
            "file_id": f"F{index:06d}", "filename": path.name, "extension": "".join(path.suffixes).lower(),
            "size_bytes": path.stat().st_size, "sha256": digest, "original_path": str(path.resolve()),
            "source_database": source, "processing_stage": stage, "geography_scope": scope,
            "risk_relevance": risk, "inferred_from": ";".join((source_basis, stage_basis, scope_basis)),
            "classification_confidence": confidence, "notes": "",
        })

    by_hash: dict[str, list[dict[str, object]]] = defaultdict(list)
    by_name: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in base_rows:
        by_hash[str(row["sha256"])].append(row); by_name[str(row["filename"]).lower()].append(row)
    duplicate_ids = {str(row["file_id"]) for rows in by_hash.values() if len(rows) > 1 for row in rows}
    name_conflict_ids = {
        str(row["file_id"]) for rows in by_name.values() if len({str(item["sha256"]) for item in rows}) > 1 for row in rows
    }

    migration_rows = []
    for row in base_rows:
        path = Path(str(row["original_path"]))
        target, action, reason = proposed_path(path, str(row["source_database"]), str(row["processing_stage"]), str(row["geography_scope"]))
        target_exists = target.exists()
        collision = "NONE"
        if str(row["file_id"]) in duplicate_ids:
            collision = "DUPLICATE_BY_HASH"
        if str(row["file_id"]) in name_conflict_ids:
            collision = "SAME_NAME_DIFFERENT_HASH_CONFLICT"
        if target_exists and target.resolve() != path.resolve():
            target_hash = sha256_file(target)
            collision = "TARGET_SAME_HASH" if target_hash == row["sha256"] else "TARGET_DIFFERENT_HASH_CONFLICT"
        ambiguous_keyword = any(token in path.name.lower() for token in ("final", "formal", "frozen", "audit", "canonical", "standardized", "validated", "merged"))
        manual = (
            row["source_database"] in {"UNKNOWN", "MULTIPLE"}
            or row["processing_stage"] == "UNKNOWN"
            or row["geography_scope"] == "UNKNOWN"
            or collision in {"SAME_NAME_DIFFERENT_HASH_CONFLICT", "TARGET_DIFFERENT_HASH_CONFLICT"}
            or (ambiguous_keyword and row["classification_confidence"] == "LOW")
        )
        if manual:
            action = "MANUAL_REVIEW"
            reason += "; unresolved identity/geography or collision requires review"
        flags = []
        if str(row["file_id"]) in duplicate_ids: flags.append("DUPLICATE_BY_HASH")
        if str(row["file_id"]) in name_conflict_ids: flags.append("SAME_NAME_DIFFERENT_HASH_CONFLICT")
        if ambiguous_keyword: flags.append("IDENTITY_KEYWORD_PRESENT")
        row["notes"] = ";".join(flags)
        migration_rows.append({
            "file_id": row["file_id"], "current_path": row["original_path"], "proposed_path": str(target),
            "action": action, "reason": reason, "sha256_before": row["sha256"],
            "target_exists": str(target_exists).upper(), "collision_status": collision,
            "manual_review_required": str(manual).upper(),
        })

    inventory_fields = ["file_id", "filename", "extension", "size_bytes", "sha256", "original_path", "source_database", "processing_stage", "geography_scope", "risk_relevance", "inferred_from", "classification_confidence", "notes"]
    migration_fields = ["file_id", "current_path", "proposed_path", "action", "reason", "sha256_before", "target_exists", "collision_status", "manual_review_required"]
    write_csv(MANIFEST_DIR / "file_inventory.csv", base_rows, inventory_fields)
    write_csv(MANIFEST_DIR / "migration_manifest.csv", migration_rows, migration_fields)

    registry = [
        {"source_database": "GDELT Event", "source_type": "EVENT_RECORDS_AND_GKG_DOCUMENT_EVIDENCE", "raw_root": "01_RAW/GDELT", "standardized_root": "02_STANDARDIZED/GDELT", "main_geography": "MIXED", "expected_formats": "ZIP;TSV.GZ;CSV", "event_id_available": "YES_FOR_EVENT_RECORD", "requires_source_internal_clustering": "HIGH", "notes": "GKG reports/document evidence are not source events."},
        {"source_database": "ReliefWeb", "source_type": "DISASTERS_AND_REPORTS", "raw_root": "01_RAW/ReliefWeb", "standardized_root": "02_STANDARDIZED/ReliefWeb", "main_geography": "MIXED", "expected_formats": "JSON;TSV.GZ", "event_id_available": "YES", "requires_source_internal_clustering": "HIGH_FOR_REPORTS_LOW_FOR_DISASTERS", "notes": "Reports are document evidence; disasters are event anchors."},
        {"source_database": "USGS", "source_type": "EARTHQUAKE_EVENTS", "raw_root": "01_RAW/USGS", "standardized_root": "02_STANDARDIZED/USGS", "main_geography": "MIXED", "expected_formats": "GEOJSON;JSON;TSV.GZ", "event_id_available": "YES", "requires_source_internal_clustering": "LOW", "notes": "Stable USGS event IDs."},
        {"source_database": "GDACS", "source_type": "DISASTER_EVENTS", "raw_root": "01_RAW/GDACS", "standardized_root": "02_STANDARDIZED/GDACS", "main_geography": "MIXED", "expected_formats": "JSON;GEOJSON;TSV.GZ", "event_id_available": "YES", "requires_source_internal_clustering": "LOW", "notes": "Structured event IDs."},
        {"source_database": "DesInventar", "source_type": "DISASTER_LOSS_RECORDS", "raw_root": "01_RAW/DesInventar", "standardized_root": "02_STANDARDIZED/DesInventar", "main_geography": "ANDEAN_EXTERNAL", "expected_formats": "CSV;XLSX;TSV.GZ", "event_id_available": "YES", "requires_source_internal_clustering": "MEDIUM", "notes": "Colombia and Peru source schemas differ."},
        {"source_database": "Copernicus EMS", "source_type": "EMERGENCY_ACTIVATIONS", "raw_root": "01_RAW/Copernicus", "standardized_root": "02_STANDARDIZED/Copernicus", "main_geography": "MIXED", "expected_formats": "JSON;TSV.GZ", "event_id_available": "YES", "requires_source_internal_clustering": "LOW", "notes": "Activation IDs are stable anchors."},
        {"source_database": "Media Cloud", "source_type": "NEWS_DOCUMENTS", "raw_root": "01_RAW/MediaCloud", "standardized_root": "02_STANDARDIZED/MediaCloud", "main_geography": "UNKNOWN", "expected_formats": "JSON;CSV;TSV.GZ", "event_id_available": "SOURCE_DEPENDENT", "requires_source_internal_clustering": "HIGH", "notes": "No local source files detected in the scanned roots."},
        {"source_database": "Favorita", "source_type": "OPERATIONAL_BASELINE", "raw_root": "08_OPERATIONAL_BASELINE/Favorita", "standardized_root": "08_OPERATIONAL_BASELINE/Favorita", "main_geography": "ECUADOR_CORE", "expected_formats": "CSV;ZIP", "event_id_available": "NOT_APPLICABLE", "requires_source_internal_clustering": "NO", "notes": "Normal-demand baseline; never mix with risk-event sources."},
        {"source_database": "M5", "source_type": "OPERATIONAL_BASELINE", "raw_root": "08_OPERATIONAL_BASELINE/M5", "standardized_root": "08_OPERATIONAL_BASELINE/M5", "main_geography": "GLOBAL_OTHER", "expected_formats": "CSV", "event_id_available": "NOT_APPLICABLE", "requires_source_internal_clustering": "NO", "notes": "Normal-demand baseline; never mix with risk-event sources."},
    ]
    write_csv(MANIFEST_DIR / "source_registry.csv", registry, list(registry[0]))

    README = """# Third-paper data structure

This is a dry-run, non-destructive layout. The authoritative data root is `E:\\文献阅读\\论文写作\\第三版AI\\数据`. Existing files were inventoried and hashed but not deleted, overwritten, renamed, moved, or modified. Because the execution environment could not write to E:, this complete structure is staged under the repository and every proposed migration path still targets the authoritative E: root.

## Data chain

`Record -> Source Event -> Canonical Real-world Event -> Model Risk Event`

- `01_RAW`: immutable source artifacts.
- `02_STANDARDIZED`: standardized record-level data.
- `03_CANDIDATES_AND_AUDIT`: high-recall candidates, refinement, human review, and audit outputs.
- `04_SOURCE_INTERNAL_EVENTS`: within-source record consolidation.
- `05_CANONICAL_EVENTS`: cross-source linkage into real-world events.
- `06_RISK_VIEWS`: derived Flash, Regional, Transport, and Compound mappings; never duplicated raw data.
- `07_MODEL_READY`: GenAI-structured records, calibrated risk atoms, uncertainty sets, and optimization inputs.
- `08_OPERATIONAL_BASELINE`: Favorita and M5 normal-demand baselines, separate from risk databases.
- `99_ARCHIVE`: diagnostics, superseded outputs, and legacy layouts retained without deletion.

## Migration safety

This round creates only inventory, hashes, classification, empty directories, and a dry-run plan. Later migration must use `COPY -> verify SHA-256 -> verify counts -> review collisions -> consider cleanup`. Rows marked `MANUAL_REVIEW` must never migrate automatically.
"""
    (MANIFEST_DIR / "README_data_structure.md").write_text(README, encoding="utf-8")
    dictionary = """# Data dictionary

## file_inventory.csv

- `file_id`: stable identifier assigned by deterministic path ordering.
- `source_database`: inferred database/source family.
- `processing_stage`: one of RAW, STANDARDIZED, CANDIDATE, AUDIT, SOURCE_EVENT, CANONICAL_EVENT, MODEL_READY, DIAGNOSTIC, BASELINE, UNKNOWN.
- `geography_scope`: ECUADOR_CORE, ANDEAN_EXTERNAL, GLOBAL_OTHER, MIXED, or UNKNOWN.
- `risk_relevance`: metadata only; it never determines RAW placement.
- `classification_confidence`: confidence in file-level classification, not research confidence.

## migration_manifest.csv

- `action`: KEEP_IN_PLACE, COPY, MOVE_AFTER_APPROVAL, ARCHIVE_AFTER_APPROVAL, or MANUAL_REVIEW. This dry run emits no destructive action.
- `collision_status`: NONE, DUPLICATE_BY_HASH, SAME_NAME_DIFFERENT_HASH_CONFLICT, TARGET_SAME_HASH, or TARGET_DIFFERENT_HASH_CONFLICT.
- `manual_review_required`: TRUE blocks automatic migration.

Risk labels are derived metadata/views. They do not create separate copies of raw records. A physical file containing multiple geographies remains intact and receives `MIXED` rather than being split.
"""
    (MANIFEST_DIR / "data_dictionary.md").write_text(dictionary, encoding="utf-8")

    counts = {
        "scanned_roots": scanned_roots, "files": len(base_rows),
        "bytes": sum(int(row["size_bytes"]) for row in base_rows),
        "by_source": dict(Counter(str(row["source_database"]) for row in base_rows)),
        "by_stage": dict(Counter(str(row["processing_stage"]) for row in base_rows)),
        "by_geography": dict(Counter(str(row["geography_scope"]) for row in base_rows)),
        "safe_planned": sum(row["action"] in {"KEEP_IN_PLACE", "COPY"} and row["manual_review_required"] == "FALSE" for row in migration_rows),
        "manual_review": sum(row["manual_review_required"] == "TRUE" for row in migration_rows),
        "duplicate_by_hash": len(duplicate_ids), "same_name_different_hash_conflict": len(name_conflict_ids),
    }
    print(json.dumps(counts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
