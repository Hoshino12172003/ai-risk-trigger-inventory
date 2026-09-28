import json
from pathlib import Path
import sys


EXPERIMENT = (
    Path(__file__).resolve().parents[1]
    / "experiments"
    / "decision_sensitivity_pilot"
)
sys.path.insert(0, str(EXPERIMENT))

from audit_m5_inputs import FILES, build_manifest


def test_missing_m5_manifest_is_deterministic() -> None:
    missing = Path(__file__).resolve().parents[1] / "data/local/m5-test-missing"
    first = build_manifest(missing)
    second = build_manifest(missing)

    assert first == second
    assert first["status"] == "BLOCKED_MISSING_LOCAL_INPUT"
    assert first["missing_files"] == list(FILES)
    assert first["optimizer_calls"] == 0


def test_m5_manifest_schema_and_provenance() -> None:
    missing = Path(__file__).resolve().parents[1] / "data/local/m5-test-missing"
    manifest = build_manifest(missing)

    assert len(manifest["files"]) == 4
    assert all(
        {"filename", "present", "sha256", "file_size", "row_count", "columns",
         "source", "status"} <= set(entry)
        for entry in manifest["files"]
    )
    assert "inventory" in manifest["field_provenance"]["CALIBRATED"]
    assert "price" in manifest["field_provenance"]["OBSERVED"]


def test_committed_manifest_matches_recorded_availability() -> None:
    root = Path(__file__).resolve().parents[1]
    committed = json.loads(
        (root / "data/manifests/m5_local_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    data_dir = root / "data/local/m5"
    if committed["status"] == "BLOCKED_MISSING_LOCAL_INPUT":
        data_dir = root / "data/local/m5-test-missing"

    assert committed == build_manifest(data_dir)
