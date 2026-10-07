#!/usr/bin/env python3
from pathlib import Path
import json
import sys

from ai_risk_trigger_inventory.risk_corpus.candidate_screening import run_candidate_screening


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2] / "data/local/processed_risk_corpus"
    result = run_candidate_screening(root)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
