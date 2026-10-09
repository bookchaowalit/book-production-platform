from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

from video_postproduction.edl import validate_edl
from video_postproduction.jobs.queue import InlineJobQueue
from video_postproduction.media.ffprobe import FFprobeMetadataReader
from video_postproduction.media.models import MediaMetadata, VideoStreamMetadata
from video_postproduction.models import EDL, MediaAsset, Project, RenderOutput
from video_postproduction.render.base import RenderArtifact
from video_postproduction.render.ffmpeg import FFmpegRenderEngine
from video_postproduction.render.service import RenderService
from video_postproduction.storage.local import LocalFilesystemStorage


@dataclass
class FakeRenderEngine:
    calls: int = 0

    def render(self, edl, *, input_path: Path, output_path: Path, caption_path: Path | None = None):
        self.calls += 1
        output_path.write_bytes(b"rendered-mp4")
        return RenderArtifact(
            duration=2,
            metadata=MediaMetadata(
                duration=2,
                format_name="mov,mp4",
                size_bytes=12,
                bitrate=None,
                video=VideoStreamMetadata(codec_name="h264", width=1080, height=1920, fps=30),
            ),
        )


def make_edl_document(project_id: UUID, asset_id: UUID) -> dict[str, object]:
    return {
        "version": 1,
        "project_id": str(project_id),
        "source_assets": [{"asset_id": str(asset_id), "storage_key": "source.mp4", "duration": 2}],
        "timeline": [
            {
                "id": "segment_0001",
                "source": {"asset_id": str(asset_id), "start": 0, "end": 2},
                "visual": {"kind": "a_roll", "reframe": {"mode": "center", "aspect_ratio": "9:16"}, "asset_refs": []},
                "captions": {"enabled": True, "source": "transcript", "format": "ass", "preset": "minimal"},
                "audio": {"operations": []},
                "color": {"operations": []},
                "provenance": {},
            }
        ],
        "assets": [],
        "output": {"width": 1080, "height": 1920, "fps": 30, "format": "mp4"},
        "capabilities": {"required": ["cut", "captions", "center_reframe"]},
    }


def test_render_service_stores_output_and_persists_record(session, tmp_path):
    project = Project(name="demo", status="ready_for_edit")
    session.add(project)
    session.flush()
    storage = LocalFilesystemStorage(tmp_path / "storage")
    storage.put_atomic("projects/demo/source.mp4", b"source", "video/mp4")
    asset = MediaAsset(
        project_id=project.id,
        storage_key="projects/demo/source.mp4",
        original_filename="source.mp4",
        media_type="video/mp4",
        size_bytes=6,
        sha256="c" * 64,
        metadata_json={},
        status="transcribed",
    )
    session.add(asset)
    session.flush()
    raw_document = make_edl_document(project.id, asset.id)
    raw_document["timeline"][0]["captions"]["enabled"] = False
    document = validate_edl(raw_document).model_dump(mode="json")
    edl = EDL(project_id=project.id, source_asset_id=asset.id, version=1, schema_version="edl.v1", document=document)
    session.add(edl)
    session.commit()

    output = RenderService(session, storage=storage, engine=FakeRenderEngine()).render(project.id, edl.id)

    assert output.status == "succeeded"
    assert output.storage_key is not None
    assert storage.local_path(output.storage_key).read_bytes() == b"rendered-mp4"
    assert session.get(RenderOutput, output.id).status == "succeeded"


def test_run_job_does_not_render_the_same_output_twice(session, tmp_path):
    project = Project(name="idempotent render", status="ready_for_edit")
    session.add(project)
    session.flush()
    storage = LocalFilesystemStorage(tmp_path / "storage")
    storage.put_atomic("projects/demo/source.mp4", b"source", "video/mp4")
    asset = MediaAsset(
        project_id=project.id,
        storage_key="projects/demo/source.mp4",
        original_filename="source.mp4",
        media_type="video/mp4",
        size_bytes=6,
        sha256="e" * 64,
        metadata_json={},
        status="transcribed",
    )
    session.add(asset)
    session.flush()
    raw_document = make_edl_document(project.id, asset.id)
    raw_document["timeline"][0]["captions"]["enabled"] = False
    document = validate_edl(raw_document).model_dump(mode="json")
    edl = EDL(project_id=project.id, source_asset_id=asset.id, version=1, schema_version="edl.v1", document=document)
    session.add(edl)
    session.commit()

    engine = FakeRenderEngine()
    service = RenderService(session, storage=storage, engine=engine)
    queued = service.enqueue(project.id, edl.id, InlineJobQueue())

    first = service.run_job(queued.job_id)
    second = service.run_job(queued.job_id)

    assert first.id == second.id == queued.id
    assert first.status == second.status == "succeeded"
    assert engine.calls == 1


def test_ffmpeg_engine_renders_real_fixture(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg and FFprobe are required for the real fixture check")

    source = tmp_path / "fixture.mp4"
    generated = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=44100",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        capture_output=True,
        check=False,
        shell=False,
        text=True,
        timeout=60,
    )
    assert generated.returncode == 0, generated.stderr

    project_id = UUID("00000000-0000-0000-0000-000000000001")
    asset_id = UUID("00000000-0000-0000-0000-000000000002")
    document = make_edl_document(project_id, asset_id)
    document["source_assets"][0]["duration"] = 2.0
    document["timeline"][0]["source"]["end"] = 1.5
    edl = validate_edl(document)
    output = tmp_path / "rendered.mp4"
    artifact = FFmpegRenderEngine(
        ffmpeg,
        probe_reader=FFprobeMetadataReader(ffprobe),
        timeout_seconds=60,
    ).render(edl, input_path=source, output_path=output)

    assert output.is_file()
    assert 1.0 < artifact.duration <= 1.6
    assert artifact.metadata.format_name is not None
    assert "mp4" in artifact.metadata.format_name.split(",")
    assert artifact.metadata.video.width == 1080
    assert artifact.metadata.video.height == 1920
