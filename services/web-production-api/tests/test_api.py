from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from web_production.main import app


def _page(tmp_path: Path) -> dict:
    return {"page_id": "api-01", "slug": "api-page", "title": "API page", "description": "desc", "hero_heading": "Hello", "hero_body": "Body", "hero_cta": {"label": "Start", "href": "#signup"}, "features": [{"title": "One", "body": "First"}]}


def test_health_and_page_contract(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WEB_PRODUCTION_OUTPUT_ROOT", str(tmp_path / "out"))
    client = TestClient(app)

    health = client.get("/health")
    response = client.post("/v1/web/pages", json={"idempotency_key": "api-key", "page": _page(tmp_path)})

    assert health.status_code == 200
    assert health.json()["model_calls_supported"] is False
    assert response.status_code == 201
    assert {item["media_type"] for item in response.json()["artifacts"]} == {"text/html", "text/css"}


def test_api_maps_conflict_and_invalid_page(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WEB_PRODUCTION_OUTPUT_ROOT", str(tmp_path / "out"))
    client = TestClient(app)
    page = _page(tmp_path)
    first = client.post("/v1/web/pages", json={"idempotency_key": "api-key", "page": page})
    conflict = client.post("/v1/web/pages", json={"idempotency_key": "api-key", "page": {**page, "title": "changed"}})
    invalid = client.post("/v1/web/pages", json={"idempotency_key": "bad", "page": {"page_id": "x"}})

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert invalid.status_code == 422
