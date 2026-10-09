from __future__ import annotations

import pytest

from video_postproduction.api.dependencies import get_session, get_settings
from video_postproduction.config import Settings
from video_postproduction.main import app


@pytest.fixture
def isolated_api(session):
    app.dependency_overrides[get_session] = lambda: session
    try:
        yield
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_create_project_returns_project_id(client, isolated_api):
    response = await client.post(
        "/api/v1/projects",
        json={"name": "Demo", "instructions": "Thai subtitles"},
    )

    assert response.status_code == 201
    assert response.json()["id"]
    assert response.json()["status"] == "created"
    assert response.json()["transcription_quality"] == "fast"


@pytest.mark.asyncio
async def test_project_creation_accepts_accurate_transcription_quality(client, isolated_api):
    response = await client.post(
        "/api/v1/projects",
        json={"name": "Careful demo", "transcription_quality": "accurate"},
    )

    assert response.status_code == 201
    assert response.json()["transcription_quality"] == "accurate"


@pytest.mark.asyncio
async def test_project_creation_rejects_unknown_transcription_quality(client, isolated_api):
    response = await client.post(
        "/api/v1/projects",
        json={"name": "Invalid demo", "transcription_quality": "best"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_ac_vid_01c_local_whisper_reports_project_quality_support(client, isolated_api):
    response = await client.get("/api/v1/transcription/capabilities")

    assert response.status_code == 200
    assert response.json() == {"supports_project_quality": True}


@pytest.mark.asyncio
async def test_ac_vid_01c_nonlocal_provider_hides_and_rejects_quality_override(
    client, isolated_api
):
    app.dependency_overrides[get_settings] = lambda: Settings(
        transcription_provider="openai_compatible",
        openai_compatible_base_url="https://gateway.test/v1",
    )

    capabilities = await client.get("/api/v1/transcription/capabilities")
    explicit_fast = await client.post(
        "/api/v1/projects",
        json={"name": "Explicit fast", "transcription_quality": "fast"},
    )
    explicit_accurate = await client.post(
        "/api/v1/projects",
        json={"name": "Explicit accurate", "transcription_quality": "accurate"},
    )
    provider_default = await client.post(
        "/api/v1/projects",
        json={"name": "Provider default"},
    )

    assert capabilities.status_code == 200
    assert capabilities.json() == {"supports_project_quality": False}
    assert explicit_fast.status_code == 422
    assert explicit_accurate.status_code == 422
    assert provider_default.status_code == 201
    assert provider_default.json()["transcription_quality"] == "fast"


@pytest.mark.asyncio
async def test_projects_can_be_listed_and_fetched(client, isolated_api):
    created = await client.post("/api/v1/projects", json={"name": "Demo"})
    project_id = created.json()["id"]

    listing = await client.get("/api/v1/projects")
    fetched = await client.get(f"/api/v1/projects/{project_id}")

    assert listing.status_code == 200
    assert listing.json()["items"][0]["id"] == project_id
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Demo"
    assert fetched.json()["transcription_quality"] == "fast"


@pytest.mark.asyncio
async def test_configured_api_token_protects_project_creation(client, isolated_api):
    app.dependency_overrides[get_settings] = lambda: Settings(video_editor_api_token="local-secret")

    unauthorized = await client.post("/api/v1/projects", json={"name": "Demo"})
    authorized = await client.post(
        "/api/v1/projects",
        json={"name": "Demo"},
        headers={"Authorization": "Bearer local-secret"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 201


@pytest.mark.asyncio
async def test_empty_api_token_allows_local_project_creation(client, isolated_api):
    app.dependency_overrides[get_settings] = lambda: Settings(video_editor_api_token="")

    response = await client.post("/api/v1/projects", json={"name": "Local demo"})

    assert response.status_code == 201
