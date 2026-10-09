from __future__ import annotations

from data_production.main import app
from fastapi.testclient import TestClient


def test_health_and_job_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_PRODUCTION_OUTPUT_ROOT", str(tmp_path))
    client = TestClient(app)
    assert client.get("/health").json()["contract_version"] == "data-v1a"
    response = client.post("/v1/data/jobs", json={"idempotency_key": "api-1", "request": {"dataset_id": "demo", "csv_text": "kind\nA\n"}})
    assert response.status_code == 201
    assert response.json()["model_calls"] == 0


def test_api_maps_idempotency_conflict(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_PRODUCTION_OUTPUT_ROOT", str(tmp_path))
    client = TestClient(app)
    payload = {"idempotency_key": "api-2", "request": {"dataset_id": "demo", "csv_text": "kind\nA\n"}}
    assert client.post("/v1/data/jobs", json=payload).status_code == 201
    payload["request"]["csv_text"] = "kind\nB\n"
    assert client.post("/v1/data/jobs", json=payload).status_code == 409
