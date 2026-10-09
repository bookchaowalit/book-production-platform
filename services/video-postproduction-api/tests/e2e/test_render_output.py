from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from video_postproduction.media.ffprobe import FFprobeMetadataReader
from video_postproduction.render.ffmpeg import FFmpegRenderEngine
from video_postproduction.render.service import RenderService

from ..fixtures.generate_sample_video import generate_sample_video


@pytest.mark.asyncio
async def test_explicit_edl_range_renders_captioned_vertical_mp4(client, e2e_runtime, tmp_path: Path):
    fixture = generate_sample_video(tmp_path / "sample.mp4", e2e_runtime.settings.ffmpeg_executable)
    project_response = await client.post("/api/v1/projects", json={"name": "E2E render"})
    project_id = project_response.json()["id"]
    upload = await client.post(
        f"/api/v1/projects/{project_id}/assets",
        files={"file": (fixture.name, fixture.read_bytes(), "video/mp4")},
    )
    assert upload.status_code == 202

    current_edl = await client.get(f"/api/v1/projects/{project_id}/edl")
    assert current_edl.status_code == 200, current_edl.json()
    document = current_edl.json()["document"]
    document["timeline"][0]["source"]["end"] = 1.5
    document["timeline"][0]["captions"]["preset"] = "bold"
    updated_edl = await client.patch(f"/api/v1/projects/{project_id}/edl", json=document)
    assert updated_edl.status_code == 200
    assert updated_edl.json()["version"] == 2

    # Let the API persist the render job, then execute the worker boundary with
    # the real FFmpeg/FFprobe engine in this isolated test process.
    e2e_runtime.queue.runner = None
    queued = await client.post(
        f"/api/v1/projects/{project_id}/renders",
        json={"edl_id": updated_edl.json()["id"]},
    )
    assert queued.status_code == 202
    output_id = UUID(queued.json()["id"])
    job_id = UUID(queued.json()["job_id"])
    render_service = RenderService(
        e2e_runtime.session,
        storage=e2e_runtime.storage,
        engine=FFmpegRenderEngine(
            e2e_runtime.settings.ffmpeg_executable,
            probe_reader=FFprobeMetadataReader(e2e_runtime.settings.ffprobe_executable),
            timeout_seconds=120,
        ),
    )
    result = render_service.run_job(job_id)
    assert result.id == output_id
    assert result.status == "succeeded"

    status = await client.get(f"/api/v1/renders/{output_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "succeeded"
    metadata = status.json()["metadata"]
    assert metadata["video"]["width"] == 1080
    assert metadata["video"]["height"] == 1920
    assert 1.0 < metadata["duration"] <= 1.7

    preview = await client.get(f"/api/v1/renders/{output_id}/stream")
    download = await client.get(f"/api/v1/renders/{output_id}/download")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("video/mp4")
    assert len(preview.content) > 0
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
