from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from book_production_platform.data_pipeline.main import app


def test_api_returns_a_plan_only_result_without_private_metadata(
    tmp_path: Path,
    monkeypatch,
    valid_pipeline_request: dict[str, Any],
) -> None:
    monkeypatch.setenv("DATA_PIPELINE_OUTPUT_ROOT", str(tmp_path))

    response = TestClient(app).post("/v1/data/pipelines", json={"pipeline": valid_pipeline_request})

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "accepted"
    assert result["model_calls"] == 0
    assert result["metadata"]["execution_mode"] == "plan_only"
    assert result["metadata"]["query_plan_verified"] is True
    assert result["artifact_names"] == ["parameters.json", "pipeline.sql", "qa-manifest.json", "report.json"]
    assert "BKK" not in response.text and "warehouse.orders" not in response.text


def test_api_rejects_oversized_raw_body_without_echoing_it(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_PIPELINE_OUTPUT_ROOT", str(tmp_path))
    body = b"x" * 262_145

    response = TestClient(app).post("/v1/data/pipelines", content=body)

    assert response.status_code == 413
    assert response.json() == {"detail": {"code": "pipeline_request_too_large"}}
    assert "x" * 100 not in response.text
    assert not (tmp_path / "jobs").exists()


def test_api_maps_schema_errors_to_a_stable_response(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_PIPELINE_OUTPUT_ROOT", str(tmp_path))

    response = TestClient(app).post(
        "/v1/data/pipelines",
        json={"pipeline": {"pipeline_id": "secret", "unknown": "private"}},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_pipeline_request"}}
    assert "secret" not in response.text and "private" not in response.text


def test_api_does_not_accept_a_client_idempotency_key(
    tmp_path: Path,
    monkeypatch,
    valid_pipeline_request: dict[str, Any],
) -> None:
    monkeypatch.setenv("DATA_PIPELINE_OUTPUT_ROOT", str(tmp_path))

    response = TestClient(app).post(
        "/v1/data/pipelines",
        json={"pipeline": valid_pipeline_request, "idempotency_key": "client-controlled"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": {"code": "invalid_pipeline_request"}}
    assert "client-controlled" not in response.text
    assert not (tmp_path / "jobs").exists()


def test_api_maps_semantic_validation_to_a_stable_response(
    tmp_path: Path,
    monkeypatch,
    valid_pipeline_request: dict[str, Any],
) -> None:
    monkeypatch.setenv("DATA_PIPELINE_OUTPUT_ROOT", str(tmp_path))
    invalid = json.loads(json.dumps(valid_pipeline_request))
    invalid["nodes"][0]["column"] = "missing"

    response = TestClient(app).post("/v1/data/pipelines", json={"pipeline": invalid})

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "unknown_pipeline_column"
    assert "BKK" not in response.text and "warehouse.orders" not in response.text


def test_api_replay_changed_input_and_tamper_return_stable_results(
    tmp_path: Path,
    monkeypatch,
    valid_pipeline_request: dict[str, Any],
) -> None:
    monkeypatch.setenv("DATA_PIPELINE_OUTPUT_ROOT", str(tmp_path))
    client = TestClient(app)
    first = client.post("/v1/data/pipelines", json={"pipeline": valid_pipeline_request})
    replay = client.post("/v1/data/pipelines", json={"pipeline": valid_pipeline_request})
    changed = json.loads(json.dumps(valid_pipeline_request))
    changed["nodes"][0]["value"] = "CNX"
    distinct = client.post("/v1/data/pipelines", json={"pipeline": changed})

    assert first.status_code == replay.status_code == distinct.status_code == 200
    assert first.json()["fingerprint"] == replay.json()["fingerprint"]
    assert replay.json()["replay"] is True
    assert distinct.json()["fingerprint"] != first.json()["fingerprint"]
    artifact = Path(first.json()["artifact_dir"]) / "parameters.json"
    artifact.write_text("{}", encoding="utf-8")
    tampered = client.post("/v1/data/pipelines", json={"pipeline": valid_pipeline_request})

    assert tampered.status_code == 409
    assert tampered.json() == {"detail": {"code": "replay_integrity_error"}}
