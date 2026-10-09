from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from marketing_production.main import app


def _campaign(asset_root: Path) -> dict:
    return {
        "campaign_id": "api-01",
        "name": "API campaign",
        "objective": "conversion",
        "channels": ["email"],
        "audience": {"name": "members", "rules": [{"field": "language", "operator": "equals", "value": "en"}]},
        "assets": [{"asset_id": "copy", "kind": "copy", "channel": "email", "uri": "inline:copy", "text": "Hello"}],
        "asset_root": str(asset_root),
    }


def test_health_and_campaign_contract(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_PRODUCTION_OUTPUT_ROOT", str(tmp_path / "out"))
    client = TestClient(app)

    health = client.get("/health")
    response = client.post("/v1/marketing/campaigns", json={"idempotency_key": "api-key", "campaign": _campaign(tmp_path)})

    assert health.status_code == 200
    assert health.json()["model_calls_supported"] is False
    assert health.json()["publish_supported"] is False
    assert response.status_code == 201
    body = response.json()
    assert body["package"]["filename"].endswith(".campaign.json")
    assert "tmp_path" not in json.dumps(body)


def test_api_maps_conflict_and_invalid_request(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MARKETING_PRODUCTION_OUTPUT_ROOT", str(tmp_path / "out"))
    client = TestClient(app)
    campaign = _campaign(tmp_path)
    first = client.post("/v1/marketing/campaigns", json={"idempotency_key": "api-key", "campaign": campaign})
    changed = {**campaign, "name": "changed"}
    conflict = client.post("/v1/marketing/campaigns", json={"idempotency_key": "api-key", "campaign": changed})
    invalid = client.post("/v1/marketing/campaigns", json={"idempotency_key": "bad", "campaign": {"campaign_id": "x"}})

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert invalid.status_code == 422
