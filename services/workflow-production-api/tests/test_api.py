from __future__ import annotations

from fastapi.testclient import TestClient
from workflow_production.main import app


def _workflow() -> dict:
    return {
        "workflow_id": "api-01",
        "name": "Onboarding",
        "fields": [{"field_id": "customer_name", "label": "Customer", "field_type": "text", "required": True}],
        "form_values": {"customer_name": "Ada"},
        "steps": [{"step_id": "collect", "kind": "collect_form"}, {"step_id": "render", "kind": "render_document", "depends_on": ["collect"]}],
        "document": {"title": "Brief", "sections": [{"kind": "field", "field_id": "customer_name"}]},
        "permissions": {"allowed_roles": ["ops"]},
    }


def test_health_and_workflow_contract(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WORKFLOW_PRODUCTION_OUTPUT_ROOT", str(tmp_path / "out"))
    client = TestClient(app)

    health = client.get("/health")
    response = client.post("/v1/workflows/plans", json={"idempotency_key": "api-key", "workflow": _workflow()})

    assert health.status_code == 200
    assert health.json()["model_calls_supported"] is False
    assert health.json()["external_effects_supported"] is False
    assert response.status_code == 201
    assert {item["media_type"] for item in response.json()["artifacts"]} == {"application/json", "text/markdown", "application/xml"}


def test_api_maps_conflict_and_invalid_workflow(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WORKFLOW_PRODUCTION_OUTPUT_ROOT", str(tmp_path / "out"))
    client = TestClient(app)
    workflow = _workflow()
    first = client.post("/v1/workflows/plans", json={"idempotency_key": "api-key", "workflow": workflow})
    conflict = client.post("/v1/workflows/plans", json={"idempotency_key": "api-key", "workflow": {**workflow, "name": "changed"}})
    invalid = client.post("/v1/workflows/plans", json={"idempotency_key": "bad", "workflow": {"workflow_id": "x"}})

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert invalid.status_code == 422
