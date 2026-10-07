#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from ai_risk_trigger_inventory.risk_corpus.human_review import (
    create_review_template,
    summarize_review,
    write_summary,
)


ROOT = Path(__file__).resolve().parents[2] / "data/local/processed_risk_corpus"


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--sample", type=Path, default=ROOT / "audit/stage5b2_candidate_audit_sample.csv")
    prepare.add_argument("--output", type=Path, default=ROOT / "audit/stage5b2_human_review_template.csv")
    summarize = subparsers.add_parser("summarize")
    summarize.add_argument("--review", type=Path, default=ROOT / "audit/stage5b2_human_review_template.csv")
    summarize.add_argument("--candidate-index", type=Path, default=ROOT / "candidates/all_risk_candidate_index.tsv.gz")
    summarize.add_argument("--output", type=Path, default=ROOT / "audit/stage5b2_human_review_summary.json")
    args = parser.parse_args()
    if args.command == "prepare":
        print(f"rows={create_review_template(args.sample, args.output)}")
    else:
        write_summary(summarize_review(args.review, args.candidate_index), args.output)
        print(args.output)


if __name__ == "__main__":
    main()
