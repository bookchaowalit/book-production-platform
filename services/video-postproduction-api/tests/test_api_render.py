from __future__ import annotations

from uuid import UUID

import pytest

from video_postproduction.api.dependencies import (
    get_job_queue,
    get_render_engine,
    get_session,
    get_storage,
)
from video_postproduction.jobs.queue import InlineJobQueue
from video_postproduction.main import app
from video_postproduction.models import EDL, MediaAsset, Project, RenderOutput
from video_postproduction.storage.local import LocalFilesystemStorage

from .conftest import valid_initial_edl


class NoopRenderEngine:
    def render(self, edl, *, input_path, output_path, caption_path=None):
        raise AssertionError("the API enqueue test must not render inline")


@pytest.fixture
def isolated_render_api(session, tmp_path):
    storage = LocalFilesystemStorage(tmp_path / "storage")
    project = Project(name="Render demo", status="ready_for_edit")
    session.add(project)
    session.flush()
    asset = MediaAsset(
        project_id=project.id,
        storage_key="projects/render/source.mp4",
        original_filename="source.mp4",
        media_type="video/mp4",
        size_bytes=10,
        sha256="d" * 64,
        metadata_json={"duration": 30.0},
        status="transcribed",
    )
    session.add(asset)
    session.flush()
    document = valid_initial_edl(project.id)
    document["source_assets"][0]["asset_id"] = str(asset.id)
    document["source_assets"][0]["duration"] = 30.0
    document["timeline"][0]["source"]["asset_id"] = str(asset.id)
    session.add(
        EDL(
            project_id=project.id,
            source_asset_id=asset.id,
            version=1,
            schema_version="edl.v1",
            document=document,
            status="draft",
        )
    )
    session.commit()
    app.dependency_overrides.update(
        {
            get_session: lambda: session,
            get_storage: lambda: storage,
            get_job_queue: lambda: InlineJobQueue(),
            get_render_engine: lambda: NoopRenderEngine(),
        }
    )
    try:
        yield project, asset, storage, session
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_edl_update_enqueue_and_render_output_routes(client, isolated_render_api):
    project, _asset, storage, session = isolated_render_api
    current = await client.get(f"/api/v1/projects/{project.id}/edl")
    document = current.json()["document"]
    document["timeline"][0]["source"]["end"] = 8.0

    updated = await client.patch(f"/api/v1/projects/{project.id}/edl", json=document)
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["document"]["timeline"][0]["source"]["end"] == 8.0

    queued = await client.post(
        f"/api/v1/projects/{project.id}/renders",
        json={"edl_id": updated.json()["id"]},
    )
    assert queued.status_code == 202
    output_id = queued.json()["id"]
    status = await client.get(f"/api/v1/renders/{output_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "queued"

    stored = storage.put_atomic("projects/render/renders/output.mp4", b"rendered", "video/mp4")
    database_output = session.get(RenderOutput, UUID(output_id))
    assert database_output is not None
    database_output.status = "succeeded"
    database_output.storage_key = stored.storage_key
    session.commit()

    preview = await client.get(f"/api/v1/renders/{output_id}/stream")
    download = await client.get(f"/api/v1/renders/{output_id}/download")
    assert preview.status_code == 200
    assert preview.content == b"rendered"
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
