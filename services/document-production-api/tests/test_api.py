import hashlib

import pytest
from document_ir import DocumentRequest
from document_production.job_store import JobStore
from document_production.jobs import _hash_json
from document_production.main import app
from fastapi.testclient import TestClient


def sample_request():
    return {
        "template_id": "invoice",
        "template_version": "1",
        "document_id": "api-001",
        "currency": "THB",
        "title": "Invoice",
        "customer_name": "API customer",
        "issue_date": "2026-09-22",
        "line_items": [{"description": "Service", "quantity": "1", "unit_price": "100", "tax_rate": "7"}],
    }


def test_health_declares_zero_model_v1a(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["model_calls_supported"] is False


def test_post_job_returns_artifact_manifest(client, tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_PRODUCTION_OUTPUT_ROOT", str(tmp_path))
    response = client.post("/v1/documents/jobs", json={"idempotency_key": "api-001", **sample_request()})
    assert response.status_code == 201
    assert response.json()["model_calls"] == 0
    assert response.json()["manifest_path"].startswith("manifest.json")
    assert str(tmp_path) not in response.text


def test_post_job_returns_structured_validation_error(client):
    payload = {"idempotency_key": "api-invalid", **sample_request(), "line_items": []}
    response = client.post("/v1/documents/jobs", json=payload)
    assert response.status_code == 422
    assert "line_items_required" in response.text


def test_idempotency_conflict_returns_409(client, tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_PRODUCTION_OUTPUT_ROOT", str(tmp_path))
    first = client.post("/v1/documents/jobs", json={"idempotency_key": "api-conflict", **sample_request()})
    assert first.status_code == 201
    changed = {**sample_request(), "title": "Changed"}
    second = client.post("/v1/documents/jobs", json={"idempotency_key": "api-conflict", **changed})
    assert second.status_code == 409
    assert "idempotency" in second.text


def test_running_job_returns_pending_202(client, tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_PRODUCTION_OUTPUT_ROOT", str(tmp_path))
    request = sample_request()
    parsed = DocumentRequest.model_validate(request)
    JobStore(tmp_path).claim(
        key_hash=hashlib.sha256(b"api-running").hexdigest(),
        request_hash=_hash_json(parsed.model_dump(mode="json")),
        job_id="running-job",
        recipe_version="invoice-v1",
        schema_version=parsed.schema_version,
        engine_version="document-engine.v1",
    )
    response = client.post("/v1/documents/jobs", json={"idempotency_key": "api-running", **request})
    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert response.headers["retry-after"] == "1"


def test_engine_failure_returns_server_error(client, tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_PRODUCTION_OUTPUT_ROOT", str(tmp_path))

    def fail_renderer(*args, **kwargs):
        raise RuntimeError("renderer unavailable")

    monkeypatch.setattr("document_production.jobs.render_document", fail_renderer)
    response = client.post("/v1/documents/jobs", json={"idempotency_key": "api-engine-failure", **sample_request()})
    assert response.status_code == 500
    assert "RuntimeError" in response.text
    assert str(tmp_path) not in response.text


def test_post_brief_returns_artifact_without_raw_brief(client, tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_PRODUCTION_OUTPUT_ROOT", str(tmp_path))
    brief = "title: API brief\ncustomer: Acme\ndate: 2026-09-22\ncurrency: USD\nitem: Service | 1 | 10"
    response = client.post(
        "/v1/documents/briefs",
        json={"idempotency_key": "api-brief", "document_id": "api-brief", "locale": "en-US", "brief": brief},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "accepted"
    assert response.json()["model_calls"] == 0
    assert brief not in response.text


def test_post_brief_returns_needs_input_422(client, tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_PRODUCTION_OUTPUT_ROOT", str(tmp_path))
    response = client.post(
        "/v1/documents/briefs",
        json={"idempotency_key": "api-brief-missing", "document_id": "api-brief-missing", "brief": "title: Missing"},
    )

    assert response.status_code == 422
    assert response.json()["status"] == "needs_input"
    assert "customer_name" in response.json()["metadata"]["extraction"]["missing"]


@pytest.fixture
def client():
    return TestClient(app)
