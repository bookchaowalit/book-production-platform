from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from video_postproduction.api.dependencies import (
    get_job_queue,
    get_probe_reader,
    get_session,
    get_settings,
    get_storage,
    get_transcription_provider,
)
from video_postproduction.config import Settings
from video_postproduction.jobs.queue import InlineJobQueue
from video_postproduction.jobs.stages import StageRunner
from video_postproduction.main import app
from video_postproduction.media.ffprobe import FFprobeMetadataReader
from video_postproduction.media.models import MediaMetadata
from video_postproduction.storage.local import LocalFilesystemStorage
from video_postproduction.transcription.models import NormalizedTranscript, TranscriptSegment, Word


class DeterministicProvider:
    """Small provider double with real normalized word timestamps."""

    def transcribe(
        self,
        audio_path: str | Path,
        metadata: MediaMetadata,
        language: str | None = None,
    ) -> NormalizedTranscript:
        return NormalizedTranscript(
            provider="e2e-deterministic",
            model="fixture-v1",
            language=language or "en",
            duration=metadata.duration,
            segments=[
                TranscriptSegment(
                    id="segment-0001",
                    start=0.2,
                    end=min(1.5, metadata.duration),
                    text="hello solo empire",
                    words=[
                        Word(text="hello", start=0.2, end=0.55),
                        Word(text="solo", start=0.6, end=0.95),
                        Word(text="empire", start=1.0, end=min(1.4, metadata.duration)),
                    ],
                )
            ],
        )


@dataclass
class E2ERuntime:
    session: object
    settings: Settings
    storage: LocalFilesystemStorage
    queue: InlineJobQueue


@pytest.fixture
def e2e_runtime(session, tmp_path) -> E2ERuntime:
    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if ffprobe is None or ffmpeg is None:
        pytest.skip("FFmpeg and FFprobe are required for the local E2E harness")
    settings = Settings(
        storage_root=tmp_path / "storage",
        max_upload_bytes=32 * 1024 * 1024,
        ffprobe_executable=ffprobe,
        ffmpeg_executable=ffmpeg,
    )
    storage = LocalFilesystemStorage(settings.storage_root)
    queue = InlineJobQueue()
    runner = StageRunner(
        session,
        storage=storage,
        probe_reader=FFprobeMetadataReader(ffprobe),
        transcription_provider=DeterministicProvider(),
        settings=settings,
        queue=queue,
    )
    queue.runner = runner
    app.dependency_overrides.update(
        {
            get_session: lambda: session,
            get_settings: lambda: settings,
            get_storage: lambda: storage,
            get_probe_reader: lambda: FFprobeMetadataReader(ffprobe),
            get_job_queue: lambda: queue,
            get_transcription_provider: lambda: DeterministicProvider(),
        }
    )
    try:
        yield E2ERuntime(session=session, settings=settings, storage=storage, queue=queue)
    finally:
        app.dependency_overrides.clear()
