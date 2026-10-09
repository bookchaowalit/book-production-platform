from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from video_postproduction.api.dependencies import (
    get_job_queue,
    get_probe_reader,
    get_session,
    get_settings,
    get_storage,
)
from video_postproduction.config import Settings
from video_postproduction.jobs.queue import InlineJobQueue
from video_postproduction.main import app
from video_postproduction.media.models import MediaMetadata, VideoStreamMetadata
from video_postproduction.models import MediaAsset, Project, Transcript
from video_postproduction.storage.local import LocalFilesystemStorage


class FakeProbe:
    def read(self, path: Path) -> MediaMetadata:
        return MediaMetadata(
            duration=2,
            format_name="mp4",
            size_bytes=10,
            bitrate=None,
            video=VideoStreamMetadata(codec_name="h264", width=320, height=240, fps=30),
        )


async def _create_fast_transcript(client, session):
    project_response = await client.post("/api/v1/projects", json={"name": "Demo"})
    project_id = project_response.json()["id"]
    upload = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": ("sample.mp4", b"sample-video-bytes", "video/mp4")},
    )
    project = session.get(Project, UUID(project_id))
    asset = session.get(MediaAsset, UUID(upload.json()["asset_id"]))
    assert project is not None
    assert asset is not None
    asset.status = "transcribed"
    session.add(
        Transcript(
            project_id=project.id,
            asset_id=asset.id,
            provider="faster_whisper",
            model="small",
            language="th",
            duration=2,
            normalized_json={"segments": []},
            content_fingerprint="fast-transcript",
        )
    )
    session.commit()
    return project, asset


@pytest.fixture
def isolated_upload_api(session, tmp_path):
    storage = LocalFilesystemStorage(tmp_path / "storage")
    settings = Settings(storage_root=tmp_path / "storage", max_upload_bytes=1024)
    queue = InlineJobQueue()
    app.dependency_overrides.update(
        {
            get_session: lambda: session,
            get_storage: lambda: storage,
            get_settings: lambda: settings,
            get_probe_reader: lambda: FakeProbe(),
            get_job_queue: lambda: queue,
        }
    )
    try:
        yield queue
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_upload_returns_job_without_waiting_for_transcription(client, isolated_upload_api):
    project = await client.post("/api/v1/projects", json={"name": "Demo"})
    project_id = project.json()["id"]

    response = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": ("sample.mp4", b"sample-video-bytes", "video/mp4")},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["asset_id"]
    assert body["job_id"]
    assert body["poll_url"].startswith("/api/v1/jobs/")
    assert body["status"] == "queued"


@pytest.mark.asyncio
async def test_fast_transcript_can_be_retranscribed_accurately(client, isolated_upload_api, session):
    project, asset = await _create_fast_transcript(client, session)

    response = await client.post(
        f"/api/v1/projects/{project.id}/assets/{asset.id}/transcribe-accurately"
    )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["current_stage"] == "transcribe"
    assert project.transcription_quality == "accurate"
    assert isolated_upload_api.calls[-1][1] == "transcribe"


@pytest.mark.asyncio
async def test_accurate_retranscription_rejects_nonlocal_provider(
    client, isolated_upload_api, session
):
    project, asset = await _create_fast_transcript(client, session)
    isolated_upload_api.calls.clear()
    app.dependency_overrides[get_settings] = lambda: Settings(
        transcription_provider="openai_compatible",
        openai_compatible_base_url="https://gateway.test/v1",
    )

    response = await client.post(
        f"/api/v1/projects/{project.id}/assets/{asset.id}/transcribe-accurately"
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "transcription_quality_unsupported"
    assert isolated_upload_api.calls == []
    assert project.transcription_quality == "fast"


@pytest.mark.asyncio
async def test_accurate_retranscription_queue_failure_restores_project_state(
    client, isolated_upload_api, session
):
    project, asset = await _create_fast_transcript(client, session)
    project.status = "processing"
    session.commit()

    class FailingQueue:
        def enqueue(self, job_id: UUID, stage_name: str) -> None:
            raise RuntimeError("queue unavailable")

    app.dependency_overrides[get_job_queue] = lambda: FailingQueue()
    response = await client.post(
        f"/api/v1/projects/{project.id}/assets/{asset.id}/transcribe-accurately"
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "job_queue_unavailable"
    assert project.transcription_quality == "fast"
    assert project.status == "processing"


@pytest.mark.asyncio
async def test_project_assets_can_be_discovered_after_upload(client, isolated_upload_api):
    project = await client.post("/api/v1/projects", json={"name": "Demo"})
    project_id = project.json()["id"]

    upload = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": ("sample.mp4", b"sample-video-bytes", "video/mp4")},
    )

    response = await client.get(f"/api/v1/projects/{project_id}/assets")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == upload.json()["asset_id"]


@pytest.mark.asyncio
async def test_latest_project_job_can_be_discovered_after_upload(client, isolated_upload_api):
    project = await client.post("/api/v1/projects", json={"name": "Demo"})
    project_id = project.json()["id"]

    upload = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": ("sample.mp4", b"sample-video-bytes", "video/mp4")},
    )

    response = await client.get(f"/api/v1/projects/{project_id}/jobs/latest")

    assert response.status_code == 200
    assert response.json()["id"] == upload.json()["job_id"]


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_type_with_shared_error(client, isolated_upload_api):
    project = await client.post("/api/v1/projects", json={"name": "Demo"})
    project_id = project.json()["id"]

    response = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": ("payload.exe", b"not-video", "application/x-executable")},
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "asset_type_not_supported"


@pytest.mark.asyncio
async def test_streaming_resolves_server_side_storage_key(client, isolated_upload_api):
    project = await client.post("/api/v1/projects", json={"name": "Demo"})
    project_id = project.json()["id"]
    upload = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": ("sample.mp4", b"sample-video-bytes", "video/mp4")},
    )

    response = await client.get(
        f"/api/v1/projects/{project_id}/assets/{upload.json()['asset_id']}/stream"
    )

    assert response.status_code == 200
    assert response.content == b"sample-video-bytes"
