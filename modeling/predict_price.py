from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modeling.price_model import RealEstatePriceModel


def main() -> None:
    parser = argparse.ArgumentParser(description="Predict a property price from a feature JSON file.")
    parser.add_argument("--model-path", default=os.environ.get("MODEL_PATH", "artifacts/models/price_model.joblib"))
    parser.add_argument("--input-json", type=Path, required=True)
    args = parser.parse_args()

    model = RealEstatePriceModel.load(args.model_path)
    payload = json.loads(args.input_json.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        if len(payload) != 1:
            parser.error("Single prediction requires exactly one record; use /predict/batch for batches.")
        payload = payload[0]
    if not isinstance(payload, dict):
        parser.error("Prediction input must be a JSON object or a one-record list.")
    prediction = model.predict(payload)
    print(json.dumps(prediction, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
