#!/usr/bin/env python3
"""Generate the read-only Stage-5B GDELT historical-date audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_risk_trigger_inventory.risk_corpus.gdelt_date_audit import audit_gdelt_dates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--input", type=Path)
    parser.add_argument("--audit-dir", type=Path)
    parser.add_argument("--sample-size", type=int, default=100)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    input_path = args.input or root / "data/local/processed_risk_corpus/processed/gdelt_event_standardized.tsv.gz"
    audit_dir = args.audit_dir or root / "data/local/processed_risk_corpus/audit"
    result = audit_gdelt_dates(input_path, audit_dir, args.sample_size)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
