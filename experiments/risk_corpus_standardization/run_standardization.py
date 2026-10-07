#!/usr/bin/env python3
"""Discover, audit, and standardize the local Stage-5A risk corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_risk_trigger_inventory.risk_corpus.standardization import run_standardization


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--external-gdelt-root", type=Path, action="append", default=[])
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--reuse-complete-outputs", action="store_true")
    args = parser.parse_args()
    output_root = args.output_root or args.repo_root / "data/local/processed_risk_corpus"
    result = run_standardization(
        args.repo_root.resolve(), args.external_gdelt_root, output_root.resolve(),
        reuse_complete_outputs=args.reuse_complete_outputs,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
