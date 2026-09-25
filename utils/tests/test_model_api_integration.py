"""Exercise the HTTP contract using an actually fitted, isolated model artifact."""
import json
import os
from pathlib import Path
import subprocess
import sys

import joblib
import pytest
from fastapi.testclient import TestClient

from modeling import api
from modeling.auth import AuthService, FileUserStore
from modeling.price_model import RealEstatePriceModel, publish_model

ROOT = Path(__file__).resolve().parents[2]
PROPERTY = {"area_m2": 80, "bedroom_count": 2, "bathroom_count": 1,
            "property_type": "apartment", "listing_type": "sell",
            "province_slug": "ha-noi", "district_slug": "dong-da"}


@pytest.fixture(scope="module")
def fitted_artifact(tmp_path_factory):
    directory = tmp_path_factory.mktemp("fitted-api-model")
    records = [dict(PROPERTY, url=f"https://example.invalid/property/{index}",
                    area_m2=60 + index, text_features="apartment redbook",
                    price_vnd=2_000_000_000 + index * 20_000_000,
                    is_model_candidate=True) for index in range(250)]
    path = directory / "models" / "price_model.joblib"
    RealEstatePriceModel().train(records, str(path))
    (directory / "records.json").write_text(json.dumps(records), encoding="utf-8")
    return path


@pytest.fixture
def api_client(tmp_path, monkeypatch, fitted_artifact):
    path = tmp_path / "price_model.joblib"
    publish_model(fitted_artifact, path)
    service = AuthService(FileUserStore(tmp_path / "users.json"), "isolated-test-secret-at-least-32-characters", 5)
    admin = service.register("admin@example.invalid", "StrongPass1")
    user = service.register("user@example.invalid", "StrongPass1")
    monkeypatch.setattr(api, "MODEL_PATH", path)
    monkeypatch.setattr(api, "_model", None)
    monkeypatch.setattr(api, "_model_mtime", None)
    monkeypatch.setattr(api, "_model_metadata", None)
    monkeypatch.setattr(api, "_auth_service", service)
    monkeypatch.setattr(api, "_login_attempts", {})
    with TestClient(api.app) as client:
        client.admin_headers = {"Authorization": f"Bearer {service.create_access_token(admin)}"}
        client.user_headers = {"Authorization": f"Bearer {service.create_access_token(user)}"}
        yield client


def test_real_model_http_predictions_and_roles(api_client):
    assert api_client.get("/ready").status_code == 200
    assert api_client.post("/predict", json=PROPERTY).status_code == 401
    response = api_client.post("/predict", json=PROPERTY, headers=api_client.user_headers)
    assert response.status_code == 200
    expected = RealEstatePriceModel.load(str(api.MODEL_PATH)).predict(PROPERTY)
    assert response.json()["predicted_price_vnd"] == pytest.approx(expected["predicted_price_vnd"])
    assert api_client.get("/model/info", headers=api_client.user_headers).status_code == 403
    metadata = api_client.get("/model/info", headers=api_client.admin_headers).json()
    assert metadata["training_sample_count"] == 250
    assert metadata["version"]
    assert api_client.post("/predict/batch", json={"properties": [PROPERTY]}, headers=api_client.user_headers).status_code == 403


@pytest.mark.parametrize("include_confidence", [False, True])
def test_batch_option_and_original_positions(api_client, include_confidence):
    response = api_client.post("/predict/batch", json={"properties": [PROPERTY, dict(PROPERTY, area_m2=100)],
                                                      "include_confidence": include_confidence}, headers=api_client.admin_headers)
    assert response.status_code == 200
    result = response.json()
    assert result["successful_count"] == 2 and result["failed_count"] == 0
    assert result["failures"] == []
    assert [item["input_index"] for item in result["predictions"]] == [0, 1]
    assert all((item["confidence_low_vnd"] is not None) == include_confidence for item in result["predictions"])


def test_batch_failure_retains_index_and_other_predictions(api_client, monkeypatch):
    model = api.get_model()
    original_predict = model.predict

    def fail_one(record):
        if record["area_m2"] == 999:
            raise ValueError("private internal detail")
        return original_predict(record)

    monkeypatch.setattr(model, "predict", fail_one)
    response = api_client.post("/predict/batch", json={"properties": [PROPERTY, dict(PROPERTY, area_m2=999), PROPERTY]},
                               headers=api_client.admin_headers)
    result = response.json()
    assert response.status_code == 200
    assert [item["input_index"] for item in result["predictions"]] == [0, 2]
    assert result["failed_count"] == 1 and result["successful_count"] == 2
    assert result["failures"] == [{"input_index": 1, "error": "Prediction failed"}]
    assert "private internal detail" not in response.text


@pytest.mark.parametrize("artifact_state", ["missing", "corrupt"])
def test_unready_model_preserves_liveness(api_client, artifact_state):
    if artifact_state == "missing":
        api.MODEL_PATH.unlink()
    else:
        api.MODEL_PATH.write_bytes(b"invalid artifact")
    assert api_client.get("/health").status_code == 200
    assert api_client.get("/health").json()["model_metadata"] is None
    assert api_client.get("/ready").status_code == 503


def test_metadata_tracks_loaded_artifact_not_unrelated_latest_file(api_client, tmp_path):
    loaded = api.get_model()
    (tmp_path / "metadata_v99999999.json").write_text('{"version":"unrelated","sample_count":9999}', encoding="utf-8")
    replacement = tmp_path / "replacement.joblib"
    joblib.dump({"model": loaded.model, "metadata": {"version": "replacement", "sample_count": 17}}, replacement)
    publish_model(replacement, api.MODEL_PATH)
    assert api_client.get("/model/info", headers=api_client.admin_headers).json()["version"] == "replacement"
    joblib.dump(loaded.model, replacement)
    publish_model(replacement, api.MODEL_PATH)
    info = api_client.get("/model/info", headers=api_client.admin_headers).json()
    assert info["version"] is None and info["training_sample_count"] is None


def test_http_login_and_invalid_inputs(api_client):
    response = api_client.post("/auth/login", json={"email": "admin@example.invalid", "password": "StrongPass1"})
    assert response.status_code == 200
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    assert api_client.get("/auth/me", headers=headers).json()["role"] == "admin"
    assert api_client.post("/predict", json=dict(PROPERTY, area_m2=-1), headers=headers).status_code == 422
    assert api_client.post("/predict", json=dict(PROPERTY, area_m2="Infinity"), headers=headers).status_code == 422
    assert api_client.post("/predict/batch", json={"properties": []}, headers=headers).status_code == 422


def test_failed_publish_preserves_previous_live_artifact(tmp_path, monkeypatch):
    import modeling.price_model as module

    source, live = tmp_path / "new.joblib", tmp_path / "live.joblib"
    source.write_bytes(b"new complete model")
    live.write_bytes(b"old complete model")

    def fail_copy(original, temporary):
        temporary.write(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(module.shutil, "copyfileobj", fail_copy)
    with pytest.raises(OSError, match="disk full"):
        publish_model(source, live)
    assert live.read_bytes() == b"old complete model"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("entry", [
    ["modeling/predict_price.py"], ["-m", "modeling.predict_price"],
    ["processing/export_training_dataset.py"], ["-m", "processing.export_training_dataset"],
    ["modeling/train_model.py"], ["-m", "modeling.train_model"],
])
def test_documented_cli_entrypoints(entry):
    result = subprocess.run([sys.executable, *entry, "--help"], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_prediction_cli_uses_real_artifact_and_configured_path(tmp_path, fitted_artifact):
    inputs = tmp_path / "property.json"
    inputs.write_text(json.dumps(PROPERTY), encoding="utf-8")
    result = subprocess.run([sys.executable, "modeling/predict_price.py", "--input-json", str(inputs)],
                            cwd=ROOT, env=dict(os.environ, MODEL_PATH=str(fitted_artifact)),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    expected = RealEstatePriceModel.load(str(fitted_artifact)).predict(PROPERTY)
    assert json.loads(result.stdout)["predicted_price_vnd"] == pytest.approx(expected["predicted_price_vnd"])


def test_training_cli_publishes_to_configured_serving_path(tmp_path, fitted_artifact):
    path = tmp_path / "models" / "price_model.joblib"
    metrics = tmp_path / "metrics.json"
    environment = dict(os.environ, MODEL_PATH=str(path), METRICS_PATH=str(metrics),
                       PROMETHEUS_METRICS_PORT="0", MIN_RECORDS_FOR_TRAINING="200")
    result = subprocess.run([sys.executable, "modeling/train_model.py", "--input-json",
                             str(fitted_artifact.parent.parent / "records.json")], cwd=ROOT,
                            env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert metrics.exists() and path.exists()
    assert RealEstatePriceModel.load(str(path)).predict(PROPERTY)["predicted_price_vnd"] > 0


def test_legacy_http_readiness_loads_artifact(tmp_path, monkeypatch, fitted_artifact):
    from http.server import ThreadingHTTPServer
    import threading
    import requests
    from modeling import predict_service

    path = tmp_path / "legacy.joblib"
    monkeypatch.setattr(predict_service, "MODEL_PATH", path)
    monkeypatch.setattr(predict_service, "_model", None)
    monkeypatch.setattr(predict_service, "_model_mtime", None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), predict_service.PredictorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        assert requests.get(endpoint + "/health", timeout=5).status_code == 200
        assert requests.get(endpoint + "/ready", timeout=5).status_code == 503
        path.write_bytes(b"invalid artifact")
        assert requests.get(endpoint + "/ready", timeout=5).status_code == 503
        publish_model(fitted_artifact, path)
        assert requests.get(endpoint + "/ready", timeout=5).status_code == 200
        prediction = requests.post(endpoint + "/predict", json=PROPERTY, timeout=5)
        assert prediction.status_code == 200 and prediction.json()["predictions"][0]["predicted_price_vnd"] > 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
