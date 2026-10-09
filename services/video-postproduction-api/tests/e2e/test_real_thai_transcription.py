from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest
from sqlalchemy import select

from video_postproduction.config import Settings
from video_postproduction.jobs.queue import InlineJobQueue
from video_postproduction.jobs.stages import StageRunner
from video_postproduction.media.ffprobe import FFprobeMetadataReader
from video_postproduction.models import (
    EDL,
    MediaAsset,
    ProcessingJob,
    ProcessingStage,
    Project,
    Transcript,
)
from video_postproduction.render.ffmpeg import FFmpegRenderEngine
from video_postproduction.render.service import RenderService
from video_postproduction.storage.local import LocalFilesystemStorage
from video_postproduction.transcription.faster_whisper import FasterWhisperProvider


def _has_thai(text: str) -> bool:
    return any("\u0e00" <= character <= "\u0e7f" for character in text)


def test_real_faster_whisper_thai_pipeline(session, tmp_path: Path):
    if os.getenv("VIDEO_POSTPRODUCTION_REAL_TRANSCRIPTION") != "1":
        pytest.skip("set VIDEO_POSTPRODUCTION_REAL_TRANSCRIPTION=1 to run the local model smoke test")

    sample_value = os.getenv("VIDEO_POSTPRODUCTION_THAI_SAMPLE_VIDEO")
    if not sample_value:
        pytest.fail("VIDEO_POSTPRODUCTION_THAI_SAMPLE_VIDEO is required for the real model smoke test")
    sample = Path(sample_value).expanduser()
    if not sample.is_file():
        pytest.fail(f"Thai sample video does not exist: {sample}")

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.fail("FFmpeg and FFprobe are required for the real model smoke test")

    settings = Settings(
        storage_root=tmp_path / "storage",
        transcription_language="th",
        whisper_model=os.getenv("WHISPER_MODEL", "small"),
        whisper_device=os.getenv("WHISPER_DEVICE", "cpu"),
    )
    storage = LocalFilesystemStorage(settings.storage_root)
    metadata = FFprobeMetadataReader(ffprobe).read(sample)
    project = Project(name="real Thai transcription", status="created")
    session.add(project)
    session.flush()
    storage_key = f"projects/{project.id}/original/{sample.name}"
    with sample.open("rb") as source:
        stored = storage.put_atomic(storage_key, source, "video/mp4")
    with sample.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    asset = MediaAsset(
        project_id=project.id,
        storage_key=stored.storage_key,
        original_filename=sample.name,
        media_type="video/mp4",
        size_bytes=stored.size_bytes,
        sha256=digest,
        metadata_json=asdict(metadata),
        status="probed",
    )
    session.add(asset)
    session.flush()
    job = ProcessingJob(
        project_id=project.id,
        asset_id=asset.id,
        kind="ingest",
        status="queued",
        idempotency_key=f"real-thai:{asset.id}",
    )
    session.add(job)
    session.add(ProcessingStage(job_id=job.id, name="transcribe", status="queued"))
    session.commit()

    provider = FasterWhisperProvider(settings)
    runner = StageRunner(
        session,
        storage=storage,
        probe_reader=FFprobeMetadataReader(ffprobe),
        transcription_provider=provider,
        settings=settings,
        queue=InlineJobQueue(),
    )
    runner.run_transcribe(job.id)

    transcript = session.scalar(select(Transcript).where(Transcript.asset_id == asset.id))
    assert transcript is not None
    assert transcript.provider == "faster_whisper"
    assert transcript.language == "th"
    assert transcript.normalized_json["segments"]
    assert any(_has_thai(segment["text"]) for segment in transcript.normalized_json["segments"])

    edl = session.scalar(
        select(EDL).where(EDL.project_id == project.id, EDL.source_asset_id == asset.id)
    )
    assert edl is not None
    output = RenderService(
        session,
        storage=storage,
        engine=FFmpegRenderEngine(
            ffmpeg,
            probe_reader=FFprobeMetadataReader(ffprobe),
            timeout_seconds=180,
        ),
    ).render(project.id, edl.id)

    assert output.status == "succeeded"
    assert output.storage_key is not None
    assert storage.local_path(output.storage_key).is_file()
