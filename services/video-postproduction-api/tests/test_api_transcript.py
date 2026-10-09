from __future__ import annotations

import pytest

from video_postproduction.api.dependencies import get_session
from video_postproduction.main import app


@pytest.fixture
def isolated_api(session):
    app.dependency_overrides[get_session] = lambda: session
    try:
        yield
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_transcript_not_ready_is_explicit(client, isolated_api):
    created = await client.post("/api/v1/projects", json={"name": "Demo"})
    project_id = created.json()["id"]

    response = await client.get(f"/api/v1/projects/{project_id}/transcript")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "transcript_not_ready"
