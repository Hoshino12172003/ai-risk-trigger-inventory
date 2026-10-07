#!/usr/bin/env python3
"""Run ReliefWeb Stage 5B-1R inside the existing processed corpus root."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from ai_risk_trigger_inventory.risk_corpus.reliefweb_standardization import run_reliefweb_standardization


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--raw-root", type=Path,
        default=Path(r"E:\文献阅读\论文写作\第三版AI\数据\ReliefWeb\data"),
    )
    parser.add_argument(
        "--output-root", type=Path,
        default=Path(__file__).resolve().parents[2] / "data/local/processed_risk_corpus",
    )
    args = parser.parse_args()
    result = run_reliefweb_standardization(args.raw_root, args.output_root)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
