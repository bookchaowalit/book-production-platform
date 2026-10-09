from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_reports_service_contract(client):
    assert client is not None, "video_postproduction.main.app is not implemented"
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "video-postproduction-api"


@pytest.mark.asyncio
async def test_health_allows_the_local_web_origin(client):
    response = await client.get("/healthz", headers={"Origin": "http://127.0.0.1:3400"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3400"
