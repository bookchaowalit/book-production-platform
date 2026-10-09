from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from video_postproduction.config import Settings
from video_postproduction.jobs.queue import InlineJobQueue
from video_postproduction.jobs.recovery import recover_stale_jobs
from video_postproduction.jobs.stages import StageRunner
from video_postproduction.media.models import MediaMetadata, VideoStreamMetadata
from video_postproduction.models import MediaAsset, ProcessingJob, ProcessingStage, Project
from video_postproduction.storage.local import LocalFilesystemStorage
from video_postproduction.transcription.base import TranscriptionProviderError
from video_postproduction.transcription.models import (
    NormalizedTranscript,
    TranscriptValidationError,
)


def media_metadata() -> MediaMetadata:
    return MediaMetadata(
        duration=2,
        format_name="mp4",
        size_bytes=10,
        bitrate=None,
        video=VideoStreamMetadata(codec_name="h264", width=320, height=240, fps=30),
    )


class FakeProbe:
    def read(self, path: Path) -> MediaMetadata:
        return media_metadata()


class FailingProvider:
    def transcribe(self, audio_path, metadata, language=None):
        raise TranscriptionProviderError("provider_down", "Provider unavailable")


class WorkingProvider:
    def transcribe(self, audio_path, metadata, language=None):
        return NormalizedTranscript(
            provider="test",
            model="deterministic",
            language="en",
            duration=metadata.duration,
            segments=[],
        )


class LanguageRecordingProvider:
    def __init__(self) -> None:
        self.languages: list[str | None] = []

    def transcribe(self, audio_path, metadata, language=None):
        self.languages.append(language)
        return NormalizedTranscript(
            provider="test",
            model="deterministic",
            language=language,
            duration=metadata.duration,
            segments=[],
        )


def make_asset(session, tmp_path: Path):
    project = Project(name="demo", status="created")
    session.add(project)
    session.flush()
    storage = LocalFilesystemStorage(tmp_path / "storage")
    storage.put_atomic("projects/demo/original/video.mp4", b"video", "video/mp4")
    asset = MediaAsset(
        project_id=project.id,
        storage_key="projects/demo/original/video.mp4",
        original_filename="video.mp4",
        media_type="video/mp4",
        size_bytes=5,
        sha256="b" * 64,
        metadata_json={},
        status="uploaded",
    )
    session.add(asset)
    session.flush()
    return project, asset, storage


def make_job(session, asset: MediaAsset) -> ProcessingJob:
    job = ProcessingJob(
        project_id=asset.project_id,
        asset_id=asset.id,
        kind="ingest",
        status="queued",
        idempotency_key=f"{asset.id}:ingest",
    )
    session.add(job)
    session.flush()
    session.add(ProcessingStage(job_id=job.id, name="probe", status="queued"))
    session.add(ProcessingStage(job_id=job.id, name="transcribe", status="queued"))
    session.commit()
    return job


def test_failed_stage_records_safe_error_and_can_retry(session, tmp_path):
    _, asset, storage = make_asset(session, tmp_path)
    job = make_job(session, asset)
    runner = StageRunner(
        session,
        storage=storage,
        probe_reader=FakeProbe(),
        transcription_provider=FailingProvider(),
        settings=Settings(max_stage_attempts=2),
        queue=InlineJobQueue(),
    )

    runner.run_transcribe(job.id)
    failed = session.query(ProcessingStage).filter_by(job_id=job.id, name="transcribe").one()
    assert failed.status == "failed"
    assert failed.error_code == "transcription_provider_error"
    assert failed.error_message == "Provider unavailable"

    retry_job = runner.retry_stage(job.id, "transcribe")
    assert retry_job.status == "queued"
    assert failed.status == "queued"


class InvalidTranscriptProvider:
    def transcribe(self, audio_path, metadata, language=None):
        raise TranscriptValidationError("word range is invalid")


def test_invalid_provider_transcript_reports_specific_safe_error(session, tmp_path, caplog):
    _, asset, storage = make_asset(session, tmp_path)
    job = make_job(session, asset)
    runner = StageRunner(
        session,
        storage=storage,
        probe_reader=FakeProbe(),
        transcription_provider=InvalidTranscriptProvider(),
        settings=Settings(max_stage_attempts=2),
        queue=InlineJobQueue(),
    )

    with caplog.at_level("WARNING"):
        runner.run_transcribe(job.id)

    failed = session.query(ProcessingStage).filter_by(job_id=job.id, name="transcribe").one()
    assert failed.status == "failed"
    assert failed.error_code == "transcript_invalid"
    assert failed.error_message == "Transcript from provider failed validation: word range is invalid"
    assert "word range is invalid" in caplog.text


def test_probe_success_persists_metadata_and_enqueues_transcribe(session, tmp_path):
    project, asset, storage = make_asset(session, tmp_path)
    job = make_job(session, asset)
    queue = InlineJobQueue()
    runner = StageRunner(
        session,
        storage=storage,
        probe_reader=FakeProbe(),
        transcription_provider=WorkingProvider(),
        settings=Settings(),
        queue=queue,
    )

    runner.run_probe(job.id)

    session.refresh(asset)
    session.refresh(job)
    assert asset.status == "probed"
    assert asset.metadata_json["duration"] == 2
    assert queue.calls[-1] == (job.id, "transcribe")
    assert session.get(Project, project.id).status == "created"


def test_transcription_passes_configured_language_to_provider(session, tmp_path):
    _, asset, storage = make_asset(session, tmp_path)
    asset.metadata_json = asdict(media_metadata())
    asset.status = "probed"
    session.commit()
    job = make_job(session, asset)
    provider = LanguageRecordingProvider()
    runner = StageRunner(
        session,
        storage=storage,
        probe_reader=FakeProbe(),
        transcription_provider=provider,
        settings=Settings(transcription_language="th"),
        queue=InlineJobQueue(),
    )

    runner.run_transcribe(job.id)

    assert provider.languages == ["th"]


def test_recover_stale_running_job(session, tmp_path):
    _, asset, _ = make_asset(session, tmp_path)
    job = ProcessingJob(
        project_id=asset.project_id,
        asset_id=asset.id,
        kind="ingest",
        status="running",
        idempotency_key=f"{asset.id}:stale",
        started_at=datetime.now(UTC) - timedelta(hours=1),
    )
    session.add(job)
    session.flush()
    stage = ProcessingStage(
        job_id=job.id,
        name="probe",
        status="running",
        attempt=1,
        started_at=datetime.now(UTC) - timedelta(hours=1),
    )
    session.add(stage)
    session.commit()

    recovered = recover_stale_jobs(session, Settings(job_lease_seconds=10))

    assert recovered == 1
    session.refresh(job)
    session.refresh(stage)
    assert job.status == "retry_queued"
    assert stage.status == "queued"
