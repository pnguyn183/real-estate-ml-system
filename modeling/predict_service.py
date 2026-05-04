from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    from modeling.price_model import RealEstatePriceModel
except ImportError:
    from price_model import RealEstatePriceModel


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

MODEL_PATH = Path(os.environ.get("MODEL_PATH", "artifacts/models/price_model.joblib"))
HOST = os.environ.get("PREDICTOR_HOST", "0.0.0.0")
PORT = int(os.environ.get("PREDICTOR_PORT", "8002"))

_model: RealEstatePriceModel | None = None
_model_mtime: float | None = None


def get_model() -> RealEstatePriceModel:
    global _model, _model_mtime
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Model file not found: {MODEL_PATH}")

    mtime = MODEL_PATH.stat().st_mtime
    if _model is None or _model_mtime != mtime:
        logger.info("Loading model from %s", MODEL_PATH)
        _model = RealEstatePriceModel.load(str(MODEL_PATH))
        _model_mtime = mtime
    return _model


class PredictorHandler(BaseHTTPRequestHandler):
    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            status = "ready" if MODEL_PATH.exists() else "waiting_for_model"
            self._write_json(200, {"status": status, "model_path": str(MODEL_PATH)})
            return
        self._write_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path != "/predict":
            self._write_json(404, {"error": "not_found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            records = payload if isinstance(payload, list) else [payload]
            model = get_model()
            predictions = [model.predict(record) for record in records]
            self._write_json(200, {"predictions": predictions})
        except FileNotFoundError as exc:
            self._write_json(503, {"error": str(exc)})
        except Exception as exc:
            logger.exception("Prediction failed")
            self._write_json(400, {"error": str(exc)})

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("%s - %s", self.client_address[0], format % args)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), PredictorHandler)
    logger.info("Predictor service listening on %s:%s", HOST, PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
