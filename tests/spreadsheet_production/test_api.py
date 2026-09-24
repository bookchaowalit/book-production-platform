from pathlib import Path

from fastapi.testclient import TestClient
from test_recipe import request

from book_production_platform.spreadsheet_production.main import app


def test_health_exposes_spreadsheet_contract() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json()["contract_version"] == "spreadsheet.v1a"
    assert response.json()["model_calls_supported"] is False


def test_create_workbook_returns_manifest(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("SPREADSHEET_PRODUCTION_OUTPUT_ROOT", str(tmp_path))  # type: ignore[attr-defined]
    response = TestClient(app).post("/v1/spreadsheets/workbooks", json={"idempotency_key": "api-1", "request": request()})

    assert response.status_code == 201
    assert response.json()["status"] == "accepted"
    assert response.json()["model_calls"] == 0
