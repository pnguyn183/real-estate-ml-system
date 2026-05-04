from __future__ import annotations

import argparse
import json
from pathlib import Path

from price_model import RealEstatePriceModel


def main() -> None:
    parser = argparse.ArgumentParser(description="Predict a property price from a feature JSON file.")
    parser.add_argument("--model-path", default="artifacts/models/price_model.joblib")
    parser.add_argument("--input-json", type=Path, required=True)
    args = parser.parse_args()

    model = RealEstatePriceModel.load(args.model_path)
    payload = json.loads(args.input_json.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        payload = payload[0]
    prediction = model.predict(payload)
    print(json.dumps(prediction, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
