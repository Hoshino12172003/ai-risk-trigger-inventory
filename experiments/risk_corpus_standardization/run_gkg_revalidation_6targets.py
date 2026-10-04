from pathlib import Path

from ai_risk_trigger_inventory.risk_corpus.gkg_revalidation import run_revalidation


if __name__ == "__main__":
    repository = Path(__file__).resolve().parents[2]
    processed_root = repository / "data/local/processed_risk_corpus"
    result = run_revalidation(processed_root, workers=64)
    print(result)
