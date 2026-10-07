import csv
import gzip
import json
from pathlib import Path

import pytest

from ai_risk_trigger_inventory.risk_corpus.human_review import (
    HUMAN_FIELDS,
    create_review_template,
    summarize_review,
)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def _write_index(path: Path) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["source", "source_record_id", "country_iso3"], delimiter="\t", lineterminator="\n")
        writer.writeheader(); writer.writerows([
            {"source": "GDELT", "source_record_id": "1", "country_iso3": "COL"},
            {"source": "RELIEFWEB", "source_record_id": "2", "country_iso3": "PER"},
        ])


def test_template_preserves_sample_and_leaves_human_fields_blank(tmp_path: Path) -> None:
    sample = tmp_path / "sample.csv"; output = tmp_path / "review.csv"
    rows = [
        {"record_id": "1", "source": "GDELT", "risk_family": "FLASH_DEMAND_SURGE", "priority": "B",
         "geo_evidence": "ACTION_GEO:STRONG", "candidate_flash_demand": "true",
         "candidate_regional_emergency": "false", "candidate_transport_network": "false"},
        {"record_id": "2", "source": "RELIEFWEB", "risk_family": "TRANSPORT_NETWORK_DISRUPTION", "priority": "A",
         "geo_evidence": "PRIMARY_COUNTRY:STRONG", "candidate_flash_demand": "false",
         "candidate_regional_emergency": "true", "candidate_transport_network": "true"},
    ]
    _write_csv(sample, rows)
    assert create_review_template(sample, output) == 2
    with output.open(encoding="utf-8", newline="") as stream:
        actual = list(csv.DictReader(stream))
    assert [{key: row[key] for key in rows[0]} for row in actual] == rows
    assert all(row[field] == "" for row in actual for field in HUMAN_FIELDS)


def test_summary_reports_descriptive_review_rates_and_validates_values(tmp_path: Path) -> None:
    review = tmp_path / "review.csv"; index = tmp_path / "index.tsv.gz"
    rows = [
        {"record_id": "1", "source": "GDELT", "risk_family": "FLASH_DEMAND_SURGE", "priority": "B",
         "geo_evidence": "ACTION_GEO:STRONG", "candidate_flash_demand": "true",
         "candidate_regional_emergency": "false", "candidate_transport_network": "false",
         "human_relevant": "YES", "human_primary_family": "FLASH_DEMAND_SURGE", "human_geo_valid": "YES",
         "human_transport_disruption_valid": "NOT_APPLICABLE", "human_demand_surge_valid": "YES",
         "human_false_positive_reason": "", "human_notes": ""},
        {"record_id": "2", "source": "RELIEFWEB", "risk_family": "TRANSPORT_NETWORK_DISRUPTION", "priority": "A",
         "geo_evidence": "PRIMARY_COUNTRY:STRONG", "candidate_flash_demand": "false",
         "candidate_regional_emergency": "true", "candidate_transport_network": "true",
         "human_relevant": "NO", "human_primary_family": "NONE", "human_geo_valid": "YES",
         "human_transport_disruption_valid": "NO", "human_demand_surge_valid": "NOT_APPLICABLE",
         "human_false_positive_reason": "TRANSPORT_IMPACT_NOT_DISRUPTION", "human_notes": ""},
    ]
    _write_csv(review, rows); _write_index(index)
    summary = summarize_review(review, index)
    assert summary["overall"]["relevance_rate"] == 0.5
    assert summary["by_country"]["COL"]["relevance_rate"] == 1.0
    assert summary["candidate_family_audit_rates"]["FLASH_DEMAND_SURGE"]["human_audit_rate"] == 1.0
    assert summary["candidate_family_audit_rates"]["TRANSPORT_NETWORK_DISRUPTION"]["human_audit_rate"] == 0.0
    rows[0]["human_relevant"] = "MAYBE"
    _write_csv(review, rows)
    with pytest.raises(ValueError, match="invalid human_relevant"):
        summarize_review(review, index)
