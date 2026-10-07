from __future__ import annotations

import csv
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import pytest

from ai_risk_trigger_inventory.risk_corpus.raw_acquisition import (
    REQUESTED_END,
    REQUESTED_START,
)


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "data" / "local" / "raw_risk_corpus"
ARTIFACTS = ROOT / "artifacts" / "risk_corpus_raw_stage5a"
SOURCES = ("gdelt", "reliefweb", "gdacs", "desinventar", "usgs", "copernicus")
ALLOWED_STATUSES = {
    "COMPLETE_FOR_REQUESTED_SCOPE",
    "PARTIAL_SOURCE_COVERAGE",
    "AUTH_BLOCKED",
    "MANUAL_DOWNLOAD_REQUIRED",
    "API_LIMITATION",
    "DOWNLOAD_FAILED",
}
STAGE4A_COMMIT = "7ad5243eecbadec677be9ada0548804c316d6002"
PAPER2_SHA = "51aebd06edf8f5d6d124d0f3eebdbb901e63274f"
PAPER2 = Path(
    r"C:\Users\Hu Jiaxin\Documents\Codex\2026-09-20\files-pasted-by-the-user-github\work\budget-inventory-benders-frozen-51aebd0"
)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def local_raw_or_skip() -> Path:
    if not RAW_ROOT.exists():
        pytest.skip("ignored Stage-5A raw corpus is not present in this checkout")
    return RAW_ROOT


def test_requested_time_range_is_frozen() -> None:
    assert REQUESTED_START == "2015-01-01T00:00:00Z"
    assert REQUESTED_END == "2026-08-31T23:59:59Z"


def test_every_source_has_local_layout_and_metadata() -> None:
    root = local_raw_or_skip()
    for source in SOURCES:
        source_dir = root / source
        assert (source_dir / "raw").is_dir()
        assert (source_dir / "manifest.json").is_file()
        assert (source_dir / "download_log.csv").is_file()
        assert (source_dir / "schema_snapshot.json").is_file()
        manifest = read_json(source_dir / "manifest.json")
        assert manifest["requested_start_date"] == REQUESTED_START
        assert manifest["requested_end_date"] == REQUESTED_END
        assert manifest["status"] in ALLOWED_STATUSES


def test_downloaded_files_exist_and_match_logged_sha256() -> None:
    root = local_raw_or_skip()
    downloaded = 0
    for source in SOURCES:
        with (root / source / "download_log.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if row["status_result"] != "DOWNLOADED":
                    continue
                downloaded += 1
                path = root / source / "raw" / row["filename"]
                assert path.is_file()
                assert path.stat().st_size == int(row["size_bytes"])
                assert row["sha256"]
                assert sha256(path.read_bytes()).hexdigest() == row["sha256"]
    assert downloaded == 108


def test_extracted_api_records_with_dates_are_inside_requested_window() -> None:
    root = local_raw_or_skip()
    start = datetime.fromisoformat(REQUESTED_START.replace("Z", "+00:00"))
    end = datetime.fromisoformat(REQUESTED_END.replace("Z", "+00:00"))

    for path in sorted((root / "usgs" / "raw").glob("*.geojson")):
        for feature in read_json(path)["features"]:
            event_time = datetime.fromtimestamp(
                feature["properties"]["time"] / 1000, tz=timezone.utc,
            )
            assert start <= event_time <= end

    for path in sorted((root / "gdacs" / "raw").glob("gdacs_*.json")):
        if path.stat().st_size == 0:
            continue
        for feature in read_json(path)["features"]:
            event_time = datetime.fromisoformat(feature["properties"]["fromdate"]).replace(
                tzinfo=timezone.utc,
            )
            assert start <= event_time <= end

    for path in sorted((root / "copernicus" / "raw").glob("rapid_*.json")):
        if path.name.endswith("activation_list.json"):
            continue
        record = read_json(path)
        value = record.get("eventTime") or record.get("activationTime")
        event_time = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        assert start <= event_time <= end

    desinventar = read_json(root / "desinventar" / "manifest.json")
    for audit in desinventar["country_archive_audits"].values():
        assert REQUESTED_START[:10] <= audit["in_scope_min_date"] <= REQUESTED_END[:10]
        assert REQUESTED_START[:10] <= audit["in_scope_max_date"] <= REQUESTED_END[:10]


def test_source_identifiers_are_preserved() -> None:
    root = local_raw_or_skip()
    usgs = next((root / "usgs" / "raw").glob("*.geojson"))
    assert all(feature.get("id") for feature in read_json(usgs)["features"])

    gdacs = next(path for path in (root / "gdacs" / "raw").glob("gdacs_*.json") if path.stat().st_size)
    for feature in read_json(gdacs)["features"]:
        properties = feature["properties"]
        assert properties.get("eventtype")
        assert properties.get("eventid") is not None
        assert properties.get("episodeid") is not None

    copernicus = next(
        path for path in (root / "copernicus" / "raw").glob("rapid_*.json")
        if not path.name.endswith("activation_list.json")
    )
    assert read_json(copernicus).get("code")


def test_raw_sources_are_separate_and_git_ignored() -> None:
    root = local_raw_or_skip()
    assert not [path for path in root.iterdir() if path.is_file()]
    for source in SOURCES:
        result = subprocess.run(
            ["git", "check-ignore", str(root / source / "raw")],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert result.returncode == 0


def test_execution_audit_excludes_downstream_processing() -> None:
    audit = read_json(ARTIFACTS / "execution_audit.json")
    assert audit["source_count"] == 6
    assert audit["source_fusion_performed"] is False
    assert audit["missing_value_imputation_performed"] is False
    assert audit["canonical_events_created"] is False
    assert audit["genai_dispatches"] == 0
    assert audit["optimizer_dispatches"] == 0
    assert audit["ml_training_dispatches"] == 0


def test_prior_stage_artifacts_are_unchanged() -> None:
    protected = (
        "artifacts/compound_risk_stage1",
        "artifacts/compound_risk_stage2",
        "artifacts/risk_event_library_stage3",
        "artifacts/risk_impact_mapping_stage3c",
        "artifacts/route_risk_calibration_stage3d",
        "artifacts/m5_demand_calibration_stage4",
        "artifacts/m5_event_proxy_refinement_stage4a",
    )
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", STAGE4A_COMMIT, "--", *protected],
        cwd=ROOT, text=True,
    ).strip()
    assert changed == ""


def test_frozen_paper2_checkout_is_unchanged() -> None:
    git = ["git", "-c", f"safe.directory={PAPER2.as_posix()}"]
    assert subprocess.check_output([*git, "rev-parse", "HEAD"], cwd=PAPER2, text=True).strip() == PAPER2_SHA
    assert subprocess.check_output([*git, "status", "--porcelain"], cwd=PAPER2, text=True).strip() == ""
