from __future__ import annotations

from pathlib import Path

import pytest

from ..fixtures.generate_sample_video import generate_sample_video


@pytest.mark.asyncio
async def test_upload_to_transcript_persists_metadata_and_edl(client, e2e_runtime, tmp_path: Path):
    fixture = generate_sample_video(tmp_path / "sample.mp4", e2e_runtime.settings.ffmpeg_executable)
    project_response = await client.post(
        "/api/v1/projects",
        json={"name": "E2E transcript", "instructions": "deterministic fixture"},
    )
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    upload = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": (fixture.name, fixture.read_bytes(), "video/mp4")},
    )
    assert upload.status_code == 202
    upload_body = upload.json()

    job = await client.get(upload_body["poll_url"])
    assert job.status_code == 200
    assert job.json()["status"] == "succeeded", job.json()

    asset = await client.get(f"/api/v1/projects/{project_id}/assets/{upload_body['asset_id']}")
    assert asset.status_code == 200
    assert asset.json()["status"] == "transcribed"
    assert asset.json()["metadata"]["duration"] > 0
    assert asset.json()["metadata"]["video"]["width"] == 320

    transcript = await client.get(f"/api/v1/projects/{project_id}/transcript")
    assert transcript.status_code == 200
    assert transcript.json()["normalized"]["segments"][0]["text"] == "hello solo empire"
    assert transcript.json()["normalized"]["segments"][0]["words"][0]["text"] == "hello"

    edl = await client.get(f"/api/v1/projects/{project_id}/edl")
    assert edl.status_code == 200
    assert edl.json()["document"]["output"]["render_profile"] == "vertical_centered"
    assert edl.json()["document"]["timeline"][0]["source"]["end"] <= 2.1
