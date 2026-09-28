from __future__ import annotations

import csv
import json
from pathlib import Path
import subprocess

import pytest

from ai_risk_trigger_inventory.risk_corpus.raw_acquisition import file_digest
from ai_risk_trigger_inventory.risk_corpus.supplement import (
    BATCH_ID,
    filter_event_row,
    filter_gkg_row,
    gdelt_monthly_anchor_days,
)


ROOT = Path(__file__).resolve().parents[1]
BASELINE = "ead6d5ae02502382d63402959a8120dbbfe54fc6"
STAGE4A = "7ad5243eecbadec677be9ada0548804c316d6002"
PAPER2_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
PAPER2 = Path(
    r"C:\Users\Hu Jiaxin\Documents\Codex\2026-09-20\files-pasted-by-the-user-github\work\budget-inventory-benders-frozen-51aebd0"
)
RAW_ROOT = ROOT / "data" / "local" / "raw_risk_corpus"
ARTIFACTS = ROOT / "artifacts" / "risk_corpus_stage5a_supplement"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def local_supplement_or_skip() -> None:
    if not (RAW_ROOT / "gdelt" / "supplement").exists():
        pytest.skip("ignored Stage-5A supplement metadata is not present")


def test_branch_derives_from_accepted_stage5a_baseline() -> None:
    subprocess.run(["git", "merge-base", "--is-ancestor", BASELINE, "HEAD"], cwd=ROOT, check=True)


def test_accepted_artifacts_are_unchanged() -> None:
    assert subprocess.check_output(
        ["git", "diff", "--name-only", BASELINE, "--", "artifacts/risk_corpus_raw_stage5a"],
        cwd=ROOT, text=True,
    ).strip() == ""
    protected = (
        "artifacts/compound_risk_stage1", "artifacts/compound_risk_stage2",
        "artifacts/risk_event_library_stage3", "artifacts/risk_impact_mapping_stage3c",
        "artifacts/route_risk_calibration_stage3d", "artifacts/m5_demand_calibration_stage4",
        "artifacts/m5_event_proxy_refinement_stage4a",
    )
    assert subprocess.check_output(
        ["git", "diff", "--name-only", STAGE4A, "--", *protected], cwd=ROOT, text=True,
    ).strip() == ""


def test_all_accepted_baseline_raw_hashes_remain_valid() -> None:
    local_supplement_or_skip()
    names = {
        "GDELT": "gdelt", "ReliefWeb": "reliefweb", "GDACS": "gdacs",
        "DesInventar": "desinventar", "USGS": "usgs", "Copernicus EMS": "copernicus",
    }
    with (ROOT / "artifacts" / "risk_corpus_raw_stage5a" / "download_manifest.csv").open(
        encoding="utf-8", newline="",
    ) as handle:
        rows = list(csv.DictReader(handle))
    checked = 0
    for row in rows:
        if row["status_result"] != "DOWNLOADED":
            continue
        path = RAW_ROOT / names[row["source"]] / "raw" / row["filename"]
        assert path.is_file()
        assert file_digest(path) == row["sha256"]
        checked += 1
    assert checked == 108


def test_supplement_scope_is_limited_to_three_sources() -> None:
    local_supplement_or_skip()
    sources = {
        path.parent.parent.name for path in RAW_ROOT.glob("*/supplement/manifest.json")
    }
    assert sources == {"gdelt", "reliefweb", "desinventar"}


def test_gdelt_frozen_filters_require_geography_and_risk_terms() -> None:
    event = [""] * 68
    event[0], event[1], event[47], event[67] = (
        "event-1", "20200101", "EC", "https://example.org/earthquake-road-closure",
    )
    kept = filter_event_row(event)
    assert kept is not None
    assert kept["countries"] == ("Ecuador",)
    assert kept["geography_provenance"] == "STRUCTURED_EVENT_GEO"
    event[67] = "https://example.org/sports"
    assert filter_event_row(event) is None
    event[47], event[67] = "US", "https://example.org/earthquake"
    assert filter_event_row(event) is None


def test_gkg_structured_and_textual_geography_provenance_are_distinct() -> None:
    gkg = [""] * 16
    gkg[0], gkg[1], gkg[4], gkg[8], gkg[10] = (
        "gkg-1", "20200101120000", "https://example.org/report", "ENV_FLOODING", "1#Quito#EC#",
    )
    structured = filter_gkg_row(gkg)
    assert structured is not None
    assert structured["geography_provenance"] == "STRUCTURED_GKG_LOCATION"
    gkg[10], gkg[4] = "", "https://example.org/ecuador/flood"
    textual = filter_gkg_row(gkg)
    assert textual is not None
    assert textual["geography_provenance"] == "TEXTUAL_DOCUMENT_URL"


def test_monthly_gdelt_anchors_stay_inside_analytical_window() -> None:
    anchors = gdelt_monthly_anchor_days()
    assert anchors[0] == "20150219"
    assert anchors[-1] == "20260831"
    assert len(anchors) == len(set(anchors))
    assert all("20150101" <= value <= "20260831" for value in anchors)


def test_blocked_sources_and_no_unaccepted_raw_payloads() -> None:
    local_supplement_or_skip()
    gdelt = read_json(RAW_ROOT / "gdelt" / "supplement" / "manifest.json")
    reliefweb = read_json(RAW_ROOT / "reliefweb" / "supplement" / "manifest.json")
    desinventar = read_json(RAW_ROOT / "desinventar" / "supplement" / "manifest.json")
    assert gdelt["records_retained"] == 0
    assert gdelt["records_inspected"] == 187375
    assert not list((RAW_ROOT / "gdelt" / "supplement" / "raw").glob("*"))
    assert reliefweb["status"] == "AUTH_BLOCKED"
    assert reliefweb["approved_appname_available"] is False
    assert desinventar["attempt_count"] == 2
    assert desinventar["download_success"] is False
    assert not list((RAW_ROOT / "desinventar" / "supplement" / "raw").glob("*"))


def test_supplement_audit_forbids_downstream_processing_and_secrets() -> None:
    audit = read_json(ARTIFACTS / "supplement_execution_audit.json")
    assert audit["status"] == "STAGE_5A_SUPPLEMENT_BLOCKED"
    assert audit["supplement_batch_id"] == BATCH_ID
    assert audit["baseline_raw_before"]["unchanged"] is True
    assert audit["baseline_raw_after"]["unchanged"] is True
    assert audit["source_fusion_performed"] is False
    assert audit["canonical_events_created"] is False
    assert audit["missing_value_imputation_performed"] is False
    assert audit["genai_dispatches"] == 0
    assert audit["llm_extractions"] == 0
    assert audit["ml_training_dispatches"] == 0
    assert audit["optimizer_dispatches"] == 0
    assert audit["delta_d_recalibrated"] is False
    assert audit["delta_a_recalibrated"] is False
    tracked_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (
            ROOT / "src" / "ai_risk_trigger_inventory" / "risk_corpus" / "supplement.py",
            ROOT / "experiments" / "risk_corpus_stage5a_supplement" / "acquire_supplement.py",
            ROOT / "docs" / "risk_corpus_stage5a_supplement.md",
        )
    )
    assert "RELIEFWEB_APPNAME=" not in tracked_text
    assert "RELIEFWEB_API_APPNAME=" not in tracked_text


def test_manifest_hashes_for_any_retained_supplement_files() -> None:
    local_supplement_or_skip()
    with (ARTIFACTS / "supplement_download_manifest.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        filename = row.get("filename", "")
        if not filename or not row.get("filtered_file_retained") == "True":
            continue
        path = RAW_ROOT / "gdelt" / "supplement" / "raw" / filename
        assert path.is_file()
        assert file_digest(path) == row["filtered_sha256"]


def test_frozen_paper2_checkout_is_unchanged() -> None:
    git = ["git", "-c", f"safe.directory={PAPER2.as_posix()}"]
    assert subprocess.check_output([*git, "rev-parse", "HEAD"], cwd=PAPER2, text=True).strip() == PAPER2_SHA
    assert subprocess.check_output([*git, "status", "--porcelain"], cwd=PAPER2, text=True).strip() == ""
