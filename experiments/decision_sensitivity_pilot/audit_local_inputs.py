"""Audit the four local Favorita inputs without downloading or modifying them."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd


SOURCE = "Favorita / Kaggle Store Sales, cleaned externally"
TARGET_STATES = {"Pichincha", "Guayas"}
ROLLING_STD_ABSOLUTE_TOLERANCE = 1e-4
REQUIRED_FILES = (
    "favorita_store_family_day.csv.gz",
    "favorita_state_family_week_states.csv.gz",
    "state_network_contexts.csv",
    "oracle_pilot_candidates.csv",
)
WEEKLY_REQUIRED_COLUMNS = {
    "state",
    "family",
    "week_start",
    "sales",
    "sales_mean_4w",
    "sales_mean_8w",
    "sales_mean_13w",
}
FILE_COLUMN_STATUS = {
    "favorita_store_family_day.csv.gz": {
        "status": "OBSERVED_AND_DERIVED",
        "observed": [
            "id",
            "date",
            "store_nbr",
            "family",
            "sales",
            "onpromotion",
            "city",
            "state",
            "type",
            "cluster",
            "dcoilwtico",
        ],
        "derived": [
            "transactions_clean",
            "oil_imputed",
            "transaction_missing",
            "transaction_pre_active",
            "transaction_post_active",
            "transaction_internal_missing",
            "holiday_event_count",
            "holiday_any",
            "holiday_national",
            "holiday_regional",
            "holiday_local",
            "dow",
            "month",
            "year",
        ],
    },
    "favorita_state_family_week_states.csv.gz": {
        "status": "DERIVED",
        "observed": [],
        "derived": "ALL_COLUMNS",
    },
    "state_network_contexts.csv": {
        "status": "DERIVED",
        "observed": [],
        "derived": "ALL_COLUMNS",
    },
    "oracle_pilot_candidates.csv": {
        "status": "DERIVED",
        "observed": [],
        "derived": "ALL_COLUMNS",
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def csv_metadata(path: Path) -> dict[str, object]:
    row_count = 0
    columns: list[str] | None = None
    for chunk in pd.read_csv(path, chunksize=100_000):
        if columns is None:
            columns = list(chunk.columns)
        row_count += len(chunk)
    return {
        "filename": path.name,
        "sha256": sha256_file(path),
        "file_size": path.stat().st_size,
        "row_count": row_count,
        "columns": columns or list(pd.read_csv(path, nrows=0).columns),
        "source": SOURCE,
        "observed_derived_status": FILE_COLUMN_STATUS[path.name],
    }


def canonical_json(document: object) -> str:
    """Return deterministic UTF-8 JSON text with one trailing newline."""

    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def build_manifest(input_dir: Path) -> dict[str, object]:
    missing = [name for name in REQUIRED_FILES if not (input_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"BLOCKED_MISSING_LOCAL_INPUT: {missing}")
    return {
        "manifest_version": 1,
        "source": SOURCE,
        "files": [csv_metadata(input_dir / name) for name in sorted(REQUIRED_FILES)],
    }


def _daily_panel_audit(path: Path) -> dict[str, object]:
    hashes: list[np.ndarray] = []
    states: set[str] = set()
    row_count = 0
    required = {"date", "store_nbr", "family", "state"}
    for chunk in pd.read_csv(path, usecols=lambda name: name in required, chunksize=200_000):
        missing = required - set(chunk.columns)
        if missing:
            raise ValueError(f"daily panel missing columns: {sorted(missing)}")
        states.update(chunk["state"].dropna().astype(str).unique())
        hashes.append(
            pd.util.hash_pandas_object(
                chunk[["date", "store_nbr", "family"]], index=False
            ).to_numpy(dtype=np.uint64)
        )
        row_count += len(chunk)
    key_hashes = np.concatenate(hashes)
    key_hashes.sort()
    duplicate_count = int(np.count_nonzero(key_hashes[1:] == key_hashes[:-1]))
    if duplicate_count:
        raise ValueError(
            "daily panel (date, store_nbr, family) key is not unique; "
            f"duplicate hashes={duplicate_count}"
        )
    missing_states = TARGET_STATES - states
    if missing_states:
        raise ValueError(f"daily panel missing pilot states: {sorted(missing_states)}")
    return {
        "row_count": row_count,
        "key": ["date", "store_nbr", "family"],
        "key_unique": True,
        "pilot_states_available": sorted(TARGET_STATES),
    }


def validate_weekly_baselines(frame: pd.DataFrame) -> dict[str, object]:
    missing = WEEKLY_REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"weekly states missing columns: {sorted(missing)}")
    weekly = frame.copy()
    weekly["week_start"] = pd.to_datetime(weekly["week_start"], errors="raise")
    weekly = weekly.sort_values(["state", "family", "week_start"])
    if weekly.duplicated(["state", "family", "week_start"]).any():
        raise ValueError("weekly (state, family, week_start) key is not unique")
    group = weekly.groupby(["state", "family"], sort=False)["sales"]
    checks: dict[str, dict[str, object]] = {}
    for window in (4, 8, 13):
        mean_column = f"sales_mean_{window}w"
        expected_mean = group.transform(
            lambda values: values.shift(1).rolling(window, min_periods=1).mean()
        )
        mean_mask = weekly[mean_column].notna()
        mean_match = bool(
            np.allclose(
                weekly.loc[mean_mask, mean_column],
                expected_mean.loc[mean_mask],
                rtol=1e-9,
                atol=1e-9,
            )
        )
        if not mean_match:
            raise ValueError(f"{mean_column} is not a shift(1)-then-rolling feature")
        checks[mean_column] = {
            "window": window,
            "shift_periods": 1,
            "non_null_rows_checked": int(mean_mask.sum()),
            "matches_prior_only_history": True,
        }

        std_column = f"sales_std_{window}w"
        if std_column in weekly:
            expected_std = group.transform(
                lambda values: values.shift(1).rolling(window, min_periods=2).std()
            )
            std_mask = weekly[std_column].notna()
            std_match = bool(
                np.allclose(
                    weekly.loc[std_mask, std_column],
                    expected_std.loc[std_mask],
                    rtol=1e-9,
                    atol=ROLLING_STD_ABSOLUTE_TOLERANCE,
                )
            )
            if not std_match:
                raise ValueError(f"{std_column} is not a shift(1)-then-rolling feature")
            checks[std_column] = {
                "window": window,
                "shift_periods": 1,
                "non_null_rows_checked": int(std_mask.sum()),
                "matches_prior_only_history": True,
                "absolute_tolerance": ROLLING_STD_ABSOLUTE_TOLERANCE,
            }

    if "sales_change_vs_4w" in weekly:
        baseline = weekly["sales_mean_4w"]
        expected_change = (weekly["sales"] - baseline) / baseline
        mask = weekly["sales_change_vs_4w"].notna()
        if not np.allclose(
            weekly.loc[mask, "sales_change_vs_4w"],
            expected_change.loc[mask],
            rtol=1e-9,
            atol=1e-9,
        ):
            raise ValueError("sales_change_vs_4w is inconsistent with the prior-only baseline")
        checks["sales_change_vs_4w"] = {
            "depends_on": "sales_mean_4w",
            "non_null_rows_checked": int(mask.sum()),
            "matches_prior_only_history": True,
        }

    if "sales_z_8w" in weekly:
        expected_z = (
            weekly["sales"] - weekly["sales_mean_8w"]
        ) / weekly["sales_std_8w"]
        mask = weekly["sales_z_8w"].notna()
        if not np.allclose(
            weekly.loc[mask, "sales_z_8w"],
            expected_z.loc[mask],
            rtol=1e-9,
            atol=1e-9,
        ):
            raise ValueError("sales_z_8w is inconsistent with prior-only rolling features")
        checks["sales_z_8w"] = {
            "depends_on": ["sales_mean_8w", "sales_std_8w"],
            "non_null_rows_checked": int(mask.sum()),
            "matches_prior_only_history": True,
        }

    states = set(weekly["state"].astype(str))
    missing_states = TARGET_STATES - states
    if missing_states:
        raise ValueError(f"weekly states missing pilot states: {sorted(missing_states)}")
    return {
        "row_count": len(weekly),
        "key": ["state", "family", "week_start"],
        "key_unique": True,
        "required_columns_present": True,
        "pilot_states_available": sorted(TARGET_STATES),
        "future_leakage_detected": False,
        "rolling_feature_checks": checks,
    }


def audit_local_inputs(input_dir: Path) -> tuple[dict[str, object], dict[str, object]]:
    manifest = build_manifest(input_dir)
    daily = _daily_panel_audit(input_dir / "favorita_store_family_day.csv.gz")
    weekly_frame = pd.read_csv(input_dir / "favorita_state_family_week_states.csv.gz")
    weekly = validate_weekly_baselines(weekly_frame)
    contexts = pd.read_csv(input_dir / "state_network_contexts.csv")
    candidates = pd.read_csv(input_dir / "oracle_pilot_candidates.csv")
    for name, frame in (("network contexts", contexts), ("oracle candidates", candidates)):
        if "state" not in frame:
            raise ValueError(f"{name} missing state column")
        missing_states = TARGET_STATES - set(frame["state"].astype(str))
        if missing_states:
            raise ValueError(f"{name} missing pilot states: {sorted(missing_states)}")
    audit = {
        "status": "PASS",
        "daily_panel": daily,
        "weekly_states": weekly,
        "network_contexts": {
            "row_count": len(contexts),
            "pilot_states_available": sorted(TARGET_STATES),
        },
        "oracle_candidates": {
            "row_count": len(candidates),
            "pilot_states_available": sorted(TARGET_STATES),
        },
    }
    return manifest, audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir", type=Path, default=Path("data/local/favorita")
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("data/manifests/favorita_local_manifest.json"),
    )
    parser.add_argument(
        "--audit",
        type=Path,
        default=Path("artifacts/decision_sensitivity_pilot/local_input_audit.json"),
    )
    args = parser.parse_args()
    try:
        manifest, audit = audit_local_inputs(args.input_dir)
    except (FileNotFoundError, ValueError) as error:
        print(f"BLOCKED_MISSING_LOCAL_INPUT: {error}")
        raise SystemExit(2) from error
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(canonical_json(manifest), encoding="utf-8")
    args.audit.write_text(canonical_json(audit), encoding="utf-8")
    print("PASS: local Favorita input audit")


if __name__ == "__main__":
    main()
