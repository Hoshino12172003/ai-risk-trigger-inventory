"""Stage 5B-2H human-review template and descriptive audit summaries."""

from __future__ import annotations

from collections import defaultdict
import csv
import gzip
import json
from pathlib import Path
from typing import Any, Iterable


HUMAN_FIELDS = [
    "human_relevant",
    "human_primary_family",
    "human_geo_valid",
    "human_transport_disruption_valid",
    "human_demand_surge_valid",
    "human_false_positive_reason",
    "human_notes",
]

ALLOWED_VALUES = {
    "human_relevant": {"", "YES", "NO", "UNCERTAIN"},
    "human_primary_family": {
        "", "FLASH_DEMAND_SURGE", "REGIONAL_EMERGENCY_DISRUPTION",
        "TRANSPORT_NETWORK_DISRUPTION", "MULTIPLE", "NONE", "UNCERTAIN",
    },
    "human_geo_valid": {"", "YES", "NO", "UNCERTAIN"},
    "human_transport_disruption_valid": {"", "YES", "NO", "NOT_APPLICABLE", "UNCERTAIN"},
    "human_demand_surge_valid": {"", "YES", "NO", "NOT_APPLICABLE", "UNCERTAIN"},
    "human_false_positive_reason": {
        "", "BACKGROUND_MENTION", "HISTORICAL_REFERENCE", "ACTOR_GEO_ONLY",
        "WEAK_KEYWORD", "PREPAREDNESS_ONLY", "TRANSPORT_IMPACT_NOT_DISRUPTION",
        "SHORTAGE_WITHOUT_DEMAND_SURGE", "WRONG_GEOGRAPHY", "OTHER",
    },
}


def create_review_template(sample_path: Path, output_path: Path) -> int:
    with sample_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        original_fields = list(reader.fieldnames or [])
        if any(field in original_fields for field in HUMAN_FIELDS):
            raise ValueError("Input sample already contains human-review fields")
        rows = list(reader)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=original_fields + HUMAN_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, **{field: "" for field in HUMAN_FIELDS}})
    return len(rows)


def _validate(rows: list[dict[str, str]]) -> None:
    for row_number, row in enumerate(rows, start=2):
        for field, allowed in ALLOWED_VALUES.items():
            value = row.get(field, "").strip()
            if value not in allowed:
                raise ValueError(f"Row {row_number}: invalid {field}={value!r}")


def _rate(rows: Iterable[dict[str, str]]) -> dict[str, Any]:
    values = [row.get("human_relevant", "").strip() for row in rows]
    yes = values.count("YES"); no = values.count("NO"); reviewed = yes + no
    return {
        "reviewed_yes_no": reviewed, "relevant_yes": yes, "irrelevant_no": no,
        "uncertain": values.count("UNCERTAIN"), "unreviewed_blank": values.count(""),
        "relevance_rate": yes / reviewed if reviewed else None,
    }


def _country_lookup(candidate_index: Path) -> dict[tuple[str, str], str]:
    values: dict[tuple[str, str], set[str]] = defaultdict(set)
    with gzip.open(candidate_index, "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            values[(row["source"], row["source_record_id"])].add(row.get("country_iso3", "") or "UNKNOWN")
    return {key: next(iter(countries)) if len(countries) == 1 else "UNKNOWN" for key, countries in values.items()}


def _group_rates(rows: list[dict[str, str]], field: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[row.get(field, "") or "UNKNOWN"].append(row)
    return {key: _rate(groups[key]) for key in sorted(groups)}


def _family_validity(rows: list[dict[str, str]], candidate_field: str, review_field: str) -> dict[str, Any]:
    candidates = [row for row in rows if row.get(candidate_field, "").lower() == "true"]
    values = [row.get(review_field, "").strip() for row in candidates]
    reviewed = sum(value in {"YES", "NO"} for value in values)
    valid = values.count("YES")
    return {"candidate_rows": len(candidates), "reviewed_yes_no": reviewed, "valid_yes": valid,
            "human_audit_rate": valid / reviewed if reviewed else None}


def summarize_review(review_path: Path, candidate_index: Path) -> dict[str, Any]:
    with review_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    _validate(rows)
    countries = _country_lookup(candidate_index)
    enriched = [{**row, "audit_country_iso3": countries.get((row["source"], row["record_id"]), "UNKNOWN")} for row in rows]
    regional_candidates = [row for row in enriched if row.get("candidate_regional_emergency", "").lower() == "true"]
    regional_values = [row.get("human_primary_family", "").strip() for row in regional_candidates]
    regional_reviewed = sum(value not in {"", "UNCERTAIN"} for value in regional_values)
    regional_valid = sum(value in {"REGIONAL_EMERGENCY_DISRUPTION", "MULTIPLE"} for value in regional_values)
    return {
        "interpretation": (
            "Descriptive human audit rates for the reviewed sample only; not statistical population "
            "precision estimates and not automatically extrapolated to the full corpus."
        ),
        "worksheet_rows": len(enriched),
        "overall": _rate(enriched),
        "by_source": _group_rates(enriched, "source"),
        "by_risk_family": _group_rates(enriched, "risk_family"),
        "by_priority": _group_rates(enriched, "priority"),
        "by_country": _group_rates(enriched, "audit_country_iso3"),
        "by_geo_evidence_class": _group_rates(enriched, "geo_evidence"),
        "candidate_family_audit_rates": {
            "FLASH_DEMAND_SURGE": _family_validity(
                enriched, "candidate_flash_demand", "human_demand_surge_valid"
            ),
            "REGIONAL_EMERGENCY_DISRUPTION": {
                "candidate_rows": len(regional_candidates), "reviewed_primary_family": regional_reviewed,
                "valid_regional_or_multiple": regional_valid,
                "human_audit_rate": regional_valid / regional_reviewed if regional_reviewed else None,
            },
            "TRANSPORT_NETWORK_DISRUPTION": _family_validity(
                enriched, "candidate_transport_network", "human_transport_disruption_valid"
            ),
        },
    }


def write_summary(summary: dict[str, Any], output_path: Path) -> None:
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
