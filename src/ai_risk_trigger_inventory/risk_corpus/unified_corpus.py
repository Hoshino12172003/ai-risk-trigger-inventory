"""Non-destructive inventory and geographic views for the historical risk corpus."""

from __future__ import annotations

from collections import Counter
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
from typing import Any, Iterator


csv.field_size_limit(100 * 1024 * 1024)
TARGET_ISO3 = {"ECU", "COL", "PER"}
ISO_MAP = {"EC": "ECU", "ECU": "ECU", "CO": "COL", "COL": "COL", "PE": "PER", "PER": "PER"}
UNIFIED_FIELDS = [
    "source", "source_record_type", "source_record_id", "event_date",
    "primary_country_iso3", "country_iso3_list", "geo_match_basis",
    "is_ecuador", "is_andean", "source_file",
]


def normalize_iso3(value: str) -> str:
    value = (value or "").strip().upper()
    return ISO_MAP.get(value, value if len(value) == 3 and value.isalpha() else "")


def parse_country_list(value: str) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        values = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        values = [part.strip() for part in value.replace(";", ",").split(",")]
    return sorted({code for item in values if (code := normalize_iso3(str(item)))})


def harmonize_record(source: str, record_type: str, row: dict[str, str], source_file: str) -> dict[str, str]:
    if source == "RELIEFWEB":
        primary = normalize_iso3(row.get("primary_country_iso3", ""))
        countries = parse_country_list(row.get("country_iso3_list", ""))
        record_id = row.get("source_record_id", "")
        event_date = row.get("event_date", "") if record_type == "EVENT" else row.get("date_original", "")
        basis = row.get("target_geo_match_basis", "SOURCE_NATIVE_COUNTRY_LIST") or "SOURCE_NATIVE_COUNTRY_LIST"
    elif source == "GKG":
        primary = normalize_iso3(row.get("target_country_code", ""))
        countries = [primary] if primary else []
        record_id = row.get("GKGRECORDID", "")
        event_date = row.get("target_event_date", "")
        basis = "LINKED_GDELT_ACTION_GEO"
    else:
        primary = normalize_iso3(row.get("country_iso3", ""))
        countries = [primary] if primary else []
        record_id = row.get("source_record_id", "")
        event_date = row.get("event_date", "")
        basis = "STANDARDIZED_SOURCE_GEOGRAPHY" if primary else "SOURCE_GEOGRAPHY_MISSING"
    if primary and primary not in countries:
        countries = sorted({*countries, primary})
    return {
        "source": source,
        "source_record_type": record_type,
        "source_record_id": record_id,
        "event_date": event_date,
        "primary_country_iso3": primary,
        "country_iso3_list": json.dumps(countries, ensure_ascii=False),
        "geo_match_basis": basis,
        "is_ecuador": str("ECU" in countries).lower(),
        "is_andean": str(bool(TARGET_ISO3 & set(countries))).lower(),
        "source_file": source_file,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _reader(path: Path) -> Iterator[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        yield from csv.DictReader(stream, delimiter="\t")


class _DeterministicGzipTable:
    def __init__(self, path: Path, fields: list[str]):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.raw = path.open("wb")
        self.compressed = gzip.GzipFile(filename="", mode="wb", fileobj=self.raw, mtime=0)
        self.text = io.TextIOWrapper(self.compressed, encoding="utf-8", newline="")
        self.writer = csv.DictWriter(self.text, fieldnames=fields, delimiter="\t", lineterminator="\n")
        self.writer.writeheader()

    def write(self, row: dict[str, Any]) -> None:
        self.writer.writerow(row)

    def close(self) -> None:
        self.text.close()


def run_unified_corpus_views(root: Path) -> dict[str, Any]:
    unified = root / "unified_corpus"
    inventory_dir, layout_dir = unified / "inventory", unified / "layout"
    geography_dir, subsets_dir = unified / "geography", unified / "subsets"
    sources = [
        ("GDELT", "EVENT", root / "processed/gdelt_event_standardized.tsv.gz"),
        ("USGS", "EVENT", root / "processed/usgs_standardized.tsv.gz"),
        ("GDACS", "EVENT", root / "processed/gdacs_standardized.tsv.gz"),
        ("DESINVENTAR", "EVENT", root / "processed/desinventar_standardized.tsv.gz"),
        ("COPERNICUS_EMS", "EVENT", root / "processed/copernicus_ems_standardized.tsv.gz"),
        ("RELIEFWEB", "EVENT", root / "processed/reliefweb_disasters_standardized.tsv.gz"),
        ("RELIEFWEB", "DOCUMENT_EVIDENCE", root / "processed/reliefweb_reports_standardized.tsv.gz"),
        ("GKG", "DOCUMENT_EVIDENCE", root / "gkg_enrichment/demand_risk_formal/gkg_demand_risk_matched_records.tsv.gz"),
    ]
    missing = [str(path) for _, _, path in sources if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing unified-corpus inputs: " + ", ".join(missing))
    input_sha = {str(path.resolve()): _sha256(path) for _, _, path in sources}

    all_table = _DeterministicGzipTable(geography_dir / "geographic_harmonization.tsv.gz", UNIFIED_FIELDS)
    ecu_table = _DeterministicGzipTable(subsets_dir / "ecuador_subset.tsv.gz", UNIFIED_FIELDS)
    andean_table = _DeterministicGzipTable(subsets_dir / "andean_subset.tsv.gz", UNIFIED_FIELDS)
    inventory: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    country_counts: Counter[str] = Counter()
    ecuador_count = andean_count = 0

    target_geo_by_event: dict[str, str] = {}
    with gzip.open(root / "gkg_targets/flash_gkg_target_events.tsv.gz", "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            target_geo_by_event[row["GlobalEventID"]] = row.get("ActionGeo_CountryCode", "")

    try:
        for source, record_type, path in sources:
            rows = 0
            min_date = max_date = ""
            for row in _reader(path):
                if source == "GKG":
                    row["target_country_code"] = target_geo_by_event.get(row.get("GlobalEventID", ""), "")
                harmonized = harmonize_record(source, record_type, row, str(path.resolve()))
                all_table.write(harmonized)
                rows += 1
                source_counts[f"{source}:{record_type}"] += 1
                for code in parse_country_list(harmonized["country_iso3_list"]):
                    country_counts[code] += 1
                event_date = harmonized["event_date"]
                if event_date:
                    min_date = event_date if not min_date or event_date < min_date else min_date
                    max_date = event_date if not max_date or event_date > max_date else max_date
                if harmonized["is_ecuador"] == "true":
                    ecu_table.write(harmonized)
                    ecuador_count += 1
                if harmonized["is_andean"] == "true":
                    andean_table.write(harmonized)
                    andean_count += 1
            inventory.append({
                "source": source, "record_type": record_type, "absolute_path": str(path.resolve()),
                "relative_path": str(path.relative_to(root)).replace("\\", "/"), "format": "tsv.gz",
                "size_bytes": path.stat().st_size, "row_count": rows, "min_date": min_date,
                "max_date": max_date, "sha256": input_sha[str(path.resolve())], "role": "STANDARDIZED_INPUT",
            })
    finally:
        all_table.close(); ecu_table.close(); andean_table.close()

    inventory_dir.mkdir(parents=True, exist_ok=True)
    inventory_fields = list(inventory[0])
    with (inventory_dir / "data_inventory.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=inventory_fields, lineterminator="\n"); writer.writeheader(); writer.writerows(inventory)
    (inventory_dir / "data_inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    layout_dir.mkdir(parents=True, exist_ok=True)
    layout_rows = [{
        "source": row["source"], "record_type": row["record_type"], "current_path": row["absolute_path"],
        "logical_path": f"sources/{row['source'].lower()}/{row['record_type'].lower()}/{Path(row['absolute_path']).name}",
        "migration_action": "REFERENCE_ONLY", "original_file_moved": "false",
    } for row in inventory]
    with (layout_dir / "directory_map.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(layout_rows[0]), lineterminator="\n"); writer.writeheader(); writer.writerows(layout_rows)
    (layout_dir / "README.md").write_text(
        "# Non-destructive corpus layout\n\nThis directory is a logical catalog. `directory_map.csv` references existing files; no source or processed file was moved, renamed, copied, or deleted.\n",
        encoding="utf-8",
    )
    geography_dir.mkdir(parents=True, exist_ok=True)
    crosswalk = [
        {"source_code": code, "country_iso3": ISO_MAP[code], "country_scope": "ECUADOR" if ISO_MAP[code] == "ECU" else "ANDEAN"}
        for code in sorted(ISO_MAP)
    ]
    with (geography_dir / "country_code_crosswalk.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(crosswalk[0]), lineterminator="\n"); writer.writeheader(); writer.writerows(crosswalk)
    with (geography_dir / "geographic_summary.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["country_iso3", "record_count"], lineterminator="\n"); writer.writeheader()
        writer.writerows({"country_iso3": key, "record_count": value} for key, value in sorted(country_counts.items()))

    placebo_status = {
        "status": "DEFINITION_REQUIRED",
        "records_written": 0,
        "reason": "A placebo geography or exclusion rule was not specified; non-Andean records were not silently treated as placebo.",
    }
    (subsets_dir / "placebo_subset_status.json").write_text(json.dumps(placebo_status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    input_after = {path: _sha256(Path(path)) for path in input_sha}
    if input_sha != input_after:
        raise RuntimeError("A standardized input changed during unified-corpus processing")
    summary = {
        "status": "UNIFIED_CORPUS_NON_DESTRUCTIVE_VIEWS_COMPLETE",
        "inventory_files": len(inventory),
        "total_records": sum(int(row["row_count"]) for row in inventory),
        "source_record_counts": dict(sorted(source_counts.items())),
        "ecuador_subset_records": ecuador_count,
        "andean_subset_records": andean_count,
        "placebo_subset_status": placebo_status["status"],
        "directory_restructure_mode": "REFERENCE_ONLY",
        "inputs_sha_unchanged": True,
        "input_sha256": input_sha,
        "root": str(unified.resolve()),
    }
    audit = root / "audit"
    (audit / "unified_risk_corpus_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = [
        "# Unified risk-corpus inventory and geography audit", "",
        "All outputs are non-destructive views. Original standardized files remain in place and retain their SHA-256 values.", "",
        f"- Inventory inputs: {len(inventory)}", f"- Total records indexed: {summary['total_records']:,}",
        f"- Ecuador subset: {ecuador_count:,}", f"- Andean subset (ECU/COL/PER): {andean_count:,}",
        "- Placebo subset: pending an explicit methodological definition.", "",
        "ReliefWeb reports and GKG records remain document evidence, not event records. The logical directory map uses REFERENCE_ONLY and performs no physical migration.", "",
    ]
    (audit / "unified_risk_corpus_report.md").write_text("\n".join(report), encoding="utf-8")
    return summary
