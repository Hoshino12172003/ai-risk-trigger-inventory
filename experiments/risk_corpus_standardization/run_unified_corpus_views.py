from pathlib import Path
import json

from ai_risk_trigger_inventory.risk_corpus.unified_corpus import run_unified_corpus_views


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2] / "data/local/processed_risk_corpus"
    print(json.dumps(run_unified_corpus_views(root), ensure_ascii=False, indent=2))
