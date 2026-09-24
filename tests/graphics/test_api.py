from pathlib import Path

from fastapi.testclient import TestClient

from book_production_platform.graphics_production.main import app


def payload() -> dict[str, object]:
    return {
        "idempotency_key": "api-1",
        "items": [{
            "request_id": "announcement-1",
            "template_id": "announcement",
            "variant": "square",
            "canvas": {"width": 1080, "height": 1080},
            "brand": {"primary": "#14213D", "accent": "#FCA311", "foreground": "#111111"},
            "content": {"headline": "ข่าวสาร", "body": "รายละเอียด", "cta": "ดูเพิ่มเติม"},
        }],
    }


def test_health_exposes_graphics_contract() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["contract_version"] == "graphics.v1a"
    assert response.json()["model_calls_supported"] is False


def test_create_batch_returns_artifact_manifest(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("GRAPHICS_PRODUCTION_OUTPUT_ROOT", str(tmp_path))  # type: ignore[attr-defined]
    client = TestClient(app)

    response = client.post("/v1/graphics/batches", json=payload())

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "accepted"
    assert body["model_calls"] == 0
