from pathlib import Path
import json

from ai_risk_trigger_inventory.risk_corpus.gkg_formalization import run_formal_demand_risk_gkg


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2] / "data/local/processed_risk_corpus"
    print(json.dumps(run_formal_demand_risk_gkg(root), ensure_ascii=False, indent=2))
